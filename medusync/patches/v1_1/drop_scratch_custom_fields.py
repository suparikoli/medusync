# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Pre-model-sync: demo sites once grew these Medusync Settings fields as
Custom Fields from ad-hoc scripts. They are standard fields of the Single
now, and a Custom Field with the same fieldname would make the schema sync
fail. Drop the Custom Field rows; a Single stores its values in `tabSingles`
by fieldname, so the configured values survive the swap."""

import frappe

SCRATCH_CUSTOM_FIELDS = (
	"Medusync Settings-inventory_source_warehouse",
	"Medusync Settings-pricing_selling_price_list",
)


def execute():
	for name in SCRATCH_CUSTOM_FIELDS:
		if frappe.db.exists("Custom Field", name):
			frappe.delete_doc("Custom Field", name, force=1, ignore_permissions=True)
	frappe.clear_cache(doctype="Medusync Settings")
