# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

import frappe


def after_install():
	"""Create the Single with safe defaults, switched OFF.

	Installing the app must never start moving data on its own — the
	operator sets the URL and secrets, then flips Enable Sync.

	Handler packs are not registered here: which packs a site loads is a
	per-site choice (`site_config.json` → `medusync_handler_packs`) and the
	registry builds itself lazily. See medusync/handlers/__init__.py.
	"""
	settings = frappe.get_single("Medusync Settings")
	settings.enabled = 0
	if not settings.inbound_path:
		settings.inbound_path = "/webhooks/erpnext-inbound"
	if not settings.request_timeout:
		settings.request_timeout = 15
	if not settings.max_attempts:
		settings.max_attempts = 3
	if not settings.log_retention_days:
		settings.log_retention_days = 180
	settings.verify_ssl = 1
	settings.use_background_jobs = 1
	settings.log_payloads = 1
	settings.flags.ignore_permissions = True
	settings.save()
	frappe.db.commit()
