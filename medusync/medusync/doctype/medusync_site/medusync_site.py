# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

import re

import frappe
from frappe.model.document import Document

SITE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class MedusyncSite(Document):
	def validate(self):
		self.normalise_site_id()
		self.normalise_url()
		self.clamp_numbers()
		self.reject_duplicate_rows()
		self.require_invoice_choices()

	def require_invoice_choices(self):
		"""The two choices that cannot run half-filled.

		Frappe shows these as mandatory in the form but does not enforce a
		conditional requirement on a server-side save, and a store that
		numbers invoices with no prefix would issue numbers that collide.
		"""
		if self.invoice_numbering == "The store numbers the invoice" and not (self.store_invoice_prefix or "").strip():
			frappe.throw(
				frappe._("Give the store an invoice prefix, or let ERPNext number the invoices."),
				frappe.MandatoryError,
			)
		if self.record_payments and not self.mode_of_payment:
			frappe.throw(
				frappe._("Pick the Mode of Payment store payments are booked under."),
				frappe.MandatoryError,
			)

	def reject_duplicate_rows(self):
		"""One warehouse, one rule; one price list, one rule.

		Both maps are collapsed into a lookup keyed by the warehouse or the
		price list, so a second row for the same one is not a conflict the
		code can see — it simply wins, quietly, and the store starts
		receiving stock at a location nobody chose.
		"""
		self._reject_duplicates("warehouses", "warehouse", "Warehouse")
		self._reject_duplicates("price_lists", "price_list", "Price List")

	def _reject_duplicates(self, fieldname, key, label):
		seen = set()
		for row in self.get(fieldname) or []:
			value = row.get(key)
			if not value:
				continue
			if value in seen:
				frappe.throw(
					frappe._("{0} {1} is listed twice for this store. Keep one row per {2}.").format(
						frappe._(label), frappe.bold(value), frappe._(label).lower()
					)
				)
			seen.add(value)

	def normalise_site_id(self):
		"""The site id travels in every envelope and names the site in logs,
		so keep it to a shape that is safe in a URL, a cache key and a
		filename."""
		self.site_id = (self.site_id or "").strip().lower()
		if not SITE_ID_PATTERN.match(self.site_id):
			frappe.throw(
				"Site ID must start with a letter or digit and use only "
				"lower-case letters, digits, dot, dash or underscore."
			)
		if not self.title:
			self.title = self.site_id

	def normalise_url(self):
		url = (self.medusa_url or "").strip().rstrip("/")
		if url and not url.startswith(("http://", "https://")):
			url = "https://" + url
		self.medusa_url = url
		path = (self.inbound_path or "/webhooks/erpnext-inbound").strip()
		if not path.startswith("/"):
			path = "/" + path
		self.inbound_path = path

	def clamp_numbers(self):
		if self.request_timeout:
			self.request_timeout = max(1, min(int(self.request_timeout), 120))

	def on_update(self):
		self.clear_site_cache()

	def on_trash(self):
		self.clear_site_cache()

	def clear_site_cache(self):
		from medusync import sites

		sites.clear_cache()
