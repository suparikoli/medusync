# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MedusyncExclusion(Document):
	"""One record kept out of the sync, for one store or for all of them.

	The central Don't Sync list. Adding an entry here updates the
	document's own selector, so the form and the list can never tell an
	operator two different stories.
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

	def on_update(self):
		from medusync import selection

		selection.apply_to_document(self)

	def on_trash(self):
		from medusync import selection

		selection.remove_from_document(self)
