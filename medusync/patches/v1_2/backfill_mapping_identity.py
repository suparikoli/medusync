# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Give existing mappings the id and version the two systems compare.

A mapping is one configuration living in two systems; `mapping_uid` pairs
the copies and `version` says whose is newer. Rows that predate that are
stamped here.

Written with direct column updates rather than doc.save() on purpose: a
save would bump the version it is trying to set, and would push every
existing mapping to Medusa as a change nobody made.
"""

import frappe


def execute():
	rows = frappe.get_all(
		"Medusync Mapping",
		filters=[["mapping_uid", "is", "not set"]],
		fields=["name"],
	)
	for row in rows:
		frappe.db.set_value(
			"Medusync Mapping",
			row.name,
			{"mapping_uid": frappe.generate_hash(length=32), "version": 1},
			update_modified=False,
		)
	# Anything that somehow has an id but no version starts at 1 too.
	frappe.db.sql(
		"update `tabMedusync Mapping` set version = 1 where ifnull(version, 0) < 1"
	)
	frappe.db.commit()
