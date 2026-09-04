# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Which documents are allowed to sync, decided in ERPNext.

ERPNext owns this decision. A mapping says a doctype *can* sync; this says
whether a particular document *may*, and to which stores. The choice lives
on the document itself so the person looking at an Item can see and change
it without leaving the form:

    one store connected     a single Check, "Sync with Medusa"
    several connected       a list of stores, "Medusa Sites"

The two are the same decision at different resolutions, and the field that
does not apply is hidden rather than removed, so switching from one store
to several never loses what was already chosen.

Everything excluded is also written to the central **Medusync Exclusion**
list, and an entry added there by hand updates the document. Two views of
one decision that must never disagree, because an operator cannot act on a
checkbox that says one thing while the list says another.

Defaults are the important part. Turning this feature on must not silently
stop a store that was syncing perfectly well, so a document nobody has
touched is allowed, and an empty store list falls back to the Check.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

SYNC_FIELD = "medusync_sync"
SITES_FIELD = "medusync_sites"
SITE_SELECTION_DOCTYPE = "Medusync Site Selection"
EXCLUSION_DOCTYPE = "Medusync Exclusion"

#: Set while we are writing a document from the exclusion side (or an
#: exclusion from the document side) so the two cannot chase each other.
_APPLYING = "medusync_selection_applying"


# ── What is under selection ──────────────────────────────────────────


def selection_doctypes() -> list[str]:
	"""DocTypes that carry the per-document selector.

	The catalogue doctype is always included — it is what the feature
	exists for — plus anything else the operator listed in Settings.
	"""
	from medusync import config

	try:
		settings = config.settings()
	except Exception:
		return []
	out = []
	catalogue = (settings.get("products_doctype") or "").strip()
	if catalogue:
		out.append(catalogue)
	for row in settings.get("selection_doctypes") or []:
		name = (row.document_type or "").strip()
		if row.enabled and name and name not in out:
			out.append(name)
	return [dt for dt in out if frappe.db.exists("DocType", dt)]


def is_under_selection(doctype: str) -> bool:
	return bool(doctype) and doctype in selection_doctypes()


# ── The rule ─────────────────────────────────────────────────────────


def is_allowed(doctype: str, name: str, site_id: str, doc=None) -> bool:
	"""May this document reach this store?

	`doc` is optional: the outbound hook already holds it, and re-reading
	per site would be a query per site on every save.
	"""
	if not is_under_selection(doctype):
		# The operator never put this doctype under selection, so the
		# mappings alone govern it — exactly as before the feature existed.
		return True
	if _is_excluded(doctype, name, site_id):
		return False

	chosen = _chosen_sites(doctype, name, doc)
	if chosen:
		return site_id in chosen
	# No store picked: the single Check answers for all of them.
	return _sync_flag(doctype, name, doc)


def sites_allowed(doctype: str, name: str, candidates, doc=None) -> list:
	"""Filter a list of site rows down to the ones this document may reach."""
	if not is_under_selection(doctype):
		return list(candidates)
	return [s for s in candidates if is_allowed(doctype, name, s["site_id"], doc=doc)]


def _sync_flag(doctype: str, name: str, doc=None) -> bool:
	if doc is not None and doc.get(SYNC_FIELD) is not None:
		return bool(doc.get(SYNC_FIELD))
	value = frappe.db.get_value(doctype, name, SYNC_FIELD)
	# A document written before the field existed has NULL, which means
	# "never decided" and must not read as "excluded".
	return True if value is None else bool(value)


def _chosen_sites(doctype: str, name: str, doc=None) -> set:
	if doc is not None and doc.get(SITES_FIELD) is not None:
		return {row.site for row in (doc.get(SITES_FIELD) or []) if row.site}
	try:
		return set(
			frappe.get_all(
				SITE_SELECTION_DOCTYPE,
				filters={"parenttype": doctype, "parent": name},
				pluck="site",
			)
		)
	except Exception:
		return set()


def site_filter(site: str | None):
	"""Filter that matches one store's rows, or the whole-document rows.

	A whole-document exclusion stores NULL, and `=` never matches NULL, so
	it has to be asked for by name.
	"""
	return ["is", "not set"] if not site else ["=", site]


def _is_excluded(doctype: str, name: str, site_id: str) -> bool:
	base = {"document_type": doctype, "document_name": name}
	try:
		# Two indexed lookups rather than one clever filter: an exclusion
		# with no site covers every store, and one naming this store
		# covers only it.
		if frappe.db.exists(EXCLUSION_DOCTYPE, {**base, "site": site_filter(None)}):
			return True
		return bool(site_id and frappe.db.exists(EXCLUSION_DOCTYPE, {**base, "site": site_id}))
	except Exception:
		return False


# ── Provisioning the selector ────────────────────────────────────────


def field_hidden(doctype: str, fieldname: str) -> bool:
	field = frappe.get_meta(doctype).get_field(fieldname)
	return bool(field and field.hidden)


def ensure_selector_fields() -> dict:
	"""Put the selector on every configured doctype and show the right one.

	Called when Settings is saved and from the install patch. Idempotent:
	`create_custom_fields(update=True)` edits the existing field rather
	than adding a second one.
	"""
	from medusync import sites

	multi = len(sites.all_sites()) > 1
	touched = []
	for doctype in selection_doctypes():
		create_custom_fields(
			{
				doctype: [
					{
						"fieldname": SYNC_FIELD,
						"label": "Sync with Medusa",
						"fieldtype": "Check",
						"default": "1",
						"insert_after": _last_field(doctype),
						"in_standard_filter": 1,
						"hidden": 1 if multi else 0,
						"description": "Uncheck to stop this document syncing. It is also recorded in Medusync Exclusion.",
					},
					{
						"fieldname": SITES_FIELD,
						"label": "Medusa Sites",
						"fieldtype": "Table MultiSelect",
						"options": SITE_SELECTION_DOCTYPE,
						"insert_after": SYNC_FIELD,
						"hidden": 0 if multi else 1,
						"description": "Stores this document syncs with. Leave empty to use the checkbox for every store.",
					},
				]
			},
			ignore_validate=True,
			update=True,
		)
		touched.append(doctype)
	frappe.clear_cache()
	return {"doctypes": touched, "multi_site": multi}


def _last_field(doctype: str) -> str:
	"""Where to hang the selector: after the last real field, so it lands
	at the end of the form rather than in the middle of someone's layout."""
	meta = frappe.get_meta(doctype)
	for df in reversed(meta.fields):
		if df.fieldtype not in ("Section Break", "Column Break", "Tab Break"):
			return df.fieldname
	return meta.fields[-1].fieldname if meta.fields else "name"


# ── Keeping the document and the list in step ────────────────────────


def exclude(doctype: str, name: str, site: str | None = None, reason: str | None = None, source: str = "Manual") -> str:
	"""Add (or keep) a Don't Sync entry, and update the document to match."""
	existing = frappe.db.exists(
		EXCLUSION_DOCTYPE,
		{"document_type": doctype, "document_name": name, "site": site_filter(site)},
	)
	if existing:
		return existing
	doc = frappe.new_doc(EXCLUSION_DOCTYPE)
	doc.update(
		{
			"document_type": doctype,
			"document_name": name,
			"site": site or None,
			"reason": reason,
			"source": source,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def apply_to_document(exclusion) -> None:
	"""An exclusion was added: make the document say so too.

	Without this the form would show a ticked box for a document the
	system refuses to sync, which is the kind of disagreement an operator
	cannot debug.
	"""
	if frappe.flags.get(_APPLYING):
		return
	doctype, name = exclusion.document_type, exclusion.document_name
	if not is_under_selection(doctype) or not frappe.db.exists(doctype, name):
		return
	frappe.flags[_APPLYING] = True
	try:
		if exclusion.site:
			rows = frappe.get_all(
				SITE_SELECTION_DOCTYPE,
				filters={"parenttype": doctype, "parent": name, "site": exclusion.site},
				pluck="name",
			)
			for row in rows:
				frappe.db.delete(SITE_SELECTION_DOCTYPE, {"name": row})
		else:
			frappe.db.set_value(doctype, name, SYNC_FIELD, 0, update_modified=False)
	finally:
		frappe.flags[_APPLYING] = False


def remove_from_document(exclusion) -> None:
	"""An exclusion was deleted: let the document sync again.

	Only the whole-document case is restored. A per-site row cannot be put
	back blindly: an empty list means "every store", so re-adding one site
	would silently narrow the document to that store alone.
	"""
	if frappe.flags.get(_APPLYING):
		return
	doctype, name = exclusion.document_type, exclusion.document_name
	if not is_under_selection(doctype) or not frappe.db.exists(doctype, name):
		return
	if exclusion.site:
		return
	frappe.flags[_APPLYING] = True
	try:
		frappe.db.set_value(doctype, name, SYNC_FIELD, 1, update_modified=False)
	finally:
		frappe.flags[_APPLYING] = False


def record_from_document(doc) -> None:
	"""A document was saved: bring the Don't Sync list in line with it.

	Only rows this checkbox created (`source = Unchecked`) are managed
	here. An entry an operator added by hand is theirs to remove.
	"""
	if frappe.flags.get(_APPLYING) or doc.flags.get(_APPLYING):
		return
	if not is_under_selection(doc.doctype):
		return

	from medusync import sites

	wanted: set = set()
	if not _sync_flag(doc.doctype, doc.name, doc):
		wanted.add("")  # every store
	else:
		chosen = _chosen_sites(doc.doctype, doc.name, doc)
		if chosen:
			for site in sites.all_sites():
				if site["site_id"] not in chosen:
					wanted.add(site["site_id"])

	existing = {
		row.site or "": row.name
		for row in frappe.get_all(
			EXCLUSION_DOCTYPE,
			filters={"document_type": doc.doctype, "document_name": doc.name, "source": "Unchecked"},
			fields=["name", "site"],
		)
	}

	frappe.flags[_APPLYING] = True
	try:
		for site_id in wanted - set(existing):
			row = frappe.new_doc(EXCLUSION_DOCTYPE)
			row.update(
				{
					"document_type": doc.doctype,
					"document_name": doc.name,
					"site": site_id or None,
					"source": "Unchecked",
					"reason": "Deselected on the document.",
				}
			)
			row.insert(ignore_permissions=True)
		for site_id in set(existing) - wanted:
			frappe.delete_doc(EXCLUSION_DOCTYPE, existing[site_id], force=1, ignore_permissions=True)
	finally:
		frappe.flags[_APPLYING] = False


def on_doc_event(doc, method=None) -> None:
	"""Wildcard hook helper — never raises, so a bookkeeping failure here
	cannot abort the user's save."""
	try:
		record_from_document(doc)
	except Exception:
		frappe.log_error(
			title="Medusync could not record a sync-selection change",
			message=frappe.get_traceback(),
		)
