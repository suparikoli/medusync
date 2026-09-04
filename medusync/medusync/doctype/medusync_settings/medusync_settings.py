# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MedusyncSettings(Document):
	def validate(self):
		self.normalise_connection()
		self.clamp_numbers()
		self.validate_selection()

		if self.enabled and not self.medusa_url and not frappe.db.count("Medusync Site"):
			frappe.throw("Add a Medusync Site before enabling sync.")

	def normalise_connection(self):
		# The connection fields are legacy: delivery reads the Medusync Site
		# record. They are kept so an install that predates sites still has
		# somewhere to fall back to, and are still tidied on save.
		if self.medusa_url:
			self.medusa_url = self.medusa_url.strip().rstrip("/")
			if not self.medusa_url.startswith(("http://", "https://")):
				frappe.throw("Medusa URL must start with http:// or https://")
		if not self.inbound_path:
			self.inbound_path = "/webhooks/erpnext-inbound"
		if not self.inbound_path.startswith("/"):
			self.inbound_path = "/" + self.inbound_path

	def clamp_numbers(self):
		self.request_timeout = max(1, min(120, int(self.request_timeout or 15)))
		self.max_attempts = max(1, min(10, int(self.max_attempts or 3)))
		if int(self.log_retention_days or 0) < 0:
			self.log_retention_days = 0

	def validate_selection(self):
		"""The catalogue doctype is required and cannot be listed twice."""
		if not self.products_doctype:
			self.products_doctype = "Item" if frappe.db.exists("DocType", "Item") else None
		seen = set()
		for row in self.selection_doctypes or []:
			name = (row.document_type or "").strip()
			if not name:
				continue
			if name in seen:
				frappe.throw(f"{name} is listed twice under Sync Selection.")
			seen.add(name)

	def on_update(self):
		"""Put the selector where it now belongs, and tell the stores.

		Both are done here rather than in a patch because an operator can
		change the catalogue doctype at any time, and the plugin's
		"link to an existing product" search would otherwise keep looking
		in the old one.
		"""
		from medusync import selection

		try:
			selection.ensure_selector_fields()
		except Exception:
			frappe.log_error(
				title="Medusync could not provision the sync selector",
				message=frappe.get_traceback(),
			)

		before = (self.get_doc_before_save() or {}).get("products_doctype") if self.get_doc_before_save() else None
		if before is not None and before == self.products_doctype:
			return
		if before is None and not self.has_value_changed("products_doctype"):
			return
		self.announce_catalogue()

	def announce_catalogue(self):
		"""Tell every store which doctype now holds the catalogue."""
		from medusync import config, outbound

		if not config.is_enabled():
			return
		try:
			outbound.emit(
				"medusync.settings.changed",
				{"products_doctype": self.products_doctype},
				ref=f"settings:{frappe.utils.now()}",
			)
		except Exception:
			frappe.log_error(
				title="Medusync could not announce the catalogue doctype",
				message=frappe.get_traceback(),
			)
