# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Send existing records to Medusa.

Hooks only fire on change, so a freshly configured mapping syncs
nothing until each record happens to be touched. Backfill closes that
gap:

    bench --site <site> execute medusync.backfill.run \\
        --kwargs "{'mapping': 'Customers to Medusa', 'limit': 100}"

Dry-run first — it reports what would be sent without sending it.
"""

import frappe

from medusync import config, outbound


@frappe.whitelist()
def run(mapping: str, limit: int = 0, filters: dict | None = None, dry_run: bool = False):
	"""Replay one mapping over existing documents.

	Returns a summary dict rather than printing, so it is equally usable
	from `bench execute`, a background job, or the API.
	"""
	frappe.only_for("System Manager")

	doc = frappe.get_doc(config.MAPPING_DOCTYPE, mapping)
	if doc.direction == "From Medusa":
		frappe.throw(f"Mapping '{mapping}' is inbound-only — there is nothing to send.")
	if not doc.enabled:
		frappe.throw(f"Mapping '{mapping}' is disabled.")

	# Prefer the mapping's own insert trigger so the emitted event name
	# matches what a live create would produce.
	events = doc.docevent_list()
	docevent = "after_insert" if "after_insert" in events else events[0]

	names = frappe.get_all(
		doc.document_type,
		filters=filters or {},
		pluck="name",
		limit=int(limit) or None,
		order_by="modified asc",
	)

	sent, skipped = 0, 0
	for name in names:
		record = frappe.get_doc(doc.document_type, name)
		if not outbound._condition_passes(doc, record):
			skipped += 1
			continue
		if dry_run:
			sent += 1
			continue
		outbound.dispatch(doc, record, docevent)
		sent += 1

	return {
		"mapping": mapping,
		"doctype": doc.document_type,
		"event": doc.resolved_event_name(docevent),
		"matched": len(names),
		"sent": sent,
		"skipped_by_condition": skipped,
		"dry_run": bool(dry_run),
	}
