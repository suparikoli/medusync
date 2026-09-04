# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Record which channel an order arrived through.

Once a web order and a phone order are both Sales Orders they are
indistinguishable, and the storefront then has no honest way to say
"placed online" or "placed by our sales team". Medusa knows its sales
channel; this is where it lands.

Existing orders are left blank on purpose. Nothing can reconstruct the
channel of an order placed before the field existed, and guessing would
put a claim in the storefront that no record supports — `source_of`
falls back to "medusa" or "erpnext" from the presence of a Medusa order
id, which is at least true.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	if not frappe.db.exists("DocType", "Sales Order"):
		return

	create_custom_fields(
		{
			"Sales Order": [
				{
					"fieldname": "medusa_order_source",
					"label": "Medusa Order Source",
					"fieldtype": "Data",
					"insert_after": "medusa_payment_reference",
					"no_copy": 1,
					"read_only": 1,
					"in_standard_filter": 1,
					"description": (
						"The sales channel this order came through, as the store reported it. "
						"Blank on an order raised in ERPNext."
					),
				}
			]
		},
		ignore_validate=True,
		update=True,
	)
	frappe.db.commit()
	print("medusync: Sales Order records the channel an order came through")
