# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MedusyncLink(Document):
	"""One ERPNext document known to one store as one Medusa record."""

	def validate(self):
		self.medusa_id = (self.medusa_id or "").strip()
		self.medusa_entity = (self.medusa_entity or "").strip().lower() or None
		clash = frappe.db.get_value(
			"Medusync Link",
			{
				"site": self.site,
				"document_type": self.document_type,
				"name": ["!=", self.name or ""],
				"medusa_id": self.medusa_id,
			},
			"document_name",
		)
		if clash and clash != self.document_name:
			frappe.throw(
				frappe._("{0} {1} is already linked to {2} at {3}.").format(
					self.document_type, clash, self.medusa_id, self.site
				)
			)


def on_doctype_update():
	# One Medusa record per ERPNext document per store, and the reverse:
	# a lookup in either direction must never find two answers.
	frappe.db.add_unique(
		"Medusync Link",
		["site", "document_type", "medusa_entity", "document_name"],
		constraint_name="unique_medusync_link_document",
	)
	frappe.db.add_index("Medusync Link", ["site", "document_type", "medusa_id"])
