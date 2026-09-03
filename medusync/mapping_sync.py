# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""A mapping is one configuration that lives in two systems.

Editing it in the Desk and editing it in the Medusa admin have to mean the
same thing, so each mapping carries a `mapping_uid` shared by both copies
and a `version` that increments on every save. A save here pushes
`mapping.upserted` to the connected sites; a save there arrives as the
same event.

Conflict rule: the higher version wins. On a tie ERPNext wins, because
ERPNext owns which documents are allowed to sync at all, and the two
decisions must not disagree.

Deleting is disabling. A mapping that vanished on one side leaves records
correlated by it on the other, so `mapping.deleted` switches the local
copy off and keeps it.
"""

import frappe

from medusync import config, envelope, sites

MAPPING_DOCTYPE = "Medusync Mapping"

#: ERPNext's Select values are written from this site's point of view
#: ("To Medusa" = data leaves here). The canonical form is written from
#: Medusa's ("push" = Medusa pushes to ERPNext), because the Medusa plugin
#: and its stored mappings already use those words.
_DIRECTION_TO_CANONICAL = {"Two-way": "both", "To Medusa": "pull", "From Medusa": "push"}
_DIRECTION_FROM_CANONICAL = {v: k for k, v in _DIRECTION_TO_CANONICAL.items()}

_FIELD_DIRECTION_TO_CANONICAL = dict(_DIRECTION_TO_CANONICAL)
_FIELD_DIRECTION_TO_CANONICAL["Don't Sync"] = "none"
_FIELD_DIRECTION_FROM_CANONICAL = {v: k for k, v in _FIELD_DIRECTION_TO_CANONICAL.items()}


def direction_to_canonical(value: str) -> str:
	return _DIRECTION_TO_CANONICAL.get(value, "both")


def direction_from_canonical(value: str) -> str:
	return _DIRECTION_FROM_CANONICAL.get(value, "Two-way")


def field_direction_to_canonical(value: str) -> str:
	return _FIELD_DIRECTION_TO_CANONICAL.get(value, "both")


def field_direction_from_canonical(value: str) -> str:
	return _FIELD_DIRECTION_FROM_CANONICAL.get(value, "Two-way")


# ── Canonical form ───────────────────────────────────────────────────


def to_canonical(doc) -> dict:
	"""The shape that travels on the wire, identical from either side."""
	return {
		"uid": doc.mapping_uid,
		"version": int(doc.version or 1),
		"name": doc.title,
		"enabled": bool(doc.enabled),
		"medusa_entity": doc.get("medusa_entity") or "",
		"doctype": doc.document_type,
		"direction": direction_to_canonical(doc.direction),
		"key_medusa_field": _key_medusa_field(doc),
		"key_erpnext_field": doc.key_field or "name",
		"source_of_truth": doc.get("source_of_truth") or "ERPNext",
		"site_id": doc.get("site") or None,
		"fields": [
			{
				"erpnext_field": row.frappe_field,
				"medusa_path": row.medusa_path or row.frappe_field,
				"direction": field_direction_to_canonical(row.direction),
			}
			for row in (doc.field_map or [])
		],
	}


def _key_medusa_field(doc) -> str:
	"""The Medusa-side half of the identity pair. When the field map names
	the key field, its medusa_path is authoritative; otherwise fall back to
	the Frappe fieldname."""
	key = doc.key_field or "name"
	for row in doc.field_map or []:
		if row.frappe_field == key and row.medusa_path:
			return row.medusa_path
	return key


def apply_canonical(canon: dict) -> dict:
	"""Apply a mapping that arrived from the other side.

	Returns {action, name, reason}. `action` is created / updated /
	skipped, never a raise — a rejected mapping is a normal outcome that
	the log records, not a failure of the request.
	"""
	uid = (canon or {}).get("uid")
	if not uid:
		return {"action": "skipped", "reason": "missing_uid", "name": None}
	incoming_version = int(canon.get("version") or 1)

	existing = frappe.db.get_value(MAPPING_DOCTYPE, {"mapping_uid": uid}, "name")
	if existing:
		local_version = int(frappe.db.get_value(MAPPING_DOCTYPE, existing, "version") or 1)
		if incoming_version < local_version:
			return {"action": "skipped", "reason": "stale_version", "name": existing}
		if incoming_version == local_version:
			# Same version, possibly different content: ERPNext owns the
			# tie so the two sides converge on one answer instead of
			# swapping edits forever.
			return {"action": "skipped", "reason": "tie_erpnext_wins", "name": existing}
		doc = frappe.get_doc(MAPPING_DOCTYPE, existing)
		action = "updated"
	else:
		doc = frappe.new_doc(MAPPING_DOCTYPE)
		doc.title = _free_title(canon.get("name") or f"Mapping {uid[:8]}", uid)
		doc.mapping_uid = uid
		action = "created"

	doc.enabled = 1 if canon.get("enabled", True) else 0
	doc.document_type = canon.get("doctype") or doc.document_type
	doc.direction = direction_from_canonical(canon.get("direction"))
	doc.key_field = canon.get("key_erpnext_field") or "name"
	if doc.meta.has_field("medusa_entity"):
		doc.medusa_entity = canon.get("medusa_entity") or doc.get("medusa_entity")
	if doc.meta.has_field("source_of_truth") and canon.get("source_of_truth"):
		doc.source_of_truth = canon["source_of_truth"]
	if canon.get("site_id") and frappe.db.exists(sites.SITE_DOCTYPE, canon["site_id"]):
		doc.site = canon["site_id"]

	doc.set("field_map", [])
	for row in canon.get("fields") or []:
		doc.append(
			"field_map",
			{
				"frappe_field": row.get("erpnext_field"),
				"medusa_path": row.get("medusa_path"),
				"direction": field_direction_from_canonical(row.get("direction")),
			},
		)

	# The version that arrived is the version we store: this save must not
	# look like a local edit, or the two sides would ratchet each other up.
	doc.version = incoming_version
	doc.flags.medusync_applying = True
	doc.save(ignore_permissions=True)
	return {"action": action, "name": doc.name, "reason": None}


def apply_deleted(uid: str) -> dict:
	"""A mapping removed on the other side is switched off here, not
	destroyed — records already correlated by it must stay traceable."""
	if not uid:
		return {"action": "skipped", "reason": "missing_uid", "name": None}
	name = frappe.db.get_value(MAPPING_DOCTYPE, {"mapping_uid": uid}, "name")
	if not name:
		return {"action": "skipped", "reason": "already_absent", "name": None}
	doc = frappe.get_doc(MAPPING_DOCTYPE, name)
	if not doc.enabled:
		return {"action": "skipped", "reason": "already_disabled", "name": name}
	doc.enabled = 0
	doc.flags.medusync_applying = True
	doc.save(ignore_permissions=True)
	return {"action": "disabled", "name": name, "reason": None}


def _free_title(preferred: str, uid: str) -> str:
	"""Medusync Mapping is named by its title. Keep the incoming label when
	it is free, and disambiguate rather than hijack someone else's row."""
	if not frappe.db.exists(MAPPING_DOCTYPE, preferred):
		return preferred
	owner_uid = frappe.db.get_value(MAPPING_DOCTYPE, preferred, "mapping_uid")
	if owner_uid == uid:
		return preferred
	return f"{preferred} ({uid[:8]})"


# ── Outbound: tell the other side ────────────────────────────────────


def push_mapping(doc, deleted: bool = False) -> None:
	"""Send this mapping's current state to every site it applies to."""
	from medusync import outbound

	if not config.is_enabled():
		return
	event = "mapping.deleted" if deleted else "mapping.upserted"
	canon = {"uid": doc.mapping_uid, "version": int(doc.version or 1)} if deleted else to_canonical(doc)
	targets = sites.sites_for_mapping(doc) or sites.all_sites()
	for site in targets:
		event_id = f"frappe:mapping:{doc.mapping_uid}:{doc.version}:{event}"
		log = outbound._create_log(
			direction="Outbound",
			status="Queued",
			event=event,
			event_id=f"{event_id}:{site['site_id']}",
			document_type=MAPPING_DOCTYPE,
			document_name=doc.name,
			site=site["site_id"],
			request_body={"mapping": canon},
		)
		outbound.send(
			log.name,
			event,
			f"{event_id}:{site['site_id']}",
			{"mapping": canon},
			site_id=site["site_id"],
			kind=envelope.KIND_MAPPING,
		)


def on_mapping_update(doc, method=None):
	"""Medusync Mapping on_update — refresh the hot-path cache, then tell
	the other side. A mapping we just applied FROM the other side is not
	pushed back."""
	config.clear_mapping_cache()
	if doc.flags.get("medusync_applying"):
		return
	try:
		push_mapping(doc)
	except Exception:
		frappe.log_error(title="Medusync could not push a mapping change", message=frappe.get_traceback())


def on_mapping_trash(doc, method=None):
	config.clear_mapping_cache()
	if doc.flags.get("medusync_applying"):
		return
	try:
		push_mapping(doc, deleted=True)
	except Exception:
		frappe.log_error(title="Medusync could not push a mapping deletion", message=frappe.get_traceback())
