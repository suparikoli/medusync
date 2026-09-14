# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MedusyncExclusion(Document):
	"""One record kept out of the sync, for one store or for all of them.

	The central Don't Sync list, for doctypes under Sync Selection in
	"unless excluded" mode. The form's Medusa sync button writes the same
	rows.
	"""

	def validate(self):
		self.site = (self.site or "").strip() or None
		self.reject_duplicates()

	def reject_duplicates(self):
		from medusync.selection import site_filter

		clash = frappe.db.exists(
			"Medusync Exclusion",
			{
				"document_type": self.document_type,
				"document_name": self.document_name,
				"site": site_filter(self.site),
				"name": ["!=", self.name or ""],
			},
		)
		if clash:
			frappe.throw(
				"{0} {1} is already excluded{2}.".format(
					self.document_type,
					self.document_name,
					f" for {self.site}" if self.site else "",
				)
			)
