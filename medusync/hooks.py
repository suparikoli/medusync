app_name = "medusync"
app_title = "Medusync"
app_publisher = "Mithtech Innovative Solutions PVT LTD"
app_description = "Two-way sync between a Frappe/ERPNext site and a Medusa v2 backend."
app_email = "manoj@polemarch.in"
app_license = "mit"

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
	"Medusync Mapping": {
		"on_update": "medusync.config.clear_mapping_cache",
		"on_trash": "medusync.config.clear_mapping_cache",
	},
}

scheduler_events = {
	"daily": [
		"medusync.tasks.prune_logs",
	],
}

# The receive endpoint authenticates by HMAC over the raw body, not by
# session, so it must be reachable without a logged-in user AND without
# a CSRF token.
ignore_csrf = 1
