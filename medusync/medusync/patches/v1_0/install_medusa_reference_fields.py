"""v1_0 — install the 5 Medusa reference Custom Fields.

medusync owns these fields, not the apps that declare the doctypes.
The fields sit on Polemarch doctypes (Security, Security Sale, Wallet
Deposit, Wallet Withdrawal) but the patch that creates them ships in
medusync, so the apps that host the doctypes don't have to know about
the Medusa back-reference.

Five fields:
  Security.medusa_product_id        — back-link set by product.synced
  Security Sale.source              — order origin (Direct / Platform Purchase / Backend Order)
  Security Sale.medusa_order_id     — back-link set by order.placed
  Wallet Deposit.medusa_originated  — 1 if API-created, 0 if operator-created
  Wallet Withdrawal.medusa_originated — same semantics

Idempotent. Checks each field's existence before insert. Backfills
existing rows with safe defaults (source='Backend Order' for legacy
Sales; medusa_originated derived from reference_no prefix heuristic).
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	# ── 1. Security.medusa_product_id ────────────────────────────────
	if not frappe.db.exists(
		"Custom Field", {"dt": "Security", "fieldname": "medusa_product_id"}
	):
		create_custom_fields(
			{
				"Security": [
					{
						"fieldname": "medusa_product_id",
						"label": "Medusa Product ID",
						"fieldtype": "Data",
						"unique": 1,
						"no_copy": 1,
						"in_standard_filter": 1,
						"insert_after": "medusa_product_id",
						"description": (
							"Back-link to the Medusa Product created from this "
							"Security. Stamped by the medusync polemarch handler "
							"when the Medusa-side erpnext-forward subscriber "
							"echoes a product.created event back."
						),
					},
				],
			},
			ignore_validate=True,
			update=True,
		)

	# ── 2. Security Sale.source + medusa_order_id ────────────────────
	if not frappe.db.exists(
		"Custom Field", {"dt": "Security Sale", "fieldname": "source"}
	):
		create_custom_fields(
			{
				"Security Sale": [
					{
						"fieldname": "source",
						"label": "Source",
						"fieldtype": "Select",
						"options": "Direct\nPlatform Purchase\nBackend Order",
						"default": "Backend Order",
						"in_list_view": 1,
						"in_standard_filter": 1,
						"insert_after": "party",
						"description": (
							"Where this Sale originated. Platform Purchase = "
							"customer placed it via the Polemarch storefront "
							"(synced from Medusa). Backend Order = operator "
							"created it inside ERPNext. Direct = anything else."
						),
					},
				],
			},
			ignore_validate=True,
			update=True,
		)
		# Backfill existing rows — anything created before this patch was
		# operator-entered, so "Backend Order" is the right default.
		frappe.db.sql(
			"UPDATE `tabSecurity Sale` SET source = %s "
			"WHERE source IS NULL OR source = ''",
			("Backend Order",),
		)

	if not frappe.db.exists(
		"Custom Field", {"dt": "Security Sale", "fieldname": "medusa_order_id"}
	):
		create_custom_fields(
			{
				"Security Sale": [
					{
						"fieldname": "medusa_order_id",
						"label": "Medusa Order ID",
						"fieldtype": "Data",
						"unique": 1,
						"read_only": 1,
						"no_copy": 1,
						"in_standard_filter": 1,
						"insert_after": "source",
						"description": (
							"Back-link to the Medusa order that triggered this "
							"Sale (when source='Platform Purchase'). Empty for "
							"Backend Order / Direct sales. Used for webhook "
							"idempotency by the medusync polemarch handler."
						),
					},
				],
			},
			ignore_validate=True,
			update=True,
		)

	# ── 3. Wallet Deposit + Wallet Withdrawal.medusa_originated ─────
	originated_fields_spec = {
		"fieldname": "medusa_originated",
		"label": "Medusa Originated",
		"fieldtype": "Check",
		"default": "0",
		"no_copy": 1,
		"read_only": 1,
		"in_standard_filter": 1,
		"description": (
			"Set to 1 when this doc was created via the medusync polemarch "
			"handler. The Medusa pull cron skips these to avoid "
			"double-mirroring; operator-created docs (default 0) are pulled "
			"into Medusa as cashfree_wallet transactions."
		),
	}

	for doctype, insert_after in [
		("Wallet Deposit", "reference_no"),
		("Wallet Withdrawal", "reference_no"),
	]:
		if not frappe.db.exists(
			"Custom Field", {"dt": doctype, "fieldname": "medusa_originated"}
		):
			create_custom_fields(
				{doctype: [{**originated_fields_spec, "insert_after": insert_after}]},
				ignore_validate=True,
				update=True,
			)

	# Backfill medusa_originated for historical rows: any reference_no
	# that looks like a gateway reference was clearly API-created.
	for doctype in ("Wallet Deposit", "Wallet Withdrawal"):
		if not frappe.db.has_column(doctype, "medusa_originated"):
			continue
		frappe.db.sql(
			f"""
			UPDATE `tab{doctype}`
			   SET medusa_originated = 1
			 WHERE COALESCE(medusa_originated, 0) = 0
			   AND (
				    reference_no LIKE 'cashfree_%%'
				 OR reference_no LIKE 'razorpay_%%'
				 OR reference_no LIKE 'wallet_%%'
				 OR reference_no LIKE 'evt_%%'
				 OR reference_no LIKE 'wt_%%'
			   )
			"""
		)
	frappe.db.commit()
