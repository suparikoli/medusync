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

	def reject_duplicate_rows(self):
		"""One warehouse, one rule; one price list, one rule.

		Both maps are collapsed into a lookup keyed by the warehouse or the
		price list, so a second row for the same one is not a conflict the
		code can see — it simply wins, quietly, and the store starts
		receiving stock at a location nobody chose.
		"""
		self._reject_duplicates("warehouses", "warehouse", "Warehouse")
		self._reject_duplicates("price_lists", "price_list", "Price List")
		self.require_tier_codes()

	def require_tier_codes(self):
		"""A Tier Price needs a tier to be.

		Without the code the far side has nothing to apply the price to, and
		the mistake would surface as a quiet stream of skipped events rather
		than as the configuration error it is. Checked here rather than on
		the child row because Frappe does not run a child doctype's
		`validate` when the parent is saved.
		"""
		for row in self.get("price_lists") or []:
			if row.role == "Tier Price" and not (row.tier_code or "").strip():
				frappe.throw(
					frappe._(
						"Give the tier price on {0} a Medusa tier code, or set its role to Base Price."
					).format(frappe.bold(row.price_list or "this row"))
				)
			if row.role != "Tier Price":
				row.tier_code = None

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
