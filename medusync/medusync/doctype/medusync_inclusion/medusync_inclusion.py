# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MedusyncInclusion(Document):
	"""One record chosen to sync, for one store or for all of them.

	The opposite of Medusync Exclusion, for doctypes where nothing syncs
	until somebody picks it.
	"""

	def validate(self):
		from medusync.selection import site_filter

		self.site = (self.site or "").strip() or None
		clash = frappe.db.exists(
			"Medusync Inclusion",
			{
				"document_type": self.document_type,
				"document_name": self.document_name,
				"site": site_filter(self.site),
				"name": ["!=", self.name or ""],
			},
		)
		if clash:
			frappe.throw(
				frappe._("{0} {1} is already chosen{2}.").format(
					self.document_type, self.document_name, f" for {self.site}" if self.site else ""
				)
			)
