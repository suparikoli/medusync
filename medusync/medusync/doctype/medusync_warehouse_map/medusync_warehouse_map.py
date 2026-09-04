# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class MedusyncWarehouseMap(Document):
	"""One ERPNext warehouse feeding one Medusa stock location.

	Rows are per store, so the same warehouse can appear on several
	stores under different location ids.
	"""

	pass
