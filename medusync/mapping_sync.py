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

Deleting propagates. `mapping.deleted` removes the local copy, because one
configuration living in two systems should not survive in one of them —
a disabled twin is how a single sync came to look like two. What the
mapping correlated is not lost: the link fields on the synced documents
and the log rows both outlive it.
"""

import re

import frappe

from medusync import config, envelope, sites

MAPPING_DOCTYPE = "Medusync Mapping"

#: A mapping's identity is derived from what it pairs, so both systems
#: agree which mapping is which without asking each other.
PAIR_PREFIX = "pair:"


# ── Identity: a sync is its pair ─────────────────────────────────────


def scrub_doctype(doctype: str) -> str:
	"""`Sales Invoice (Return)` -> `sales_invoice_return`, the same on both sides."""
	return re.sub(r"[^a-z0-9]+", "_", (doctype or "").strip().lower()).strip("_")


def pair_uid(medusa_entity: str | None, doctype: str, site_id: str | None = None) -> str:
	"""One Medusa entity and one DocType, per store, is one mapping.

	The Medusa plugin derives the same string, so a mapping created on
	either side lands on the other as itself rather than as a twin.
	"""
	uid = f"{PAIR_PREFIX}{(medusa_entity or '').strip().lower()}:{scrub_doctype(doctype)}"
	return f"{uid}:{site_id}" if site_id else uid


def pair_uid_of(doc) -> str:
	return pair_uid(doc.get("medusa_entity"), doc.document_type, doc.get("site") or None)


def find_by_pair(
	medusa_entity: str | None, doctype: str, site_id: str | None = None, exclude: str | None = None
) -> str | None:
	"""The mapping that already keeps this pair in step, if there is one."""
	filters = {
		"document_type": doctype,
		"medusa_entity": medusa_entity if medusa_entity else ["is", "not set"],
		"site": site_id if site_id else ["is", "not set"],
	}
	if exclude:
		filters["name"] = ["!=", exclude]
	rows = frappe.get_all(MAPPING_DOCTYPE, filters=filters, pluck="name", limit=1)
	return rows[0] if rows else None


def merge_field_rows(base_rows, extra_rows) -> list[dict]:
	"""Union by Frappe field; the base wins a collision."""
	out = []
	seen = set()
	for row in list(base_rows or []) + list(extra_rows or []):
		field = row.get("frappe_field")
		if not field or field in seen:
			continue
		seen.add(field)
		out.append(
			{
				"frappe_field": field,
				"medusa_path": row.get("medusa_path"),
				"constant_value": row.get("constant_value"),
				"direction": row.get("direction") or "Two-way",
			}
		)
	return out


def consolidate_pairs() -> dict:
	"""Fold every mapping that shares a pair into one, and stamp the pair
	identity on all of them.

	The base is the copy with the higher version, then the one that is
	switched on, then the older row; the other's field pairs and trigger
	events are folded into it. The folded rows are removed without
	telling the store — the surviving mapping's push says everything the
	store needs, and the store folds its own twins the same way.
	"""
	groups: dict[str, list] = {}
	for row in frappe.get_all(
		MAPPING_DOCTYPE,
		fields=["name", "mapping_uid", "medusa_entity", "document_type", "site", "version", "enabled", "creation"],
	):
		groups.setdefault(pair_uid(row.medusa_entity, row.document_type, row.site or None), []).append(row)

	merged, stamped = [], []
	for pair, rows in groups.items():
		rows.sort(key=lambda r: (-int(r.version or 1), -int(r.enabled or 0), r.creation))
		keeper, twins = rows[0], rows[1:]
		doc = frappe.get_doc(MAPPING_DOCTYPE, keeper.name)
		for twin in twins:
			twin_doc = frappe.get_doc(MAPPING_DOCTYPE, twin.name)
			folded = merge_field_rows(doc.field_map, twin_doc.field_map)
			doc.set("field_map", [])
			for row in folded:
				doc.append("field_map", row)
			events = doc.docevent_list()
			for event in twin_doc.docevent_list():
				if event not in events:
					events.append(event)
			doc.docevents = "\n".join(events)
			frappe.flags.medusync_applying = True
			try:
				frappe.delete_doc(MAPPING_DOCTYPE, twin.name, force=1, ignore_permissions=True)
			finally:
				frappe.flags.medusync_applying = False
		if doc.mapping_uid != pair:
			# Twins are gone, so the identity is free to take.
			frappe.db.set_value(MAPPING_DOCTYPE, doc.name, "mapping_uid", pair, update_modified=False)
			doc.mapping_uid = pair
			stamped.append({"pair": pair, "name": doc.name})
		if twins:
			doc.flags.ignore_permissions = True
			doc.save(ignore_permissions=True)
			merged.append({"pair": pair, "kept": doc.name, "folded": [t.name for t in twins]})
	return {"merged": merged, "stamped": stamped}

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
		"fields": [_canonical_field(row) for row in (doc.field_map or [])],
		# Why it is off, when it is off and somebody here said why. The
		# other side shows it rather than a switch that went off by itself.
		**(
			{"attention": doc.get("attention"), "attention_detail": doc.get("attention_detail")}
			if not doc.enabled and doc.get("attention")
			else {}
		),
	}


def _canonical_field(row) -> dict:
	"""One field pair on the wire.

	A fixed-value row has no Medusa path at all, so the usual
	`medusa_path or frappe_field` fallback would hand the other side a path
	that does not exist there. It carries `constant` instead, and only when
	there is one — an ordinary pair stays the shape it has always been, so
	an older peer reads it unchanged.
	"""
	constant = row.get("constant_value")
	if constant:
		return {
			"erpnext_field": row.frappe_field,
			"medusa_path": "",
			"direction": field_direction_to_canonical(row.direction),
			"constant": constant,
		}
	return {
		"erpnext_field": row.frappe_field,
		"medusa_path": row.medusa_path or row.frappe_field,
		"direction": field_direction_to_canonical(row.direction),
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

	# A sync is its pair. The uid on the wire finds the copy the other side
	# meant; failing that, the pair does, so a mapping the store created
	# under an identity of its own lands on the one here for the same pair
	# rather than beside it. Either way what is stored is the pair's own
	# identity.
	pair = pair_uid(canon.get("medusa_entity"), canon.get("doctype"), canon.get("site_id") or None)
	existing = None
	by_uid = frappe.db.get_value(
		MAPPING_DOCTYPE, {"mapping_uid": uid}, ["name", "medusa_entity", "document_type", "site"], as_dict=True
	)
	if by_uid and pair_uid(by_uid.medusa_entity, by_uid.document_type, by_uid.site or None) == pair:
		existing = by_uid.name
	if not existing:
		existing = find_by_pair(canon.get("medusa_entity"), canon.get("doctype"), canon.get("site_id") or None)
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
		doc.title = _free_title(canon.get("name") or f"Mapping {pair}", pair)
		action = "created"
	doc.mapping_uid = pair

	wants_on = bool(canon.get("enabled", True))
	if action == "created":
		# A mapping we have never seen arrives switched OFF, whatever the
		# sender says. Turning on a rule nobody has reviewed here is exactly
		# what the brief forbids, and the copy is necessarily partial:
		# options that exist on only one side (Send All Fields here, the
		# Medusa event list there) have nothing to carry them. An operator
		# enables it once they have looked.
		doc.enabled = 0
	else:
		doc.enabled = 1 if wants_on else 0
	# A mapping switched off over there is off here too, and says why.
	# One switched on over there clears what we said, if our gate agrees.
	if not wants_on and canon.get("attention"):
		doc.attention = canon["attention"] if canon["attention"] in ("Mapping Required", "Field Missing") else "Mapping Required"
		doc.attention_detail = canon.get("attention_detail") or None
	elif wants_on:
		doc.attention = None
		doc.attention_detail = None
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
				"constant_value": row.get("constant"),
				"direction": field_direction_from_canonical(row.get("direction")),
			},
		)

	# The version that arrived is the version we store: this save must not
	# look like a local edit, or the two sides would ratchet each other up.
	doc.version = incoming_version
	doc.flags.medusync_applying = True
	doc.save(ignore_permissions=True)

	if wants_on and action == "updated" and not doc.enabled:
		# The other side switched it on and our gate kept it off. A mapping
		# runs only when both sides have it on, so say so: one version up,
		# and the other side turns it off too rather than believing it runs.
		doc.attention = doc.attention or "Mapping Required"
		doc.attention_detail = doc.attention_detail or frappe._(
			"The store switched this on, and it has not been rehearsed here. "
			"Use Test to rehearse it and switch it on."
		)
		doc.version = incoming_version + 1
		frappe.db.set_value(
			MAPPING_DOCTYPE,
			doc.name,
			{"attention": doc.attention, "attention_detail": doc.attention_detail, "version": doc.version},
			update_modified=False,
		)
		push_mapping(doc)
		return {"action": "declined", "name": doc.name, "reason": "not_rehearsed_here"}
	return {
		"action": action,
		"name": doc.name,
		"reason": "created_disabled" if action == "created" else None,
	}


def apply_deleted(uid: str) -> dict:
	"""A mapping removed on the other side is removed here too.

	It used to be switched off instead, on the reasoning that records
	correlated by it should stay traceable. They still are: the link fields
	on the synced documents (`medusa_product_id` on an Item) and the log
	rows both outlive the mapping. What the old behaviour actually produced
	was a disabled twin on one side for every mapping deleted on the other,
	which is how one configuration came to look like two.

	Medusync Log rows ARE dynamically linked to the mapping, so this deletes
	with `force`. That is deliberate: a log says what happened, and what
	happened does not stop being true when the rule is retired. The rows
	keep the mapping name as text, so the history stays readable.
	"""
	if not uid:
		return {"action": "skipped", "reason": "missing_uid", "name": None}
	name = frappe.db.get_value(MAPPING_DOCTYPE, {"mapping_uid": uid}, "name")
	if not name:
		return {"action": "skipped", "reason": "already_absent", "name": None}
	# A request-level flag, not a doc one: `delete_doc` loads its own copy of
	# the document, so a flag set on an instance here would not reach the
	# trash hook, and the deletion would be echoed straight back.
	frappe.flags.medusync_applying = True
	try:
		frappe.delete_doc(MAPPING_DOCTYPE, name, force=1, ignore_permissions=True)
	finally:
		frappe.flags.medusync_applying = False
	return {"action": "deleted", "name": name, "reason": None}


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


def push_mapping(doc, deleted: bool = False, only_site: str | None = None) -> None:
	"""Send this mapping's current state to every site it applies to."""
	from medusync import outbound

	if not config.is_enabled():
		return
	event = "mapping.deleted" if deleted else "mapping.upserted"
	canon = {"uid": doc.mapping_uid, "version": int(doc.version or 1)} if deleted else to_canonical(doc)
	targets = sites.sites_for_mapping(doc) or sites.all_sites()
	if only_site:
		targets = [site for site in targets if site["site_id"] == only_site]
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
	# A reset is rewriting these on both sides at once. If each side
	# pushed its copy the two would collide on version, and the conflict
	# rule would then pick a winner nobody asked for. The cache refresh
	# above still has to happen: the mappings really did change.
	if frappe.flags.get("medusync_reset"):
		return
	try:
		push_mapping(doc)
	except Exception:
		frappe.log_error(title="Medusync could not push a mapping change", message=frappe.get_traceback())


def on_mapping_trash(doc, method=None):
	config.clear_mapping_cache()
	if doc.flags.get("medusync_applying") or frappe.flags.get("medusync_applying"):
		return
	try:
		push_mapping(doc, deleted=True)
	except Exception:
		frappe.log_error(title="Medusync could not push a mapping deletion", message=frappe.get_traceback())


# ── The whole list, on demand ────────────────────────────────────────


def push_all(site_id: str | None = None) -> dict:
	"""Send every mapping to the connected stores.

	A mapping travels when it is saved, and nothing else ever moved the
	list: a store connected later, or one that lost a mapping, simply
	never heard of the rest. This is the missing step — cheap, idempotent
	(the receiver keeps its newer copies), and what "sync now" does.
	"""
	pushed = 0
	for name in frappe.get_all(MAPPING_DOCTYPE, pluck="name", order_by="modified asc"):
		doc = frappe.get_doc(MAPPING_DOCTYPE, name)
		if site_id and doc.get("site") and doc.site != site_id:
			continue
		push_mapping(doc, only_site=site_id)
		pushed += 1
	return {"pushed": pushed, "site_id": site_id}


@frappe.whitelist()
def sync_now(site_id: str | None = None) -> dict:
	"""The "Sync mappings with the store" button."""
	frappe.only_for("System Manager")
	return push_all(site_id or None)


def on_site_update(doc, method=None):
	"""Medusync Site on_update: a store that was just connected, or
	re-pointed, is told about every mapping rather than only the next one
	somebody happens to save."""
	if not doc.get("enabled") or not config.is_enabled():
		return
	try:
		push_all(doc.site_id)
	except Exception:
		frappe.log_error(title="Medusync could not push the mappings to a site", message=frappe.get_traceback())
