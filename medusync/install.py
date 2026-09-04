# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

import frappe

#: The patches that install schema rather than migrate data. Every one is
#: idempotent — `create_custom_fields(update=True)` behind a
#: `frappe.db.exists` guard — so running them at install and again on the
#: next migrate changes nothing the second time.
SCHEMA_PATCHES = (
	"medusync.patches.v1_1.install_reference_fields",
	"medusync.patches.v1_3.install_sync_selection",
	"medusync.patches.v1_4.install_order_source_field",
)


def install_schema():
	"""Create the custom fields the connector reads and writes.

	`frappe.installer.install_app` calls `set_all_patches_as_completed`
	before it calls `after_install`, so on a fresh site every patch is
	marked done without ever running. That is right for a patch that
	migrates data a new site does not have, and wrong for ours: these
	create `Customer.medusa_customer_id`, the Sales Order payment and
	channel fields, and the per-document sync selector. Without this a
	fresh install looks healthy and fails on the first push, on a field
	that was never created.
	"""
	for path in SCHEMA_PATCHES:
		frappe.get_attr(path + ".execute")()


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

	install_schema()
	frappe.db.commit()


def after_migrate():
	"""Runs on every `bench migrate`. Must never raise.

	Applying the shipped defaults and looking for drift are both things an
	operator wants done on upgrade and neither is worth failing a migrate
	over. A site that will not migrate because one mapping went stale is a
	site nobody upgrades.
	"""
	from medusync import defaults, drift

	try:
		if defaults.installed_version() < defaults.DEFAULTS_VERSION:
			result = defaults.apply_defaults(reason="migrate")
			touched = len(result["created"]) + len(result["applied"])
			if touched or result["flagged"]:
				print(
					"medusync: default mappings — %s new, %s updated, %s need a decision"
					% (len(result["created"]), len(result["applied"]), len(result["flagged"]))
				)
	except Exception:
		frappe.log_error(title="medusync could not apply default mappings", message=frappe.get_traceback())

	try:
		report = drift.run()
		if report["flagged"]:
			print(
				"medusync: %s mapping(s) name a field that no longer exists and have been "
				"switched off — see the Needs Attention column" % len(report["flagged"])
			)
	except Exception:
		frappe.log_error(title="medusync drift check failed on migrate", message=frappe.get_traceback())
