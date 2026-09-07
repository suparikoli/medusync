# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""A sync is its pair.

Mappings used to carry a generated identity, and the defaults a
`default:` one, so the same pair could exist twice on one side and under
two different identities across the two. Every mapping now carries the
identity derived from what it pairs, and any twins for one pair are folded
into a single mapping (see mapping_sync.consolidate_pairs).
"""

import frappe

from medusync import mapping_sync


def execute():
	report = mapping_sync.consolidate_pairs()
	if report["merged"] or report["stamped"]:
		frappe.logger("medusync").info(f"pair identity: {report}")
