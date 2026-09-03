# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Commerce handler pack for the Medusa wire.

Where the Polemarch pack maps Medusa products to `Security` (by ISIN)
and orders to `Security Sale`, this one is ordinary commerce:

  Product  -> Item             (with the mandatory item_group / stock_uom)
  Order    -> Sales Order      (customer link + child line items)
  Invoice  -> Sales Invoice
  Customer -> Customer

Everything here is opt-in per site through `site_config.json`:

    "medusync_handler_packs": ["risitex"]

`hooks.py` names none of these doctypes; it binds one wildcard handler
which asks the registry for the hooks the configured packs declare below.
"""

from medusync.handlers import register_handler

# Doctype-aware upsert used by the mapped push path
# (see medusync.handlers.get_mapped_upsert).
MAPPED_UPSERT = "medusync.handlers.risitex.mapped.upsert_via_mapping"

# Document events this pack acts on. The core binds nothing for these
# doctypes; a site without this pack runs none of it.
#
#   Stock Ledger Entry / Sales Order  -> stock level (actual - reserved - safety)
#   Delivery Note / Shipment / Sales Invoice -> the post-order reverse path
#   Item Price / Item / Customer      -> price, MOQ and customer group
OUTBOUND_HOOKS = {
	"Stock Ledger Entry": {
		"after_insert": "medusync.handlers.risitex.inventory.on_sle",
	},
	"Sales Order": {
		"on_submit": "medusync.handlers.risitex.inventory.on_sales_order",
		"on_cancel": "medusync.handlers.risitex.inventory.on_sales_order",
		"on_update_after_submit": "medusync.handlers.risitex.inventory.on_sales_order",
	},
	"Delivery Note": {
		"on_submit": "medusync.handlers.risitex.reverse.on_delivery_note",
		"on_cancel": "medusync.handlers.risitex.reverse.on_delivery_note",
	},
	"Shipment": {
		"on_submit": "medusync.handlers.risitex.reverse.on_shipment",
		"on_update_after_submit": "medusync.handlers.risitex.reverse.on_shipment",
		"on_cancel": "medusync.handlers.risitex.reverse.on_shipment",
	},
	"Sales Invoice": {
		"on_submit": "medusync.handlers.risitex.reverse.on_sales_invoice",
		"on_cancel": "medusync.handlers.risitex.reverse.on_sales_invoice",
	},
	"Item Price": {
		"after_insert": "medusync.handlers.risitex.pricing.on_item_price",
		"on_update": "medusync.handlers.risitex.pricing.on_item_price",
		"on_trash": "medusync.handlers.risitex.pricing.on_item_price",
	},
	"Item": {
		"on_update": "medusync.handlers.risitex.pricing.on_item",
	},
	"Customer": {
		"after_insert": "medusync.handlers.risitex.pricing.on_customer_group_link",
		"on_update": "medusync.handlers.risitex.pricing.on_customer_group_link",
	},
}


def register() -> None:
	"""Register this pack's inbound handlers. Idempotent — safe to re-run.

	One entry: the Medusa-initiated return request. Inbound order, product,
	customer and invoice upserts flow through the mapped push path
	(MAPPED_UPSERT above), not this registry; only the return-request
	method call needs a handler here, because there is no doctype to
	upsert — it calls create_pending_return instead.
	"""
	from medusync.handlers.risitex.reverse import handle_return_requested

	register_handler("order.return_requested", handle_return_requested)
