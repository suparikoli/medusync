# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Move the Medusa ids off the documents and into Medusync Link.

Earlier versions kept them in Custom Fields on Customer, Item, the sales
doctypes, Address and Price List, plus a sync selector on the catalogue.
The values are copied into links first, and the fields dropped only once
they are; a site that never had the fields has nothing to do.
"""

import frappe

from medusync import links

FIELDS = {
	"Customer": ["medusa_customer_id"],
	"Item": ["medusa_product_id", "medusa_variant_id"],
	"Sales Order": [
		"medusa_order_id",
		"medusa_display_id",
		"medusa_order_source",
		"medusa_payment_method",
		"medusa_payment_reference",
	],
	"Sales Invoice": ["medusa_order_id"],
	"Delivery Note": ["medusa_order_id"],
	"Address": ["medusa_address_id"],
	"Price List": ["medusa_customer_tier"],
}

SELECTOR_FIELDS = ("medusync_sync", "medusync_sites")


def execute():
	frappe.reload_doc("medusync", "doctype", "medusync_link")
	site = links.current_site()
	for doctype, fieldnames in FIELDS.items():
		present = [
			f
			for f in fieldnames
			if frappe.db.exists("Custom Field", {"dt": doctype, "fieldname": f})
			and frappe.db.has_column(doctype, f)
		]
		if not present:
			continue
		copyable = [f for f in present if links.is_link_key(f)]
		if copyable and site:
			for row in frappe.get_all(doctype, fields=["name", *copyable]):
				taken = {f: row.get(f) for f in copyable if row.get(f) not in (None, "")}
				if taken:
					links.remember_all(doctype, row.name, taken, site=site)
		for fieldname in present:
			frappe.delete_doc(
				"Custom Field", f"{doctype}-{fieldname}", ignore_permissions=True, force=True
			)
		frappe.clear_cache(doctype=doctype)

	for row in frappe.get_all(
		"Custom Field", filters={"fieldname": ["in", SELECTOR_FIELDS]}, fields=["name", "dt"]
	):
		frappe.delete_doc("Custom Field", row.name, ignore_permissions=True, force=True)
		frappe.clear_cache(doctype=row.dt)
