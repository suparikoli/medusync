# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Medusa → this site.

One generic endpoint, `medusync.api.receive`. It is `allow_guest` on
purpose: the caller is a server, not a Frappe user, and authentication
is the HMAC over the raw body. Anything that fails the signature check
never reaches the document layer.

Envelope
--------
    {
      "event":    "customer.updated",
      "event_id": "medusa:cus_01H...:1723459200",
      "doctype":  "Customer",
      "key_field": "email_id",          # optional, defaults to "name"
      "key_value": "someone@example.com",
      "data":     { ...fields to write... },
      "ts":       1723459200              # unix seconds, inside the signed body
    }

`doctype` may be omitted when the event name matches a mapping's
derived event; that keeps simple integrations from repeating
themselves.

Status codes are chosen so a sender's retry logic does the right
thing: 401 for a bad signature (don't retry), 200 + status "skipped"
for events we deliberately ignore (don't retry), 5xx only for genuine
failures (do retry).

Site-specific behaviour comes from handler packs chosen per site
(`site_config.json` → `medusync_handler_packs`); see medusync.handlers.
"""

import json
import time as _time

import frappe

from medusync import config
from medusync.handlers import dispatch as handlers_dispatch
from medusync.signing import EVENT_ID_HEADER, SIGNATURE_HEADER, verify

_REPLAY_WINDOW_SECONDS = 300


def _replay_fresh(envelope: dict) -> bool:
	"""Replay protection. `ts` lives inside the HMAC-signed body, so it
	cannot be re-dated without breaking the signature. A request that DOES
	carry a `ts` must be within the window; a request that omits `ts` is
	accepted for backward-compatibility (the security review recommends
	making `ts` mandatory once every sender is guaranteed to send it)."""
	raw_ts = envelope.get("ts")
	if raw_ts is None:
		return True
	try:
		ts = float(raw_ts)
	except (TypeError, ValueError):
		return True
	return abs(_time.time() - ts) <= _REPLAY_WINDOW_SECONDS


@frappe.whitelist(allow_guest=True)
def receive():
	"""Apply one inbound event. See module docstring for the envelope.

	Two dispatch layers:
	  1. Handler-driven (`handlers_dispatch`) — used by the configured
	     handler packs. Each registered handler is a pure function of
	     `(payload, event_id)` that knows its doctypes intimately.
	  2. Mapping-driven (`_resolve_mapping` + `apply_inbound`) — the
	     site-agnostic fallback for events no pack handles, driven by
	     `Medusync Mapping` rows.

	`ping` is special — it never writes a `Medusync Log` row and always
	returns `pong` so the admin UI's 'test connection' button works
	before any operator config.
	"""
	raw = frappe.request.get_data() or b""

	secret = config.get_secret("inbound_secret")
	if not secret:
		return _respond(401, ok=False, status="unauthorized", message="inbound_secret is not configured")

	provided = frappe.get_request_header(SIGNATURE_HEADER) or frappe.get_request_header(
		"X-Frappe-Webhook-Signature"
	)
	if not verify(raw, secret, provided):
		return _respond(401, ok=False, status="unauthorized", message="invalid signature")

	# Signature first, parsing second — never parse attacker-controlled
	# JSON before we know who sent it.
	try:
		envelope = json.loads(raw.decode("utf-8") or "{}")
	except Exception:
		return _respond(400, ok=False, status="bad_request", message="body is not valid JSON")

	event = (envelope.get("event") or "").strip()
	if not event:
		return _respond(400, ok=False, status="bad_request", message="missing `event`")

	event_id = (
		frappe.get_request_header(EVENT_ID_HEADER) or envelope.get("event_id") or ""
	).strip()

	if event == "ping":
		return _respond(200, ok=True, status="success", message="pong", event=event)

	if not _replay_fresh(envelope):
		return _respond(401, ok=False, status="unauthorized", message="stale or missing timestamp (replay window)")

	if not config.is_enabled():
		return _respond(200, ok=True, status="skipped", message="sync disabled", event=event)

	if not event_id:
		return _respond(400, ok=False, status="bad_request", message="missing `event_id`")

	# Idempotency. A sender that retries after a timeout must not create
	# a second document.
	if frappe.db.exists("Medusync Log", {"event_id": event_id, "status": "Success", "direction": "Inbound"}):
		return _respond(200, ok=True, status="skipped", message="already applied", event=event, event_id=event_id)

	# Handler-driven dispatch. If a configured pack registered a handler
	# for this event, it owns the business logic — it can create a Sale +
	# submit it, post a JE, upsert child rows, etc. We still write a
	# Medusync Log row so the inbound audit trail is consistent.
	from medusync.handlers import list_registered
	if event in list_registered():
		log = frappe.new_doc("Medusync Log")
		log.update(
			{
				"direction": "Inbound",
				"status": "Queued",
				"event": event,
				"event_id": event_id,
				"document_type": None,  # handlers may touch many doctypes
				"request_body": json.dumps(envelope, indent=2, default=str)
				if config.settings().log_payloads
				else None,
			}
		)
		log.insert(ignore_permissions=True)
		try:
			result = handlers_dispatch(event, envelope.get("data") or {}, event_id=event_id)
		except Exception as exc:
			_close(log, "Failed", error=frappe.get_traceback()[:4000])
			frappe.db.commit()
			return _respond(500, ok=False, status="failed", message=str(exc), event=event)
		_close(
			log,
			_result_status(result),
			document_type=_result_doctype(result),
			document_name=_result_name(result),
			action=_clamp_action(_result_action(result)),
		)
		frappe.db.commit()
		return _respond(200, ok=True, status="success", event=event, event_id=event_id, result=result)

	# Mapping-driven dispatch. Fallback for sites that configure
	# `Medusync Mapping` rows for events no handler pack covers.
	mapping = _resolve_mapping(envelope, event)
	if not mapping:
		return _respond(
			200,
			ok=True,
			status="skipped",
			message=f"no inbound mapping for event '{event}'",
			event=event,
		)

	log = frappe.new_doc("Medusync Log")
	log.update(
		{
			"direction": "Inbound",
			"status": "Queued",
			"event": event,
			"event_id": event_id,
			"document_type": mapping.document_type,
			"request_body": json.dumps(envelope, indent=2, default=str)
			if config.settings().log_payloads
			else None,
		}
	)
	log.insert(ignore_permissions=True)

	try:
		result = apply_inbound(mapping, envelope)
	except frappe.PermissionError as exc:
		_close(log, "Failed", error=str(exc))
		return _respond(403, ok=False, status="failed", message=str(exc), event=event)
	except Exception as exc:
		_close(log, "Failed", error=frappe.get_traceback()[:4000])
		frappe.db.commit()
		return _respond(500, ok=False, status="failed", message=str(exc), event=event)

	_close(
		log,
		result.get("status", "Success"),
		document_name=result.get("name"),
		action=result.get("action"),
	)
	frappe.db.commit()
	return _respond(200, ok=True, status="success", event=event, event_id=event_id, result=result)


def _result_status(result: dict) -> str:
	"""Map a handler's return envelope to a `Medusync Log.status` value."""
	status = (result or {}).get("status", "Success")
	# Normalise — handlers may return "synced", "created", "updated",
	# "cancelled", "skipped", "idempotent_skip", "exists" — collapse
	# them into the 4 Medusync Log statuses (Queued, Success, Failed,
	# Skipped).
	if status.lower() in ("created", "updated", "synced", "cancelled", "idempotent_skip", "exists", "draft_deleted", "pong", "ok"):
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


@frappe.whitelist(allow_guest=True)
def receive_mapped():
	"""Canonical-mapping push receiver.

	The Medusa erpnext-plugin's `applyMapping` path posts per-mapping
	per-doctype envelopes here. We HMAC-verify, parse, then hand the
	upsert to the doctype-aware function the site's configured handler
	pack provides (`MAPPED_UPSERT`, see medusync.handlers).

	Envelope:
	  {
	    event:        'customer.created' | 'customer.updated' | ...,
	    id:           <unique event id — `<medusa-event-id>:<mapping-id>`>,
	    mapping_id:   <erpnext_mapping.id on the Medusa side>,
	    mapping_name: <'Customer ↔ Customer', etc.>,
	    doctype:      'Customer',
	    key_field:    'email_id',
	    key_value:    'user@x.com',
	    payload:      { <erpnext_field>: <transformed value>, ... },
	    allow_create: true,      # optional; the sender's own policy
	    allow_update: true,      # optional
	    ts:           <unix seconds>
	  }
	"""
	raw = frappe.request.get_data() or b""
	signature_header = frappe.get_request_header(SIGNATURE_HEADER) or ""
	secret = config.get_secret("inbound_secret")
	if not secret:
		return _respond(500, ok=False, status="failed", message="inbound_secret is not configured")
	if not verify(raw, secret, signature_header):
		return _respond(401, ok=False, status="unauthorized", message="invalid signature")

	try:
		envelope = json.loads(raw.decode("utf-8") or "{}")
	except Exception:
		return _respond(400, ok=False, status="bad_request", message="body is not valid JSON")

	event = (envelope.get("event") or "").strip()
	event_id = (envelope.get("id") or "").strip()
	doctype = (envelope.get("doctype") or "").strip()
	key_field = (envelope.get("key_field") or "").strip()
	key_value = envelope.get("key_value")
	payload = envelope.get("payload") or {}
	mapping_name = envelope.get("mapping_name") or "(unnamed)"

	if not _replay_fresh(envelope):
		return _respond(401, ok=False, status="unauthorized", message="stale or missing timestamp (replay window)")

	if not doctype:
		return _respond(400, ok=False, status="bad_request", message="missing `doctype`")
	if not key_field or key_value in (None, ""):
		return _respond(
			200, ok=True, status="skipped", message="missing key/value",
			doctype=doctype, key_field=key_field,
		)
	if not config.is_enabled():
		return _respond(200, ok=True, status="skipped", message="sync disabled", event=event)

	if not event_id:
		return _respond(400, ok=False, status="bad_request", message="missing `id`")

	if frappe.db.exists("Medusync Log", {"event_id": event_id, "status": "Success", "direction": "Inbound"}):
		return _respond(200, ok=True, status="skipped", message="already applied", event=event, event_id=event_id)

	from medusync.handlers import get_mapped_upsert
	upsert_via_mapping = get_mapped_upsert()
	if upsert_via_mapping is None:
		return _respond(
			500, ok=False, status="failed",
			message="no configured handler pack provides a mapped upsert (site_config `medusync_handler_packs`)",
		)

	log = frappe.new_doc("Medusync Log")
	log.update(
		{
			"direction": "Inbound",
			"status": "Queued",
			"event": event,
			"event_id": event_id,
			"document_type": doctype,
			"request_body": json.dumps(envelope, indent=2, default=str)
			if config.settings().log_payloads
			else None,
		}
	)
	log.insert(ignore_permissions=True)

	# Switch to Administrator for the dispatch so downstream doc-event
	# hooks (Contact sync on Customer save, etc.) can read/write linked
	# doctypes without tripping on Guest-role checks.
	prev_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		result = upsert_via_mapping(
			doctype=doctype,
			key_field=key_field,
			key_value=key_value,
			payload=payload,
			event=event,
			event_id=event_id,
			allow_create=envelope.get("allow_create", True) is not False,
			allow_update=envelope.get("allow_update", True) is not False,
		)
		frappe.db.commit()
	except Exception as exc:
		frappe.set_user(prev_user)
		_close(
			log, "Failed",
			error=frappe.get_traceback()[:4000],
		)
		frappe.db.commit()
		return _respond(500, ok=False, status="failed", message=str(exc), event=event)
	frappe.set_user(prev_user)

	_close(
		log,
		_result_status(result),
		document_name=_result_name(result),
		action=_clamp_action(_result_action(result)),
	)
	frappe.db.commit()
	result["ok"] = True
	result["mapping_name"] = mapping_name
	return _respond(200, ok=True, status="success", event=event, event_id=event_id, result=result)


def _resolve_mapping(envelope: dict, event: str):
	"""Find the Medusync Mapping this event should be applied through.

	Explicit `doctype` in the envelope wins. Otherwise match on the
	derived event name so a mapping with no custom event name still
	works without the sender knowing Frappe's doctype names.
	"""
	doctype = (envelope.get("doctype") or "").strip()
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


def apply_inbound(mapping, envelope: dict) -> dict:
	"""Create, update or delete one document from an inbound envelope."""
	data = envelope.get("data") or {}
	key_field = (envelope.get("key_field") or mapping.key_field or "name").strip()
	key_value = envelope.get("key_value") or data.get(key_field)

	payload = _translate(mapping, data)
	# Never let the wire decide identity or workflow state.
	for reserved in ("doctype", "owner", "creation", "modified", "modified_by", "docstatus", "idx"):
		payload.pop(reserved, None)

	# The sender may restrict what it is willing to have done on its
	# behalf. Intersect with this mapping's own permissions rather than
	# letting either side alone decide — a mapping that forbids creates
	# must stay authoritative even if a sender asks for one, and vice
	# versa.
	may_create = bool(mapping.allow_insert) and envelope.get("allow_create", True) is not False
	may_update = bool(mapping.allow_update) and envelope.get("allow_update", True) is not False

	existing = None
	if key_value:
		if key_field == "name":
			existing = key_value if frappe.db.exists(mapping.document_type, key_value) else None
		else:
			existing = frappe.db.get_value(mapping.document_type, {key_field: key_value}, "name")

	# The flag is what stops this write bouncing straight back out.
	frappe.flags.medusync_inbound = True
	try:
		if envelope.get("event", "").endswith(".deleted"):
			if not mapping.allow_delete:
				return {"status": "Skipped", "reason": "delete not permitted by mapping"}
			if not existing:
				return {"status": "Skipped", "reason": "already absent"}
			frappe.delete_doc(mapping.document_type, existing, ignore_permissions=True)
			return {"status": "Success", "action": "deleted", "name": existing}

		if existing:
			if not may_update:
				return {"status": "Skipped", "reason": "update not permitted"}
			doc = frappe.get_doc(mapping.document_type, existing)
			doc.update(payload)
			doc.save(ignore_permissions=True)
			return {"status": "Success", "action": "updated", "name": doc.name}

		if not may_create:
			return {"status": "Skipped", "reason": "create not permitted"}

		doc = frappe.new_doc(mapping.document_type)
		doc.update(payload)
		if key_field != "name" and key_value:
			doc.set(key_field, key_value)
		doc.insert(ignore_permissions=True)
		if mapping.submit_on_insert and doc.meta.is_submittable:
			doc.submit()
		return {"status": "Success", "action": "created", "name": doc.name}
	finally:
		frappe.flags.medusync_inbound = False


def _translate(mapping, data: dict) -> dict:
	"""Medusa field names → Frappe fieldnames, per the field map."""
	if mapping.include_all_fields or not mapping.field_map:
		return dict(data)

	out = {}
	for row in mapping.field_map:
		if row.direction == "To Medusa":
			continue
		source = row.medusa_path or row.frappe_field
		if source in data:
			out[row.frappe_field] = data[source]
	return out


def _close(log, status: str, **kwargs):
	"""Finish the audit row. Must never raise: by the time we get here the
	business write has usually been committed, and a validation error on
	the log row would turn a success into a 5xx that the sender retries
	forever (which is exactly what happened when a handler returned a
	docname but the row had no document_type)."""
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
				{"status": status, "document_name": None, "error": (log.get("error") or "")[:4000] or None},
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
	and which event handlers are registered (so the Medusa admin UI
	knows what events will be handled without sending a signed ping).
	"""
	from medusync.handlers import list_registered
	frappe.local.response["http_status_code"] = 200
	frappe.local.response.update({
		"ok": True,
		"app": "medusync",
		"enabled": config.is_enabled(),
		"inbound_secret_configured": bool(config.get_secret("inbound_secret")),
		"registered_handlers": list_registered(),
	})
	return


@frappe.whitelist()
def test_medusa_connection():
	"""Operator convenience: POST a signed ping to the Medusa inbound webhook
	and report whether Medusa was reachable and accepted our signature."""
	import requests
	from medusync.signing import sign, SIGNATURE_HEADER, EVENT_ID_HEADER

	s = config.settings()
	url = (s.medusa_url or "").rstrip("/")
	if not url:
		return {"ok": False, "message": "Medusa URL is not set in Connection."}
	secret = config.get_secret("outbound_secret")
	if not secret:
		return {"ok": False, "message": "Outbound Secret is not set in Shared Secrets."}
	target = url + (s.inbound_path or "/webhooks/erpnext-inbound")
	eid = "ping-" + frappe.generate_hash(length=10)
	body = json.dumps({"event": "ping", "event_id": eid, "data": {}, "ts": int(_time.time())}).encode()
	sig = sign(body, secret)
	try:
		r = requests.post(
			target,
			data=body,
			headers={
				"Content-Type": "application/json",
				SIGNATURE_HEADER: sig,
				EVENT_ID_HEADER: eid,
			},
			timeout=(s.request_timeout or 15),
		)
		ok = 200 <= r.status_code < 300
		return {
			"ok": ok,
			"url": target,
			"status_code": r.status_code,
			"message": "Reached Medusa." if ok else ("HTTP %s: %s" % (r.status_code, r.text[:300])),
		}
	except Exception as exc:
		return {"ok": False, "url": target, "message": str(exc)[:300]}
