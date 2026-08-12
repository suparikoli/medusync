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
      "data":     { ...fields to write... }
    }

`doctype` may be omitted when the event name matches a mapping's
derived event; that keeps simple integrations from repeating
themselves.

Status codes are chosen so a sender's retry logic does the right
thing: 401 for a bad signature (don't retry), 200 + status "skipped"
for events we deliberately ignore (don't retry), 5xx only for genuine
failures (do retry).
"""

import json

import frappe

from medusync import config
from medusync.signing import EVENT_ID_HEADER, SIGNATURE_HEADER, verify


@frappe.whitelist(allow_guest=True)
def receive():
	"""Apply one inbound event. See module docstring for the envelope."""
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

	if not config.is_enabled():
		return _respond(200, ok=True, status="skipped", message="sync disabled", event=event)

	if not event_id:
		return _respond(400, ok=False, status="bad_request", message="missing `event_id`")

	# Idempotency. A sender that retries after a timeout must not create
	# a second document.
	if frappe.db.exists("Medusync Log", {"event_id": event_id, "status": "Success", "direction": "Inbound"}):
		return _respond(200, ok=True, status="skipped", message="already applied", event=event, event_id=event_id)

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
	log.status = status
	for key, value in kwargs.items():
		if value is not None:
			log.set(key, value)
	log.save(ignore_permissions=True)


def _respond(http_status: int, **body):
	frappe.local.response["http_status_code"] = http_status
	frappe.local.response.update(body)
	# Frappe wraps whitelisted return values in `message`; setting the
	# response dict directly keeps the envelope flat for the caller.
	return


@frappe.whitelist(allow_guest=True)
def health():
	"""Unauthenticated liveness probe — deliberately says nothing about
	configuration beyond whether the app is installed and switched on."""
	frappe.local.response["http_status_code"] = 200
	frappe.local.response.update({"ok": True, "app": "medusync", "enabled": config.is_enabled()})
	return
