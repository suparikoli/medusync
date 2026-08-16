# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Medusa-side Product → Frappe Security metadata sync.

Securities are Frappe-managed (the operator decides what's tradable
via the desk). Medusa derives its Product catalog FROM Frappe via
the pull cron. So this handler is intentionally narrow — it only
accepts a back-reference from Medusa once the Product has been
created on the Medusa side, stamping `medusa_product_id` on the
matching Security so cross-system lookups work.

Expected payload:
  {
    isin: <ISIN, matches Security.name>,
    medusa_product_id: <Medusa product id>,
    medusa_variant_id?: <default variant id>,
  }

Does NOT create Securities — those originate in Frappe. Returns
'security_not_found' if the ISIN doesn't match an existing Security;
operator should create it in Frappe first.
"""

from typing import Optional

import frappe


def handle_synced(data: dict, event_id: Optional[str] = None) -> dict:
	isin = (data.get("isin") or "").strip().upper()
	medusa_product_id = data.get("medusa_product_id") or ""
	if not isin:
		return {"status": "skipped", "reason": "no_isin"}
	if not frappe.db.exists("Security", isin):
		return {"status": "security_not_found", "isin": isin}
	if not medusa_product_id:
		return {"status": "skipped", "reason": "no_medusa_product_id"}

	# Only stamp the back-ref if Security has the Custom Field for it
	if frappe.db.has_column("Security", "medusa_product_id"):
		current = frappe.db.get_value("Security", isin, "medusa_product_id")
		if current == medusa_product_id:
			return {
				"status": "idempotent_skip",
				"security": isin,
				"medusa_product_id": medusa_product_id,
			}
		frappe.db.set_value(
			"Security", isin, "medusa_product_id", medusa_product_id,
			update_modified=False,
		)
		frappe.db.commit()
	return {
		"status": "synced",
		"security": isin,
		"medusa_product_id": medusa_product_id,
	}
