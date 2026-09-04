# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""The wire contract, version 2.

One envelope shape carries every kind of traffic between the two systems,
so each side has a single receiver instead of one endpoint per payload
shape. What changed from v1:

    v (int)        2. Absent means a v1 sender, which still parses.
    origin         who sent it: system, site_id, and a correlation_id
                   that is carried unchanged through a causal chain.
    kind           what the body holds: "event", "mapped", or "mapping".
    echo_of        set when this message was caused by an inbound write
                   from the named system+site. The far side drops what it
                   recognises as its own, which is what breaks sync loops
                   once the round trip crosses a background worker and the
                   in-request `medusync_inbound` flag can no longer help.

v1 keys are still emitted (`id` alongside `event_id`) so a receiver that
has not been upgraded keeps working during a rolling deploy.
"""

import time
import uuid

ENVELOPE_VERSION = 2

#: This system's name in `origin.system`. The Medusa plugin sends "medusa".
SYSTEM = "erpnext"

#: A signed body carrying a `ts` outside this window is refused. `ts` sits
#: inside the signed body, so it cannot be re-dated without breaking the
#: signature.
REPLAY_WINDOW_SECONDS = 300

KIND_EVENT = "event"
KIND_MAPPED = "mapped"
KIND_MAPPING = "mapping"


def new_correlation_id() -> str:
	return uuid.uuid4().hex


def origin_ref(system: str, site_id: str) -> str:
	"""The `echo_of` form: `<system>:<site_id>`."""
	return f"{system}:{site_id}"


def build(
	event: str,
	event_id: str,
	*,
	site_id: str,
	kind: str = KIND_EVENT,
	correlation_id: str | None = None,
	echo_of: str | None = None,
	data=None,
	doctype: str | None = None,
	key_field: str | None = None,
	key_value=None,
	payload=None,
	mapping=None,
	mapping_id: str | None = None,
	mapping_name: str | None = None,
	allow_create: bool = True,
	allow_update: bool = True,
	ts: int | None = None,
	dry_run: bool = False,
) -> dict:
	"""Compose an outbound envelope. Only the keys the `kind` needs are set."""
	env = {
		"v": ENVELOPE_VERSION,
		"kind": kind,
		"event": event,
		"event_id": event_id,
		# v1 receivers read `id` on the mapped path and `event_id` on the
		# event path. Send both; the cost is a duplicated string.
		"id": event_id,
		"ts": int(ts if ts is not None else time.time()),
		"origin": {
			"system": SYSTEM,
			"site_id": site_id,
			"correlation_id": correlation_id or new_correlation_id(),
		},
	}
	if echo_of:
		env["origin"]["echo_of"] = echo_of
	if dry_run:
		# Only present when true, so a v1 receiver that ignores unknown
		# keys is unaffected and an ordinary envelope is unchanged.
		env["dry_run"] = True
	if kind == KIND_MAPPED:
		env.update(
			{
				"doctype": doctype,
				"key_field": key_field,
				"key_value": key_value,
				"payload": payload if payload is not None else {},
				"allow_create": bool(allow_create),
				"allow_update": bool(allow_update),
			}
		)
		if mapping_id:
			env["mapping_id"] = mapping_id
		if mapping_name:
			env["mapping_name"] = mapping_name
	elif kind == KIND_MAPPING:
		env["mapping"] = mapping or {}
	else:
		env["data"] = data if data is not None else {}
	return env


class Envelope:
	"""A parsed envelope. Reading a v1 body fills the same attributes, with
	`version` 1 and no origin."""

	__slots__ = (
		"version",
		"kind",
		"event",
		"event_id",
		"ts",
		"origin_system",
		"origin_site_id",
		"correlation_id",
		"echo_of",
		"dry_run",
		"data",
		"doctype",
		"key_field",
		"key_value",
		"payload",
		"mapping",
		"mapping_id",
		"mapping_name",
		"allow_create",
		"allow_update",
		"raw",
	)

	def __init__(self, **kw):
		for slot in self.__slots__:
			setattr(self, slot, kw.get(slot))

	@property
	def origin_ref(self) -> str | None:
		if not self.origin_system or not self.origin_site_id:
			return None
		return origin_ref(self.origin_system, self.origin_site_id)


def _infer_kind(raw: dict) -> str:
	"""A v1 body has no `kind`; its shape says which path it took."""
	if raw.get("mapping") is not None:
		return KIND_MAPPING
	if raw.get("doctype") and raw.get("payload") is not None:
		return KIND_MAPPED
	return KIND_EVENT


def parse(raw: dict) -> Envelope:
	raw = raw or {}
	origin = raw.get("origin") or {}
	if not isinstance(origin, dict):
		origin = {}
	version = raw.get("v")
	try:
		version = int(version)
	except (TypeError, ValueError):
		version = 1
	kind = raw.get("kind") or _infer_kind(raw)
	ts = raw.get("ts")
	try:
		ts = float(ts) if ts is not None else None
	except (TypeError, ValueError):
		ts = None
	return Envelope(
		version=version,
		kind=kind,
		event=(raw.get("event") or "").strip(),
		event_id=str(raw.get("event_id") or raw.get("id") or "").strip(),
		ts=ts,
		origin_system=origin.get("system"),
		origin_site_id=origin.get("site_id"),
		correlation_id=origin.get("correlation_id"),
		echo_of=origin.get("echo_of"),
		dry_run=bool(raw.get("dry_run")),
		data=raw.get("data") if raw.get("data") is not None else raw.get("doc"),
		doctype=(raw.get("doctype") or "").strip() or None,
		key_field=(raw.get("key_field") or "").strip() or None,
		key_value=raw.get("key_value"),
		payload=raw.get("payload"),
		mapping=raw.get("mapping"),
		mapping_id=raw.get("mapping_id"),
		mapping_name=raw.get("mapping_name"),
		# absent flags mean permitted, which is exactly how v1 behaved
		allow_create=raw.get("allow_create", True) is not False,
		allow_update=raw.get("allow_update", True) is not False,
		raw=raw,
	)


def is_fresh(env: Envelope, window: int = REPLAY_WINDOW_SECONDS) -> bool:
	"""Replay protection. A body with no `ts` is accepted for backward
	compatibility; one that carries a `ts` must be inside the window."""
	if env.ts is None:
		return True
	return abs(time.time() - env.ts) <= window


def is_echo(env: Envelope, our_site_ids) -> bool:
	"""True when this message is our own change coming back to us.

	Two ways that shows: the sender explicitly tagged it as caused by one
	of our sites (`echo_of`), or the envelope claims to originate from this
	system at one of our own sites.
	"""
	ours = set(our_site_ids or ())
	if env.echo_of:
		system, _, site_id = str(env.echo_of).partition(":")
		if system == SYSTEM and site_id in ours:
			return True
	if env.origin_system == SYSTEM and env.origin_site_id in ours:
		return True
	return False
