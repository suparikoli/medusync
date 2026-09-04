# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Move the single connection onto a Medusync Site record.

Before multi-site, the Medusa URL, path and both shared secrets lived on
the Single, which allowed exactly one store. An existing install has real
traffic flowing through those values, so this copies them onto a site
called `default` rather than asking the operator to re-enter them. The
Single's copies are left in place: they are the fallback the receiver
uses if this ever runs on a site where the record was deleted.

Idempotent, and a no-op on a fresh install with nothing configured.
"""

import frappe

SITE_ID = "default"


def execute():
	if frappe.db.count("Medusync Site"):
		return

	settings = frappe.get_single("Medusync Settings")
	if not settings.get("medusa_url"):
		# Nothing configured yet — a fresh install creates its site in the
		# Desk, with no legacy values to preserve.
		return

	site = frappe.new_doc("Medusync Site")
	site.site_id = SITE_ID
	site.title = "Default Medusa"
	site.enabled = 1 if settings.get("enabled") else 0
	site.medusa_url = settings.get("medusa_url")
	site.inbound_path = settings.get("inbound_path") or "/webhooks/erpnext-inbound"
	site.request_timeout = settings.get("request_timeout") or 15
	site.verify_ssl = 1 if settings.get("verify_ssl") else 0
	site.products_doctype = "Item" if frappe.db.exists("DocType", "Item") else None
	for field in ("inbound_secret", "outbound_secret"):
		value = settings.get_password(field, raise_exception=False)
		if value:
			site.set(field, value)
	site.insert(ignore_permissions=True)
	frappe.db.commit()
