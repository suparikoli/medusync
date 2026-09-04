# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Cross-system reference fields on standard ERPNext doctypes.

The mapped push keys on these (`medusa_customer_id`, `medusa_order_id`,
…) and the commerce handler pack reads the rest (`medusa_address_id`,
`Price List.medusa_customer_tier`, the Sales Order payment fields). They
used to be created by hand on each site; a rebuild lost them. Idempotent
(`update=True`) and skipped for doctypes the site does not have.

Only standard ERPNext doctypes. A client app's own doctypes are that
app's business, not this one's.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def _ref(fieldname, label, insert_after, unique=0, **extra):
	spec = {
		"fieldname": fieldname,
		"label": label,
		"fieldtype": "Data",
		"insert_after": insert_after,
		"no_copy": 1,
		"in_standard_filter": 1,
		"unique": unique,
	}
	spec.update(extra)
	return spec


FIELDS = {
	"Customer": [
		_ref("medusa_customer_id", "Medusa Customer ID", "customer_name", unique=1),
	],
	"Item": [
		_ref("medusa_product_id", "Medusa Product ID", "item_code"),
		_ref("medusa_variant_id", "Medusa Variant ID", "medusa_product_id", unique=1),
	],
	"Sales Order": [
		_ref("medusa_order_id", "Medusa Order ID", "customer", unique=1),
		_ref("medusa_display_id", "Medusa Display ID", "medusa_order_id", in_standard_filter=0),
		_ref("medusa_payment_method", "Medusa Payment Method", "medusa_display_id", in_standard_filter=0),
		_ref("medusa_payment_reference", "Medusa Payment Reference", "medusa_payment_method", in_standard_filter=0),
	],
	"Sales Invoice": [
		_ref("medusa_order_id", "Medusa Order ID", "customer"),
	],
	"Delivery Note": [
		_ref("medusa_order_id", "Medusa Order ID", "customer"),
	],
	"Address": [
		_ref("medusa_address_id", "Medusa Address ID", "address_title", unique=1, in_standard_filter=0),
	],
	"Price List": [
		{
			"fieldname": "medusa_customer_tier",
			"label": "Medusa Customer Tier",
			"fieldtype": "Data",
			"insert_after": "price_list_name",
			"description": "Medusa customer_tier code this price list feeds as B2B tier prices. Leave blank for none.",
		},
	],
}


def execute():
	for doctype, specs in FIELDS.items():
		if not frappe.db.exists("DocType", doctype):
			continue
		create_custom_fields({doctype: specs}, ignore_validate=True, update=True)
	frappe.clear_cache()
