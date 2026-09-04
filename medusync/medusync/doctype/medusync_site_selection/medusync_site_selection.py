# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class MedusyncSiteSelection(Document):
	"""One row per Medusa store a document is allowed to sync with.

	Rendered as the checkbox list on the document form via the
	`medusync_sites` Table MultiSelect field.
	"""

	pass
