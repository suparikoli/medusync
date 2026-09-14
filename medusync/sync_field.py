# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""The one field Medusync puts on an ERPNext doctype: the sync tick.

Everything else Medusa needs — ids, order details — lives in Medusync
Link, off the document. This is the exception, and it earns it: a tick a
user can see next to `Is Shopify Item`, sort a list by, and set on fifty
rows at once through the ordinary Actions -> Edit. A dialog behind a
button can do none of those, because a list column and Frappe's bulk edit
both read real fields.

The field is a mirror, not the truth. `Medusync Inclusion` still decides
what syncs; writing the tick writes an inclusion row and reading it reads
one back, so the two can never drift into disagreeing. The column exists
so the Desk has something to render.

It lives in its own `Medusync` tab so nothing of ours sits among a
doctype's own fields.
"""

import frappe

from medusync import selection

SYNC_FIELD = "medusync_sync"
TAB_FIELD = "medusync_tab"


def doctypes() -> list[str]:
	"""Doctypes that carry the tick: those under selection, in chosen mode.

	"Every document unless excluded" needs no tick to say yes — everything
	is already yes — so the field would only invite someone to tick what
	is already true.
	"""
	return sorted(
		dt for dt, mode in selection.modes().items() if mode == selection.MODE_ONLY_CHOSEN
	)


def _field_specs(doctype: str) -> list[dict]:
	return [
		{
			"fieldname": TAB_FIELD,
			"label": "Medusync",
			"fieldtype": "Tab Break",
			"insert_after": _last_fieldname(doctype),
		},
		{
			"fieldname": SYNC_FIELD,
			"label": "Sync to Medusa",
			"fieldtype": "Check",
			"insert_after": TAB_FIELD,
			"description": (
				"Send this record to the connected store. Which stores is on the "
				"Medusa sync dialog; this tick covers every connected store."
			),
		},
	]


def _last_fieldname(doctype: str) -> str:
	meta = frappe.get_meta(doctype)
	fields = [f.fieldname for f in meta.fields if f.fieldname]
	return fields[-1] if fields else ""


def install(doctype: str) -> None:
	"""Put the tab and the tick on one doctype, if they are not there."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	if frappe.get_meta(doctype).get_field(SYNC_FIELD):
		return
	create_custom_fields({doctype: _field_specs(doctype)}, ignore_validate=True)


def remove(doctype: str) -> None:
	"""Take both back off. A doctype that leaves chosen-mode keeps no trace."""
	for fieldname in (SYNC_FIELD, TAB_FIELD):
		name = frappe.db.get_value(
			"Custom Field", {"dt": doctype, "fieldname": fieldname}, "name"
		)
		if name:
			frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)


def sync_fields(doc=None, method=None) -> dict:
	"""Install the tick where it belongs and remove it where it does not.

	Called after migrate and whenever Medusync Settings changes, so the
	field follows the selection mode rather than needing anyone to
	remember.
	"""
	wanted = set(doctypes())
	present = {
		row.dt
		for row in frappe.get_all("Custom Field", filters={"fieldname": SYNC_FIELD}, fields=["dt"])
	}
	for doctype in wanted - present:
		if frappe.db.exists("DocType", doctype):
			install(doctype)
	for doctype in present - wanted:
		remove(doctype)
	if wanted != present:
		frappe.clear_cache()
	return {"installed": sorted(wanted - present), "removed": sorted(present - wanted)}


# ── Keeping the tick and the inclusion row saying the same thing ─────


def read_into(doc, method=None) -> None:
	"""Show, on load, what Medusync Inclusion actually says.

	The column is only a mirror. Reading it from the inclusion rows means a
	choice made in the dialog, by the bulk action, or through the API shows
	on the form without anyone syncing the two by hand.
	"""
	if not doc.meta.get_field(SYNC_FIELD):
		return
	chosen = _is_chosen(doc.doctype, doc.name)
	if bool(doc.get(SYNC_FIELD)) != chosen:
		doc.set(SYNC_FIELD, 1 if chosen else 0)


def write_from(doc, method=None) -> None:
	"""Make the inclusion rows agree with the tick somebody just saved."""
	if not doc.meta.get_field(SYNC_FIELD) or doc.flags.get("medusync_writing_tick"):
		return
	wanted = bool(doc.get(SYNC_FIELD))
	if wanted == _is_chosen(doc.doctype, doc.name):
		return
	sites = [s["site_id"] for s in _all_site_ids()] if wanted else []
	doc.flags.medusync_writing_tick = True
	try:
		selection.set_document_selection(doc.doctype, [doc.name], sites)
	finally:
		doc.flags.medusync_writing_tick = False


def _is_chosen(doctype: str, name: str) -> bool:
	"""Whether any connected store takes this document."""
	return any(
		selection.is_allowed(doctype, name, site["site_id"]) for site in _all_site_ids()
	)


def _all_site_ids() -> list[dict]:
	from medusync import sites

	return sites.all_sites(enabled_only=False)
