# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MedusyncPriceListMap(Document):
	"""One Price List's direction and meaning for one store."""

	def validate(self):
		# A tier with no code cannot be applied at the far end, and the
		# failure would surface as a stream of skipped events rather than
		# as the configuration mistake it is.
		if self.role == "Tier Price" and not (self.tier_code or "").strip():
			frappe.throw(
				frappe._("Give the tier price on {0} a Medusa tier code, or set its role to Base Price.").format(
					frappe.bold(self.price_list or "this row")
				)
			)
		if self.role != "Tier Price":
			self.tier_code = None
