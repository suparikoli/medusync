# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Medusa → Polemarch Security Sale lifecycle.

handle_placed       — create + submit a Security Sale with
                       source='Platform Purchase' (fires the
                       FIFO + Investment Disposal + COGS JE chain)
handle_canceled     — cancel the linked Security Sale (fires the
                       full cancel cascade)

Both are idempotent on `medusa_order_id` — a Medusa plugin that
retries after a timeout never creates a duplicate Sale.
"""

from typing import Any, Optional

import frappe

from medusync.handlers.polemarch.common import (
	customer_by_email,
	set_doctype_fields,
	upsert_mapped_customer,
)


def handle_placed(data: dict, event_id: Optional[str] = None) -> dict:
	"""Storefront-placed order → create + submit a Security Sale.

	Expected payload shape (sent by the Medusa erpnext-plugin's order
	subscriber after pricing finalisation):
	  {
	    order_id: <medusa order id>,
	    customer: <frappe customer name OR email>,
	    security: <ISIN>,
	    qty: <int>,
	    rate: <currency, per unit>,
	    payment_method: 'Customer Wallet' | 'Bank' | 'Cash' | 'Default Receivable',
	    from_classification: 'Stock in Trade' | 'Investment',
	    discount_amount?: <currency>   # promo-wallet utilisation —
	                                    # recorded as a remarks line for
	                                    # now; future hardening = post
	                                    # a Discount JE alongside.
	    posting_date?: <YYYY-MM-DD>
	  }
	"""
	# Resolve customer — accept either Frappe Customer.name or email
	customer = data.get("customer") or data.get("customer_email") or ""
	if "@" in customer:
		resolved = customer_by_email(customer.lower())
		if not resolved:
			return {
				"status": "customer_not_found",
				"lookup_email": customer,
				"hint": "Create the Frappe Customer first (or rely on customer.created webhook)",
			}
		customer = resolved
	if not frappe.db.exists("Customer", customer):
		return {"status": "customer_not_found", "customer": customer}

	# Validate the security exists. Storefront should never reference an
	# unknown ISIN, but guard against payload tampering.
	security = data.get("security") or ""
	if not frappe.db.exists("Security", security):
		return {"status": "security_not_found", "security": security}

	medusa_order_id = data.get("order_id") or event_id

	# Idempotency via the medusa_order_id Custom Field
	if medusa_order_id and frappe.db.has_column("Security Sale", "medusa_order_id"):
		existing = frappe.db.get_value(
			"Security Sale",
			{"medusa_order_id": medusa_order_id},
			"name",
		)
		if existing:
			return {
				"status": "idempotent_skip",
				"sale": existing,
				"medusa_order_id": medusa_order_id,
			}

	qty = float(data.get("qty") or 0)
	rate = float(data.get("rate") or 0)
	discount = float(data.get("discount_amount") or 0)
	if qty <= 0 or rate <= 0:
		return {
			"status": "invalid_payload",
			"reason": "qty and rate must be > 0",
			"qty": qty,
			"rate": rate,
		}

	company = frappe.defaults.get_global_default("company")
	remarks_parts = [f"Medusa order {medusa_order_id}"]
	if discount > 0:
		remarks_parts.append(
			f"Promo-wallet discount applied: ₹{discount:,.2f} "
			f"(included in storefront-side pricing; sale.rate reflects net)"
		)

	sale = frappe.get_doc({
		"doctype": "Security Sale",
		"company": company,
		"posting_date": data.get("posting_date") or frappe.utils.today(),
		"security": security,
		"party_type": "Customer",
		"party": customer,
		"from_classification": data.get("from_classification", "Stock in Trade"),
		"qty": qty,
		"rate": rate,
		"payment_method": data.get("payment_method", "Customer Wallet"),
		"source": "Platform Purchase",
		"remarks": " | ".join(remarks_parts),
	})
	if frappe.db.has_column("Security Sale", "medusa_order_id") and medusa_order_id:
		sale.medusa_order_id = medusa_order_id
	sale.flags.ignore_permissions = True
	sale.insert(ignore_permissions=True)
	sale.submit()
	frappe.db.commit()
	return {
		"status": "created",
		"sale": sale.name,
		"medusa_order_id": medusa_order_id,
		"amount": sale.amount,
		"discount_applied": discount if discount > 0 else None,
	}


def handle_canceled(data: dict, event_id: Optional[str] = None) -> dict:
	"""Medusa-side order cancellation → cancel the linked Security Sale.

	Triggers the Frappe Security Sale's full cancel cascade (Gap 3 fix):
	wallet reversal → CH snapshot rollback → JEs cancelled → Disposal
	cancelled → qty_disposed_<class> restored. Idempotent: returns
	'already_cancelled' if the Sale is docstatus=2 already, and
	'not_found' if there's no Sale for the given Medusa order id (e.g.
	cancellation before the order.placed webhook completed).
	"""
	medusa_order_id = data.get("order_id") or event_id
	if not medusa_order_id:
		return {"status": "skipped", "reason": "no_order_id"}

	if not frappe.db.has_column("Security Sale", "medusa_order_id"):
		return {
			"status": "skipped",
			"reason": "medusa_order_id Custom Field not deployed yet",
		}

	sale_name = frappe.db.get_value(
		"Security Sale", {"medusa_order_id": medusa_order_id}, "name"
	)
	if not sale_name:
		return {
			"status": "not_found",
			"medusa_order_id": medusa_order_id,
			"hint": (
				"Sale for this Medusa order id doesn't exist on Frappe — "
				"either order.placed webhook never landed, or the Sale "
				"was already manually deleted."
			),
		}

	sale = frappe.get_doc("Security Sale", sale_name)
	if sale.docstatus == 2:
		return {
			"status": "already_cancelled",
			"sale": sale_name,
			"medusa_order_id": medusa_order_id,
		}
	if sale.docstatus == 0:
		sale.flags.ignore_permissions = True
		sale.delete(ignore_permissions=True)
		frappe.db.commit()
		return {
			"status": "draft_deleted",
			"sale": sale_name,
			"medusa_order_id": medusa_order_id,
		}

	sale.flags.ignore_permissions = True
	sale.cancel()
	frappe.db.commit()
	return {
		"status": "cancelled",
		"sale": sale_name,
		"medusa_order_id": medusa_order_id,
	}


# ── receive_mapped helpers ─────────────────────────────────────────


def upsert_via_mapping(
	doctype: str,
	key_field: str,
	key_value: Any,
	payload: dict,
	event: str,
	event_id: str,
	allow_create: bool = True,
	allow_update: bool = True,
) -> dict:
	"""Doctype-aware upsert for the canonical-mapping push path
	(`receive_mapped`). Each branch handles the per-doctype quirks
	(Customer's Contact wiring, Security Sale's submit+Disposal chain)
	and then delegates to `set_doctype_fields` for the scalar write.
	"""
	if doctype == "Customer":
		return upsert_mapped_customer(
			key_field=key_field,
			key_value=str(key_value),
			payload=payload,
			allow_create=allow_create,
			allow_update=allow_update,
		)
	# Security Sale is a SUBMITTABLE doctype whose financial effects
	# (wallet debit, Investment Disposal, COGS/revenue JEs) ALL fire in
	# `on_submit`. The generic upsert below only `.insert()`s — that
	# would leave every storefront order as a draft (docstatus=0) Sale
	# with no money moved and no holdings disposed. Route it through the
	# rich `handle_placed` / `handle_canceled` handlers, which insert +
	# submit (or cancel), are idempotent on `medusa_order_id`, and stamp
	# source='Platform Purchase' so the bounce-back Webhook + pull cron
	# skip it (no echo loop).
	if doctype == "Security Sale":
		order_data = dict(payload)
		# The rich handlers key idempotency on `order_id`; the canonical
		# Order↔Security Sale mapping writes the Medusa order id into the
		# `medusa_order_id` column (== key_value here).
		order_data.setdefault(
			"order_id", payload.get("medusa_order_id") or key_value
		)
		if event in ("order.canceled", "order.cancelled"):
			return handle_canceled(order_data, event_id=event_id)
		return handle_placed(order_data, event_id=event_id)
	# Generic fallback — applies to Security, Wallet Deposit, etc. that
	# don't need custom child-doc wiring.
	existing_name = frappe.db.get_value(
		doctype, {key_field: key_value}, "name"
	)
	if existing_name:
		if not allow_update:
			return {
				"doctype": doctype,
				"name": existing_name,
				"status": "skipped",
				"reason": "update not permitted by the sending mapping",
			}
		set_doctype_fields(doctype, existing_name, payload)
		return {
			"doctype": doctype,
			"name": existing_name,
			"status": "updated",
		}
	if not allow_create:
		return {
			"doctype": doctype,
			"name": None,
			"status": "skipped",
			"reason": "create not permitted by the sending mapping",
		}
	new_doc = frappe.get_doc({"doctype": doctype, **payload})
	new_doc.flags.ignore_permissions = True
	new_doc.flags.ignore_mandatory = True
	new_doc.insert(ignore_permissions=True)
	return {
		"doctype": doctype,
		"name": new_doc.name,
		"status": "created",
	}
