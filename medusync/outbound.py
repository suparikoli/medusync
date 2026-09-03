# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""This site → Medusa.

One wildcard `doc_events` hook covers every doctype (see hooks.py); the
per-doctype decision is made here, at runtime, from Medusync Mapping
rows. That is what lets an operator add a new synced doctype from the
Desk UI without an app release.

Loop prevention
---------------
An inbound write from Medusa lands as an ordinary document save, which
would fire this hook and push the change straight back — a permanent
ping-pong between the two systems. `frappe.flags.medusync_inbound` is
set for the duration of an inbound apply and checked here.

The flag is per-request, so it does NOT cover the case where Medusa's
write happens to coincide with a human editing the same doc in Desk.
That's fine: Medusa's own receiver dedupes on `event_id`, so the worst
case is one redundant round trip, not a loop.

Retries
-------
`frappe.enqueue` cannot delay a job, so a failed delivery is *parked*:
the log row keeps `status = Queued` and gets a `next_attempt_at`. The
once-a-minute sweep `medusync.tasks.retry_due` re-enqueues rows whose
time has come. Handler packs should send through `send()` so they get
the same queueing and retry behaviour as mapped events.
"""

import hashlib
import json
import time

import frappe
from frappe.utils import add_to_date, now_datetime

from medusync import config
from medusync.signing import EVENT_ID_HEADER, SIGNATURE_HEADER, sign

QUEUE = "short"


def on_doc_event(doc, method=None):
	"""Wildcard entry point — called for every doctype on every event.

	Must be cheap and must never raise: an exception here would abort
	the user's save.
	"""
	try:
		if frappe.flags.get("medusync_inbound"):
			return
		if doc.doctype in ("Medusync Log", "Medusync Mapping", "Medusync Settings"):
			return
		if not config.is_enabled():
			return

		candidates = config.mappings_for(doc.doctype)
		if not candidates:
			return

		for row in candidates:
			if row["direction"] == "From Medusa":
				continue
			_maybe_dispatch(row["name"], doc, method)
	except Exception:
		frappe.log_error(
			title="Medusync outbound hook failed",
			message=frappe.get_traceback(),
		)


def _maybe_dispatch(mapping_name: str, doc, method: str | None):
	mapping = frappe.get_cached_doc(config.MAPPING_DOCTYPE, mapping_name)
	events = mapping.docevent_list()
	if method not in events:
		return

	# Frappe runs `on_update` as part of `insert()`, so a mapping that
	# listens to BOTH after_insert and on_update emits two events for one
	# create — same state, twice, doubling traffic and relying on the
	# receiver being idempotent. Drop the redundant one.
	#
	# When on_update is the ONLY configured trigger this must still fire:
	# the operator has asked for "any change", and a create is a change.
	if method == "on_update" and doc.flags.get("in_insert") and "after_insert" in events:
		return

	if not _condition_passes(mapping, doc):
		return
	dispatch(mapping, doc, method)


def dispatch(mapping, doc, docevent: str):
	"""Queue one event for delivery.

	Split out from the hook path so backfill can replay a record without
	pretending a document event just happened — the caller has already
	decided this record qualifies.
	"""
	event = mapping.resolved_event_name(docevent)
	# `modified` is part of the key so a repeated save produces a new
	# event, but a retry of the SAME save does not apply twice on the
	# Medusa side.
	event_id = f"frappe:{doc.doctype}:{doc.name}:{doc.get('modified') or now_datetime()}"
	payload = build_payload(mapping, doc)

	# "Only send when something actually changed."
	#
	# Frappe fires on_update for any save, including ones that touched
	# nothing this mapping cares about. Hashing the PAYLOAD (not the
	# doc) is the point: an unrelated field changing must not count as a
	# change for a mapping that doesn't sync that field.
	payload_hash = hashlib.sha256(
		json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
	).hexdigest()

	if mapping.get("skip_unchanged") and frappe.db.exists(
		"Medusync Log",
		{
			"direction": "Outbound",
			"document_type": doc.doctype,
			"document_name": doc.name,
			"payload_hash": payload_hash,
			"status": "Success",
		},
	):
		return

	log = _create_log(
		direction="Outbound",
		status="Queued",
		event=event,
		event_id=event_id,
		document_type=doc.doctype,
		document_name=doc.name,
		payload_hash=payload_hash,
		request_body=payload,
	)
	send(log.name, event, event_id, payload)


def send(log_name: str, event_name: str, event_id: str, payload: dict):
	"""Hand one logged event to the delivery channel.

	Background by default (`Send in Background`), so a slow or down Medusa
	never blocks the user's save; inline only when the operator turned the
	queue off for debugging. Handler packs call this instead of `deliver`
	directly so they inherit the same behaviour.
	"""
	cfg = config.settings()
	if cfg.use_background_jobs:
		frappe.enqueue(
			"medusync.outbound.deliver",
			queue=QUEUE,
			log_name=log_name,
			event_name=event_name,
			event_id=event_id,
			payload=payload,
			attempt=1,
			enqueue_after_commit=True,
		)
	else:
		deliver(log_name, event_name, event_id, payload, attempt=1)


def _condition_passes(mapping, doc) -> bool:
	if not mapping.condition:
		return True
	try:
		return bool(
			frappe.safe_eval(
				mapping.condition.strip(),
				None,
				{"doc": doc.as_dict(), "frappe": frappe._dict(utils=frappe.utils)},
			)
		)
	except Exception:
		frappe.log_error(
			title=f"Medusync condition failed on {mapping.name}",
			message=frappe.get_traceback(),
		)
		# A broken condition must not silently sync everything.
		return False


def build_payload(mapping, doc) -> dict:
	"""The `data` object Medusa receives.

	Child tables are included as lists of plain dicts when `Send All
	Fields` is on; an explicit field map can also name a Table field and
	get the same treatment.
	"""
	source = doc.as_dict(convert_dates_to_str=True)

	if mapping.include_all_fields:
		data = {k: v for k, v in source.items() if not k.startswith("_")}
	else:
		data = {}
		for row in mapping.field_map:
			if row.direction == "From Medusa":
				continue
			target = row.medusa_path or row.frappe_field
			data[target] = source.get(row.frappe_field)

	# The key is always present regardless of the field map — Medusa
	# cannot correlate the record without it.
	key_field = mapping.key_field or "name"
	data.setdefault(key_field, source.get(key_field))
	data.setdefault("name", doc.name)
	data.setdefault("doctype", doc.doctype)
	return data


def deliver(log_name: str, event_name: str, event_id: str, payload: dict, attempt: int = 1):
	"""POST one event to Medusa and record the outcome.

	NB the parameter is `event_name`, not `event`. `frappe.enqueue`
	reserves `event` for its own scheduler-event argument and consumes
	it, so a kwarg by that name never reaches this function — the job
	then dies with "missing 1 required positional argument". Inline
	delivery hides the bug completely, which is how it survived a
	green test run.

	Runs on the background queue by default. A failed attempt parks the
	row for the retry sweep rather than looping in-process, so a long
	outage doesn't pin a worker.
	"""
	import requests

	cfg = config.settings()
	endpoint = config.medusa_endpoint()
	secret = config.get_secret("outbound_secret")

	if not endpoint or not secret:
		_finish_log(log_name, status="Failed", error="medusa_url or outbound_secret not configured", next_attempt_at=None)
		return

	envelope = {"event": event_name, "event_id": event_id, "data": payload, "ts": int(time.time())}
	body = json.dumps(envelope, separators=(",", ":"), default=str).encode("utf-8")

	try:
		response = requests.post(
			endpoint,
			data=body,
			headers={
				"Content-Type": "application/json",
				SIGNATURE_HEADER: sign(body, secret),
				EVENT_ID_HEADER: event_id,
			},
			timeout=cfg.request_timeout or 15,
			verify=bool(cfg.verify_ssl),
		)
	except Exception as exc:
		_retry_or_fail(log_name, event_name, event_id, payload, attempt, str(exc), status_code=0)
		return

	text = (response.text or "")[:4000]
	if 200 <= response.status_code < 300:
		# Medusa reports what it did with the record; store it so
		# "why is this missing over there" is answerable from the log
		# alone. Older receivers say nothing — fall back to "updated".
		action = "updated"
		try:
			body_json = json.loads(text or "{}")
			action = (
				body_json.get("result", {}).get("action")
				or body_json.get("action")
				or "updated"
			)
		except Exception:
			pass
		fields = dict(
			status="Success",
			status_code=response.status_code,
			response_body=text,
			attempt=attempt,
			action=str(action)[:40],
			next_attempt_at=None,
		)
		if not cfg.log_payloads:
			# A retry may have parked the payload on the row; the row is
			# terminal now, so honour "don't log bodies".
			fields["request_body"] = None
		_finish_log(log_name, **fields)
		return

	_retry_or_fail(
		log_name,
		event_name,
		event_id,
		payload,
		attempt,
		f"HTTP {response.status_code}: {text[:500]}",
		status_code=response.status_code,
	)


def retry_delay_seconds(attempt: int) -> int:
	"""Backoff before the next attempt: 30 s after the first failure,
	120 s after the second, 270 s after the third … — long enough to ride
	out a deploy, short enough that a blip clears within minutes."""
	return 30 * max(int(attempt or 1), 1) ** 2


def _retry_or_fail(log_name, event_name, event_id, payload, attempt, error, status_code=0):
	cfg = config.settings()
	max_attempts = cfg.max_attempts or 3

	if attempt >= max_attempts:
		fields = dict(status="Failed", status_code=status_code, error=error, attempt=attempt, next_attempt_at=None)
		if not cfg.log_payloads:
			fields["request_body"] = None
		_finish_log(log_name, **fields)
		return

	# Park the row; `medusync.tasks.retry_due` re-enqueues it once
	# `next_attempt_at` has passed. No immediate re-enqueue: frappe.enqueue
	# has no delay, and retrying within milliseconds burns every attempt
	# during a single outage.
	fields = dict(
		status="Queued",
		status_code=status_code,
		error=error,
		attempt=attempt,
		next_attempt_at=add_to_date(now_datetime(), seconds=retry_delay_seconds(attempt)),
	)
	if not cfg.log_payloads:
		# The sweep re-reads the payload from the row. Keep it until the
		# row is terminal, then it is cleared again (see deliver()).
		fields["request_body"] = json.dumps(payload, indent=2, default=str)
	_finish_log(log_name, **fields)


# ── Logging ──────────────────────────────────────────────────────────


def _create_log(**kwargs):
	cfg = config.settings()
	if not cfg.log_payloads:
		kwargs.pop("request_body", None)
	doc = frappe.new_doc("Medusync Log")
	for key, value in kwargs.items():
		if key in ("request_body",) and value is not None:
			value = json.dumps(value, indent=2, default=str)
		doc.set(key, value)
	doc.insert(ignore_permissions=True)
	# Deliberately NOT committing here. This runs inside a document save,
	# where Frappe refuses a commit outright ("Commit/rollback are
	# disabled during certain events") because it would break the
	# atomicity of the user's own save. Ordering is already guaranteed by
	# `enqueue_after_commit=True` on the job: the worker cannot start
	# until this transaction lands, log row included.
	return doc


def _finish_log(log_name: str, **kwargs):
	cfg = config.settings()
	if not cfg.log_payloads:
		kwargs.pop("response_body", None)
	try:
		frappe.db.set_value("Medusync Log", log_name, kwargs, update_modified=False)
		# No explicit commit: in the worker, Frappe's job runner commits
		# on success; inline, the surrounding request transaction does.
		# Committing here would hit the same doc-event guard as above.
	except Exception:
		frappe.log_error(title="Medusync could not update its log", message=frappe.get_traceback())
