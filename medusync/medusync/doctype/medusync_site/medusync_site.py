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
