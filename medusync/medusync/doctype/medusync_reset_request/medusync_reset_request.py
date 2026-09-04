# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class MedusyncResetRequest(Document):
	"""One attempt at a two-sided reset.

	The audit record, not the mechanism: everything that decides whether a
	reset may happen lives in `medusync.reset`, where it can be read in one
	place. This document exists so that afterwards there is something to
	point at saying who asked, when, whether both sides proved themselves,
	and what was actually done.
	"""

	pass
