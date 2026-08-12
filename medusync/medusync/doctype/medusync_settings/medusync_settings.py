# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MedusyncSettings(Document):
	def validate(self):
		if self.medusa_url:
			self.medusa_url = self.medusa_url.strip().rstrip("/")
			if not self.medusa_url.startswith(("http://", "https://")):
				frappe.throw("Medusa URL must start with http:// or https://")
		if not self.inbound_path:
			self.inbound_path = "/webhooks/erpnext-inbound"
		if not self.inbound_path.startswith("/"):
			self.inbound_path = "/" + self.inbound_path
		self.request_timeout = max(1, min(120, int(self.request_timeout or 15)))
		self.max_attempts = max(1, min(10, int(self.max_attempts or 3)))
		if int(self.log_retention_days or 0) < 0:
			self.log_retention_days = 0

		if self.enabled and not self.medusa_url:
			frappe.throw("Set the Medusa URL before enabling sync.")
