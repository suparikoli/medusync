app_name = "medusync"
app_title = "Medusync"
app_publisher = "Mithtech Innovative Solutions PVT LTD"
app_description = "Two-way sync between a Frappe/ERPNext site and a Medusa v2 backend."
app_email = "manoj@polemarch.in"
app_license = "mit"

# The handler packs (inventory, pricing, fulfilment, returns) reach into
# ERPNext's stock and selling modules; a Frappe-only site cannot host them.
required_apps = ["erpnext"]

after_install = "medusync.install.after_install"

# ── Outbound ─────────────────────────────────────────────────────────
# A wildcard hook rather than a per-doctype list. Which doctypes are
# actually synced is a runtime question answered from Medusync Mapping
# rows, so an operator can add one from the Desk UI without an app
# release. `on_doc_event` returns immediately when no mapping matches,
# and the lookup is served from the request cache.
doc_events = {
	"*": {
		"after_insert": "medusync.outbound.on_doc_event",
		"on_update": "medusync.outbound.on_doc_event",
		"on_submit": "medusync.outbound.on_doc_event",
		"on_cancel": "medusync.outbound.on_doc_event",
		"on_trash": "medusync.outbound.on_doc_event",
		"on_update_after_submit": "medusync.outbound.on_doc_event",
	},
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
	"Medusync Mapping": {
		"on_update": "medusync.config.clear_mapping_cache",
		"on_trash": "medusync.config.clear_mapping_cache",
	},
}

scheduler_events = {
	"cron": {
		# Failed deliveries wait for their backoff (Medusync Log.next_attempt_at);
		# this sweep re-enqueues the ones that are due.
		"* * * * *": [
			"medusync.tasks.retry_due",
		],
	},
	"daily": [
		"medusync.tasks.prune_logs",
	],
}

# The receive endpoint authenticates by HMAC over the raw body, not by
# session, so it must be reachable without a logged-in user AND without
# a CSRF token.
ignore_csrf = 1
