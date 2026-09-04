# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Move the catalogue DocType onto Settings and put the selector in place.

Which DocType holds the catalogue is a statement about this ERPNext, not
about any one store: several stores read the same catalogue. It briefly
lived on Medusync Site, so carry that value over before the field goes.

Then provision the per-document selector. Every existing document keeps
syncing: the Check defaults to 1 and a NULL reads as "never decided",
which the rule treats as allowed.
"""

import frappe


def execute():
	settings = frappe.get_single("Medusync Settings")

	if not settings.get("products_doctype"):
		carried = None
		if frappe.db.has_column("Medusync Site", "products_doctype"):
			carried = frappe.db.sql(
				"""select products_doctype from `tabMedusync Site`
				   where ifnull(products_doctype, '') != '' order by enabled desc limit 1"""
			)
			carried = carried[0][0] if carried else None
		settings.products_doctype = carried or ("Item" if frappe.db.exists("DocType", "Item") else None)
		settings.flags.ignore_permissions = True
		settings.flags.ignore_validate = True
		settings.save()
		frappe.db.commit()

	from medusync import selection

	result = selection.ensure_selector_fields()
	frappe.db.commit()
	print("medusync: sync selector on", ", ".join(result["doctypes"]) or "(nothing configured)")
