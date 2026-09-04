# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Medusa → this site.

One endpoint, `medusync.api.receive`, for every kind of traffic. It is
`allow_guest` on purpose: the caller is a server, not a Frappe user, and
authentication is the HMAC over the raw body. Anything that fails the
signature check never reaches the document layer.

Which site sent it is answered by the signature itself — each Medusync
Site has its own inbound secret — so a site cannot claim to be another by
setting a header.

The envelope says what the body holds (see medusync.envelope):

    kind "event"    a business event; a handler pack or a Medusync Mapping
                    applies it
    kind "mapped"   an already-mapped upsert: doctype, key, payload
    kind "mapping"  the mapping configuration itself, being synchronised

`receive_mapped` is kept as a thin alias so a Medusa plugin that has not
been upgraded yet keeps working through a rolling deploy.

Status codes are chosen so a sender's retry logic does the right thing:
401 for a bad signature (don't retry), 200 + status "skipped" for events
we deliberately ignore (don't retry), 5xx only for genuine failures (do
retry).
"""

import json
import time as _time
from dataclasses import dataclass, field

import frappe

from medusync import catalogue, config, echo, envelope, mapping_sync, reset, sites
from medusync.handlers import dispatch as handlers_dispatch
from medusync.signing import EVENT_ID_HEADER, SIGNATURE_HEADER, verify

#: Kept for backward compatibility with anything importing it.
_REPLAY_WINDOW_SECONDS = envelope.REPLAY_WINDOW_SECONDS


def _replay_fresh(raw_envelope: dict) -> bool:
	"""Replay protection. `ts` lives inside the HMAC-signed body, so it
	cannot be re-dated without breaking the signature. A request that DOES
	carry a `ts` must be within the window; a request that omits `ts` is
	accepted for backward-compatibility."""
	return envelope.is_fresh(envelope.parse(raw_envelope))


@frappe.whitelist(allow_guest=True)
def receive():
	"""Apply one inbound message. See the module docstring."""
	return _receive()


@frappe.whitelist(allow_guest=True)
def receive_mapped():
	"""Legacy alias for the mapped-push path.

	v1 senders post the mapped shape here and the event shape to
	`receive`. v2 puts `kind` in the envelope and both arrive at the same
	place; this endpoint stays so an un-upgraded plugin keeps working.
	"""
	return _receive(default_kind=envelope.KIND_MAPPED)


def _receive(default_kind: str | None = None):
	raw = frappe.request.get_data() or b""
	provided = frappe.get_request_header(SIGNATURE_HEADER) or frappe.get_request_header(
		"X-Frappe-Webhook-Signature"
	)

	site = sites.site_for_signature(raw, provided)
	if not site and not _legacy_secret_matches(raw, provided):
		return _respond(401, ok=False, status="unauthorized", message="invalid signature")
	if site and not site.get("enabled"):
		return _respond(
			200, ok=True, status="skipped", message=f"site '{site['site_id']}' is disabled"
		)

	# Signature first, parsing second — never parse attacker-controlled
	# JSON before we know who sent it.
	try:
		body = json.loads(raw.decode("utf-8") or "{}")
	except Exception:
		return _respond(400, ok=False, status="bad_request", message="body is not valid JSON")

	env = envelope.parse(body)
	if default_kind and not body.get("kind"):
		env.kind = default_kind
	site_id = site["site_id"] if site else (env.origin_site_id or "default")

	if not env.event:
		return _respond(400, ok=False, status="bad_request", message="missing `event`")

	header_event_id = (frappe.get_request_header(EVENT_ID_HEADER) or "").strip()
	event_id = header_event_id or env.event_id

	if env.event == "ping":
		return _respond(200, ok=True, status="success", message="pong", event=env.event, site=site_id)

	if not envelope.is_fresh(env):
		return _respond(
			401, ok=False, status="unauthorized", message="stale or missing timestamp (replay window)"
		)

	# Our own change coming home. Dropping it here is what stops a sync
	# loop once the round trip has crossed a background worker and the
	# in-request guard flag is long gone.
	if envelope.is_echo(env, sites.our_site_ids()):
		return _respond(
			200, ok=True, status="skipped", message="echo of our own change", event=env.event, site=site_id
		)

	if not config.is_enabled():
		return _respond(200, ok=True, status="skipped", message="sync disabled", event=env.event)

	if not event_id:
		return _respond(400, ok=False, status="bad_request", message="missing `event_id`")

	# Idempotency. A sender that retries after a timeout must not create
	# a second document.
	if frappe.db.exists(
		"Medusync Log", {"event_id": event_id, "status": "Success", "direction": "Inbound"}
	):
		return _respond(
			200, ok=True, status="skipped", message="already applied", event=env.event, event_id=event_id
		)

	# The other side proving it holds the secret this side generated. A
	# control message, not business data: it never reaches a mapping, and
	# its body is redacted before the audit row is written.
	if env.event == reset.VERIFY_EVENT:
		return _reset_verify(env, event_id, site_id)

	# A rehearsal from the other side. It has already passed the signature
	# check, the replay window and the echo test, so a green answer proves
	# everything except the write — and the write is the only part a dry
	# run must not do.
	if getattr(env, "dry_run", False):
		return _rehearse(env, event_id, site_id)

	if env.kind == envelope.KIND_MAPPING:
		return _apply_mapping(env, event_id, site_id)
	if env.kind == envelope.KIND_MAPPED:
		return _apply_mapped(env, event_id, site_id)
	return _apply_event(env, event_id, site_id)


def _legacy_secret_matches(raw: bytes, provided: str | None) -> bool:
	"""Fallback for a site that has not been migrated to a Medusync Site
	record yet: the Single's inbound secret still verifies."""
	secret = config.get_secret("inbound_secret")
	return bool(secret and provided and verify(raw, secret, provided))


def _origin_ref(env, site_id: str) -> str:
	return f"{env.origin_system or 'medusa'}:{site_id}"


def _new_log(
	env, event_id: str, site_id: str, doctype=None, is_test: bool = False, redact: bool = False
):
	"""Open the audit row for one inbound message.

	`redact` is not the same as "payload logging is off". A control message
	carrying a secret must never be written down whatever the setting says,
	and the row still has to exist, because an attempt on the reset
	endpoint is exactly the thing somebody would want to see afterwards.
	"""
	if redact:
		body = json.dumps({"redacted": True}, indent=2)
	elif config.settings().log_payloads:
		body = json.dumps(env.raw, indent=2, default=str)
	else:
		body = None
	log = frappe.new_doc("Medusync Log")
	log.update(
		{
			"direction": "Inbound",
			"status": "Queued",
			"event": env.event,
			"event_id": event_id,
			"is_test": 1 if is_test else 0,
			"document_type": doctype,
			"site": site_id if frappe.db.exists(sites.SITE_DOCTYPE, site_id) else None,
			"request_body": body,
		}
	)
	log.insert(ignore_permissions=True)
	return log


# ── Resets ───────────────────────────────────────────────────────────


def _reset_verify(env, event_id: str, site_id: str):
	"""The store is claiming to hold the secret we generated.

	Answers 200 either way. A refusal is a fact about the secret, not a
	transport failure, and telling the sender to retry would hand an
	attacker unlimited attempts inside the window.
	"""
	log = _new_log(env, event_id, site_id, doctype=None, redact=True)
	secret = (env.data or {}).get("secret") if isinstance(env.data, dict) else None
	try:
		result = reset.verify_local(secret)
	except Exception:
		# Deliberately not `frappe.get_traceback()`: a traceback here could
		# quote the argument.
		_close(log, "Failed", error="reset verification raised")
		frappe.db.commit()
		return _respond(
			200, ok=True, status="failed", event=env.event, result={"ok": False, "reason": "error"}
		)

	_close(log, "Success" if result.get("ok") else "Skipped", action="updated" if result.get("ok") else "skipped")
	frappe.db.commit()
	return _respond(
		200,
		ok=True,
		status="success" if result.get("ok") else "skipped",
		event=env.event,
		event_id=event_id,
		result=result,
	)


# ── Rehearsals ───────────────────────────────────────────────────────


def _rehearse(env, event_id: str, site_id: str):
	"""Decide what this envelope would do, report it, and write nothing.

	Answers 200 whatever it finds. A rehearsal that discovers the mapping
	would refuse the payload has done its job; telling the sender to retry
	would be wrong twice over.
	"""
	log = _new_log(env, event_id, site_id, doctype=env.doctype, is_test=True)
	try:
		if env.kind == envelope.KIND_MAPPED:
			verdict = catalogue.guard(env.doctype, env.key_field, env.key_value, env.event)
			existing = catalogue.find(env.doctype, env.key_field, env.key_value) if env.doctype else None
			result = {
				"kind": envelope.KIND_MAPPED,
				"doctype": env.doctype,
				"key_field": env.key_field,
				"key_value": env.key_value,
				"action": "skipped"
				if verdict.blocked
				else ("updated" if existing else "created"),
				"existing": existing,
				"reason": verdict.reason,
				"fields": sorted((env.payload or {}).keys()),
			}
		else:
			mapping = _resolve_mapping(env.raw, env.event)
			if not mapping:
				result = {
					"kind": envelope.KIND_EVENT,
					"action": "skipped",
					"reason": f"no inbound mapping for event '{env.event}'",
				}
			else:
				decision = plan_inbound(mapping, env.raw)
				result = {
					"kind": envelope.KIND_EVENT,
					"mapping": mapping.name,
					"doctype": decision.doctype,
					"key_field": decision.key_field,
					"key_value": decision.key_value,
					"action": decision.action,
					"existing": decision.existing,
					"reason": decision.reason,
					"fields": sorted(decision.payload.keys()),
				}
	except Exception as exc:
		_close(log, "Failed", error=frappe.get_traceback()[:4000])
		frappe.db.commit()
		return _respond(
			200,
			ok=True,
			status="dry_run",
			dry_run=True,
			event=env.event,
			event_id=event_id,
			result={"action": "error", "reason": str(exc)},
		)

	_close(log, "Skipped", action="skipped")
	frappe.db.commit()
	return _respond(
		200, ok=True, status="dry_run", dry_run=True, event=env.event, event_id=event_id, result=result
	)


# ── kind: mapping ────────────────────────────────────────────────────


def _apply_mapping(env, event_id: str, site_id: str):
	"""The mapping configuration itself changed on the other side."""
	log = _new_log(env, event_id, site_id, doctype=mapping_sync.MAPPING_DOCTYPE)
	try:
		if env.event.endswith(".deleted"):
			result = mapping_sync.apply_deleted((env.mapping or {}).get("uid"))
		else:
			result = mapping_sync.apply_canonical(env.mapping or {})
	except Exception as exc:
		_close(log, "Failed", error=frappe.get_traceback()[:4000])
		frappe.db.commit()
		return _respond(500, ok=False, status="failed", message=str(exc), event=env.event)
	_close(
		log,
		"Skipped" if result.get("action") == "skipped" else "Success",
		document_name=result.get("name"),
		action=_clamp_action(result.get("action")),
	)
	frappe.db.commit()
	return _respond(
		200, ok=True, status="success", event=env.event, event_id=event_id, result=result
	)


# ── kind: mapped ─────────────────────────────────────────────────────


def _apply_mapped(env, event_id: str, site_id: str):
	"""An already-mapped upsert: doctype, key field, key value, payload."""
	if not env.doctype:
		return _respond(400, ok=False, status="bad_request", message="missing `doctype`")
	if not env.key_field or env.key_value in (None, ""):
		return _respond(
			200,
			ok=True,
			status="skipped",
			message="missing key/value",
			doctype=env.doctype,
			key_field=env.key_field,
		)

	# ERPNext owns the catalogue. An update to a record that is already
	# here is held back unless an operator allowed it, and a delete never
	# destroys one — it unlinks. Refusing is a *skip*, not a failure: a
	# 5xx here would put the sender into a retry loop that can never
	# succeed.
	verdict = catalogue.guard(env.doctype, env.key_field, env.key_value, env.event)
	if verdict.blocked:
		log = _new_log(env, event_id, site_id, doctype=env.doctype)
		_close(log, "Skipped", document_name=verdict.document, action="skipped")
		frappe.db.commit()
		return _respond(
			200,
			ok=True,
			status="skipped",
			event=env.event,
			event_id=event_id,
			result=verdict.as_result(),
		)

	from medusync.handlers import get_mapped_upsert

	upsert_via_mapping = get_mapped_upsert()
	if upsert_via_mapping is None:
		return _respond(
			500,
			ok=False,
			status="failed",
			message="no configured handler pack provides a mapped upsert (site_config `medusync_handler_packs`)",
		)

	log = _new_log(env, event_id, site_id, doctype=env.doctype)

	# Switch to Administrator for the dispatch so downstream doc-event
	# hooks (Contact sync on Customer save, etc.) can read/write linked
	# doctypes without tripping on Guest-role checks.
	prev_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		with echo.inbound_context(
			correlation_id=env.correlation_id or envelope.new_correlation_id(),
			origin=_origin_ref(env, site_id),
		):
			result = upsert_via_mapping(
				doctype=env.doctype,
				key_field=env.key_field,
				key_value=env.key_value,
				payload=env.payload or {},
				event=env.event,
				event_id=event_id,
				allow_create=env.allow_create,
				allow_update=env.allow_update,
			)
			name = _result_name(result)
			if name:
				echo.mark_touched(env.doctype, name)
		frappe.db.commit()
	except Exception as exc:
		frappe.set_user(prev_user)
		_close(log, "Failed", error=frappe.get_traceback()[:4000])
		frappe.db.commit()
		return _respond(500, ok=False, status="failed", message=str(exc), event=env.event)
	frappe.set_user(prev_user)

	_close(
		log,
		_result_status(result),
		document_name=_result_name(result),
		action=_clamp_action(_result_action(result)),
	)
	frappe.db.commit()
	result["ok"] = True
	if env.mapping_name:
		result["mapping_name"] = env.mapping_name
	return _respond(200, ok=True, status="success", event=env.event, event_id=event_id, result=result)


# ── kind: event ──────────────────────────────────────────────────────


def _apply_event(env, event_id: str, site_id: str):
	"""A business event. A configured handler pack owns it, or a Medusync
	Mapping applies it generically."""
	from medusync.handlers import list_registered

	data = env.data or {}
	origin = _origin_ref(env, site_id)
	correlation_id = env.correlation_id or envelope.new_correlation_id()

	if env.event in list_registered():
		log = _new_log(env, event_id, site_id, doctype=None)
		try:
			with echo.inbound_context(correlation_id=correlation_id, origin=origin):
				result = handlers_dispatch(env.event, data, event_id=event_id)
				doctype, name = _result_doctype(result), _result_name(result)
				if doctype and name:
					echo.mark_touched(doctype, name)
		except Exception as exc:
			_close(log, "Failed", error=frappe.get_traceback()[:4000])
			frappe.db.commit()
			return _respond(500, ok=False, status="failed", message=str(exc), event=env.event)
		_close(
			log,
			_result_status(result),
			document_type=_result_doctype(result),
			document_name=_result_name(result),
			action=_clamp_action(_result_action(result)),
		)
		frappe.db.commit()
		return _respond(
			200, ok=True, status="success", event=env.event, event_id=event_id, result=result
		)

	mapping = _resolve_mapping(env.raw, env.event)
	if not mapping:
		return _respond(
			200,
			ok=True,
			status="skipped",
			message=f"no inbound mapping for event '{env.event}'",
			event=env.event,
		)

	log = _new_log(env, event_id, site_id, doctype=mapping.document_type)
	try:
		with echo.inbound_context(correlation_id=correlation_id, origin=origin):
			result = apply_inbound(mapping, env.raw)
			if result.get("name"):
				echo.mark_touched(mapping.document_type, result["name"])
	except frappe.PermissionError as exc:
		_close(log, "Failed", error=str(exc))
		return _respond(403, ok=False, status="failed", message=str(exc), event=env.event)
	except Exception as exc:
		_close(log, "Failed", error=frappe.get_traceback()[:4000])
		frappe.db.commit()
		return _respond(500, ok=False, status="failed", message=str(exc), event=env.event)

	_close(
		log,
		result.get("status", "Success"),
		document_name=result.get("name"),
		action=_clamp_action(result.get("action")),
	)
	frappe.db.commit()
	return _respond(200, ok=True, status="success", event=env.event, event_id=event_id, result=result)


# ── Result interpretation ────────────────────────────────────────────


def _result_status(result: dict) -> str:
	"""Map a handler's return envelope to a `Medusync Log.status` value."""
	status = (result or {}).get("status", "Success")
	# Normalise — handlers may return "synced", "created", "updated",
	# "cancelled", "skipped", "idempotent_skip", "exists" — collapse
	# them into the Medusync Log statuses.
	if status.lower() in (
		"created",
		"updated",
		"synced",
		"cancelled",
		"idempotent_skip",
		"exists",
		"draft_deleted",
		"pong",
		"ok",
	):
		return "Success"
	if status.lower() in ("skipped", "not_found", "already_cancelled", "no_handler_for_event"):
		return "Skipped"
	return "Success"


def _result_doctype(result) -> str | None:
	"""A handler may say which doctype it touched (`{"doctype": "Customer"}`).
	Only a real DocType name is accepted, so the audit row's Dynamic Link can
	never be asked to point at nothing."""
	if not result:
		return None
	dt = result.get("doctype")
	if not dt or not isinstance(dt, str):
		return None
	return dt if frappe.db.exists("DocType", dt) else None


def _result_name(result: dict) -> str | None:
	"""Pick the most useful docname out of a handler's return envelope."""
	if not result:
		return None
	for k in ("name", "customer", "deposit", "withdrawal", "sale", "security", "medusa_id"):
		v = result.get(k)
		if v:
			return str(v)
	return None


_ALLOWED_LOG_ACTIONS = {"", "created", "updated", "deleted", "skipped"}


def _clamp_action(action):
	"""Medusync Log.action is a Select of a fixed vocabulary; handlers may
	return richer verbs (disabled, cancelled, ...). Map anything outside the
	allowed set onto the closest valid value so a log write never 417s."""
	if action in _ALLOWED_LOG_ACTIONS:
		return action
	if action in ("disabled", "cancelled", "canceled"):
		return "updated"
	return "updated"


def _result_action(result: dict) -> str | None:
	if not result:
		return None
	for k in ("action", "status"):
		v = result.get(k)
		if v:
			return str(v)
	return None


# ── Mapping-driven apply ─────────────────────────────────────────────


def _resolve_mapping(envelope_raw: dict, event: str):
	"""Find the Medusync Mapping this event should be applied through.

	Explicit `doctype` in the envelope wins. Otherwise match on the
	derived event name so a mapping with no custom event name still
	works without the sender knowing Frappe's doctype names.
	"""
	doctype = (envelope_raw.get("doctype") or "").strip()
	filters = {"enabled": 1, "direction": ["in", ["Two-way", "From Medusa"]]}
	if doctype:
		filters["document_type"] = doctype

	for row in frappe.get_all(config.MAPPING_DOCTYPE, filters=filters, fields=["name"]):
		mapping = frappe.get_cached_doc(config.MAPPING_DOCTYPE, row.name)
		if doctype:
			return mapping
		if mapping.medusa_event == event:
			return mapping
		for docevent in mapping.docevent_list():
			if mapping.resolved_event_name(docevent) == event:
				return mapping
	return None


@dataclass
class InboundPlan:
	"""What an inbound envelope would do, before anything does it.

	Split out so the studio can rehearse the real rule instead of a copy
	of it. A dry run that reasons independently is worse than no dry run,
	because it is believed: the two would agree for a fortnight and then
	quietly stop agreeing.
	"""

	action: str  # created | updated | deleted | skipped
	doctype: str
	key_field: str
	key_value: object = None
	payload: dict = field(default_factory=dict)
	existing: str | None = None
	reason: str | None = None


def plan_inbound(mapping, envelope_raw: dict) -> InboundPlan:
	"""Decide what would happen. Reads the database; writes nothing."""
	data = envelope_raw.get("data") or {}
	key_field = (envelope_raw.get("key_field") or mapping.key_field or "name").strip()
	key_value = envelope_raw.get("key_value") or data.get(key_field)

	payload = _translate(mapping, data)
	# Never let the wire decide identity or workflow state.
	for reserved in ("doctype", "owner", "creation", "modified", "modified_by", "docstatus", "idx"):
		payload.pop(reserved, None)

	# The sender may restrict what it is willing to have done on its
	# behalf. Intersect with this mapping's own permissions rather than
	# letting either side alone decide — a mapping that forbids creates
	# must stay authoritative even if a sender asks for one, and vice
	# versa.
	may_create = bool(mapping.allow_insert) and envelope_raw.get("allow_create", True) is not False
	may_update = bool(mapping.allow_update) and envelope_raw.get("allow_update", True) is not False

	existing = None
	if key_value:
		if key_field == "name":
			existing = key_value if frappe.db.exists(mapping.document_type, key_value) else None
		else:
			existing = frappe.db.get_value(mapping.document_type, {key_field: key_value}, "name")

	def plan(action, existing_name=existing, reason=None):
		return InboundPlan(
			action=action,
			doctype=mapping.document_type,
			key_field=key_field,
			key_value=key_value,
			payload=payload,
			existing=existing_name,
			reason=reason,
		)

	# Same guard as the mapped path, one level down, because a generic
	# mapping can carry a delete straight into frappe.delete_doc.
	verdict = catalogue.guard(
		mapping.document_type, key_field, key_value, envelope_raw.get("event")
	)
	if verdict.blocked:
		return plan("skipped", verdict.document or existing, verdict.reason)

	if envelope_raw.get("event", "").endswith(".deleted"):
		if not mapping.allow_delete:
			return plan("skipped", reason="delete not permitted by mapping")
		if not existing:
			return plan("skipped", reason="already absent")
		return plan("deleted")

	if existing:
		if not may_update:
			return plan("skipped", reason="update not permitted")
		return plan("updated")

	if not may_create:
		return plan("skipped", reason="create not permitted")
	return plan("created", existing_name=None)


def apply_inbound(mapping, envelope_raw: dict) -> dict:
	"""Carry out what `plan_inbound` decided.

	Deliberately thin. Every question — may it, does it exist, what does
	the field map carry, does the catalogue guard allow it — is answered
	one function up, where the studio can ask the same question without
	the answer being acted on.
	"""
	decision = plan_inbound(mapping, envelope_raw)

	if decision.action == "skipped":
		return {"status": "Skipped", "reason": decision.reason, "name": decision.existing}

	if decision.action == "deleted":
		frappe.delete_doc(decision.doctype, decision.existing, ignore_permissions=True)
		return {"status": "Success", "action": "deleted", "name": decision.existing}

	if decision.action == "updated":
		doc = frappe.get_doc(decision.doctype, decision.existing)
		doc.update(decision.payload)
		doc.save(ignore_permissions=True)
		return {"status": "Success", "action": "updated", "name": doc.name}

	doc = frappe.new_doc(decision.doctype)
	doc.update(decision.payload)
	if decision.key_field != "name" and decision.key_value:
		doc.set(decision.key_field, decision.key_value)
	doc.insert(ignore_permissions=True)
	if mapping.submit_on_insert and doc.meta.is_submittable:
		doc.submit()
	return {"status": "Success", "action": "created", "name": doc.name}


#: Field-map directions whose data does NOT enter this site.
_NOT_INBOUND = ("To Medusa", "Don't Sync")


def _translate(mapping, data: dict) -> dict:
	"""Medusa field names → Frappe fieldnames, per the field map."""
	if mapping.include_all_fields or not mapping.field_map:
		return dict(data)

	out = {}
	for row in mapping.field_map:
		if row.direction in _NOT_INBOUND:
			continue
		source = row.medusa_path or row.frappe_field
		if source in data:
			out[row.frappe_field] = data[source]
	return out


def _close(log, status: str, **kwargs):
	"""Finish the audit row. Must never raise: by the time we get here the
	business write has usually been committed, and a validation error on
	the log row would turn a success into a 5xx that the sender retries
	forever."""
	log.status = status
	for key, value in kwargs.items():
		if value is not None:
			log.set(key, value)
	# `document_name` is a Dynamic Link on `document_type`; without the
	# type it cannot validate, so keep the name out rather than fail.
	if not log.get("document_type"):
		log.document_name = None
	try:
		log.save(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Medusync could not close its log row", message=frappe.get_traceback())
		try:
			frappe.db.set_value(
				"Medusync Log",
				log.name,
				{"status": status, "document_name": None},
				update_modified=False,
			)
		except Exception:
			pass


def _respond(http_status: int, **body):
	frappe.local.response["http_status_code"] = http_status
	frappe.local.response.update(body)
	# Frappe wraps whitelisted return values in `message`; setting the
	# response dict directly keeps the envelope flat for the caller.
	return


@frappe.whitelist(allow_guest=True)
def health():
	"""Unauthenticated liveness probe — deliberately says nothing about
	configuration beyond whether the app is installed and switched on,
	which event handlers are registered, and how many sites are wired."""
	from medusync.handlers import list_registered

	frappe.local.response["http_status_code"] = 200
	frappe.local.response.update(
		{
			"ok": True,
			"app": "medusync",
			"enabled": config.is_enabled(),
			"envelope_version": envelope.ENVELOPE_VERSION,
			"sites": len(sites.all_sites()),
			"registered_handlers": list_registered(),
		}
	)
	return


@frappe.whitelist()
def test_medusa_connection(site_id: str | None = None):
	"""Operator convenience: POST a signed ping to a site's inbound webhook
	and report whether it was reachable and accepted our signature."""
	import requests

	from medusync.signing import sign

	site = sites.get_site(site_id) if site_id else sites.default_site()
	if not site:
		return {"ok": False, "message": "No Medusync Site is configured."}
	target = sites.endpoint(site)
	if not target:
		return {"ok": False, "message": f"Site '{site['site_id']}' has no Medusa URL."}
	secret = sites.secret(site, "outbound_secret")
	if not secret:
		return {"ok": False, "message": f"Site '{site['site_id']}' has no Outbound Secret."}

	eid = "ping-" + frappe.generate_hash(length=10)
	env = envelope.build("ping", eid, site_id=site["site_id"], data={})
	body = json.dumps(env, separators=(",", ":"), default=str).encode("utf-8")
	try:
		r = requests.post(
			target,
			data=body,
			headers={
				"Content-Type": "application/json",
				SIGNATURE_HEADER: sign(body, secret),
				EVENT_ID_HEADER: eid,
			},
			timeout=sites.timeout(site),
			verify=sites.verify_ssl(site),
		)
		ok = 200 <= r.status_code < 300
		return {
			"ok": ok,
			"site": site["site_id"],
			"url": target,
			"status_code": r.status_code,
			"message": "Reached Medusa." if ok else ("HTTP %s: %s" % (r.status_code, r.text[:300])),
		}
	except Exception as exc:
		return {"ok": False, "site": site["site_id"], "url": target, "message": str(exc)[:300]}
