# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class MedusyncPriceListMap(Document):
	"""One Price List's direction and meaning for one store.

	No validation here on purpose. Frappe does not run a child doctype's
	`validate` when the parent is saved, so a rule written here would
	simply never fire — which is worse than not having it, because the
	next person reads it and believes it. The rules for these rows live on
	`Medusync Site.validate`.
	"""

	pass
