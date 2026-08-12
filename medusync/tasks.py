# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import add_days, now_datetime

from medusync import config


def prune_logs():
	"""Delete Medusync Log rows past the configured retention.

	Sync logs contain whatever fields the mapping carries, which on a
	Customer mapping means personal data. Keeping them forever turns the
	log table into a second, unmanaged copy of it.
	"""
	try:
		days = int(config.settings().log_retention_days or 0)
	except Exception:
		return
	if days <= 0:
		return

	cutoff = add_days(now_datetime(), -days)
	frappe.db.delete("Medusync Log", {"creation": ("<", cutoff)})
	frappe.db.commit()
