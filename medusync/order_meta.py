# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Where an order came from, and what has been paid against it.

Two things a storefront cannot work out for itself. An order a
salesperson typed into ERPNext and an order a customer placed on the web
are indistinguishable once both are Sales Orders, and money that arrives
by bank transfer never touches Medusa at all.

Both end up in the Medusa order's *metadata* rather than as Medusa
payment records. ERPNext is the accounting authority in this setup;
inventing Medusa payments it never captured would put figures in a
storefront ledger that no bank statement backs.

Payment is reported per receipt rather than as a running total, and each
receipt names the Payment Entry that produced it, so the far side can
merge them additively. A single "payment" object would mean the second
receipt against an order erased the first.
"""

import frappe

from medusync import config
from medusync.outbound import emit

SOURCE_FIELD = "medusa_order_source"
ORDER_ID_FIELD = "medusa_order_id"

SOURCE_EVENT = "order.source.set"
PAYMENT_EVENT = "order.payment.set"

#: What an order with no recorded channel is called, depending on which
#: system it was born in.
SOURCE_MEDUSA = "medusa"
SOURCE_ERPNEXT = "erpnext"


def _guard() -> bool:
	return not frappe.flags.get("medusync_inbound") and config.is_enabled()


# ── Reading an order ─────────────────────────────────────────────────


def source_of(doc) -> str:
	"""The channel this order arrived through.

	Medusa sends its sales channel when it has one. Failing that, the
	presence of a Medusa order id is itself the answer: with one, the
	order came from a store; without, somebody entered it here, and a
	storefront showing "placed online" for a phone order is worse than
	showing nothing.
	"""
	explicit = (doc.get(SOURCE_FIELD) or "").strip()
	if explicit:
		return explicit
	return SOURCE_MEDUSA if doc.get(ORDER_ID_FIELD) else SOURCE_ERPNEXT


def payment_of(doc) -> dict:
	"""How the order stands at this moment.

	Advance-based, which is what a Sales Order actually knows: money
	received against the order itself. An order settled later through a
	Sales Invoice reads `unpaid` here and is corrected by the invoice
	event (`reverse.on_sales_invoice`), which reports Paid or Unpaid from
	the invoice's own outstanding amount.
	"""
	total = float(doc.get("grand_total") or 0)
	paid = float(doc.get("advance_paid") or 0)
	outstanding = round(total - paid, 2)
	if total > 0 and outstanding <= 0:
		status = "paid"
	elif paid > 0:
		status = "part_paid"
	else:
		status = "unpaid"
	return {
		"method": doc.get("medusa_payment_method") or None,
		"reference": doc.get("medusa_payment_reference") or None,
		"currency": doc.get("currency") or None,
		"total": total,
		"paid": paid,
		"outstanding": outstanding,
		"status": status,
	}


def _order_id_for(reference_doctype: str, reference_name: str) -> str | None:
	"""The Medusa order a Payment Entry reference points at.

	A receipt can be allocated against the Sales Order directly or
	against the Sales Invoice raised from it; both have to lead back to
	the same Medusa order.
	"""
	if not reference_doctype or not reference_name:
		return None
	if reference_doctype == "Sales Order":
		return frappe.db.get_value("Sales Order", reference_name, ORDER_ID_FIELD)
	if reference_doctype == "Sales Invoice":
		direct = frappe.db.get_value("Sales Invoice", reference_name, ORDER_ID_FIELD)
		if direct:
			return direct
		rows = frappe.get_all(
			"Sales Invoice Item",
			filters={"parent": reference_name, "sales_order": ["is", "set"]},
			fields=["sales_order"],
			limit=1,
		)
		if rows:
			return frappe.db.get_value("Sales Order", rows[0].sales_order, ORDER_ID_FIELD)
	return None


# ── Document events ──────────────────────────────────────────────────


def on_sales_order(doc, method=None) -> None:
	"""Submit, amend or cancel an order — tell its store where it came
	from and where its money stands."""
	try:
		if not _guard():
			return
		order_id = doc.get(ORDER_ID_FIELD)
		if not order_id:
			# An order Medusa has never seen has nothing to be told about.
			return
		cancelled = method == "on_cancel" or int(doc.get("docstatus") or 0) == 2
		payload = {
			"medusa_order_id": order_id,
			"source": source_of(doc),
			"sales_order": doc.name,
			"status": "cancelled" if cancelled else "confirmed",
			"payment": payment_of(doc),
		}
		emit(
			SOURCE_EVENT,
			payload,
			ref="%s-%s" % (doc.name, method or "u"),
			doctype="Sales Order",
			docname=doc.name,
		)
	except Exception:
		frappe.log_error(
			title="medusync order source hook failed", message=frappe.get_traceback()
		)


def on_payment_entry(doc, method=None) -> None:
	"""Money received against one or more orders.

	Each order is told what was allocated to *it*, not the size of the
	whole receipt: one transfer settling three orders must not read as
	three full payments.
	"""
	try:
		if not _guard():
			return
		if doc.get("payment_type") != "Receive":
			# Money going out is a supplier payment. Not an order's business.
			return
		cancelled = method == "on_cancel" or int(doc.get("docstatus") or 0) == 2
		for row in doc.get("references") or []:
			order_id = _order_id_for(row.get("reference_doctype"), row.get("reference_name"))
			if not order_id:
				continue
			payload = {
				"medusa_order_id": order_id,
				"payment_entry": doc.name,
				"method": doc.get("mode_of_payment") or None,
				"reference": doc.get("reference_no") or None,
				"amount": float(row.get("allocated_amount") or 0),
				"currency": doc.get("paid_from_account_currency") or doc.get("currency") or None,
				"received_at": str(doc.get("posting_date") or ""),
				"status": "cancelled" if cancelled else "received",
				"against": {
					"doctype": row.get("reference_doctype"),
					"name": row.get("reference_name"),
				},
			}
			emit(
				PAYMENT_EVENT,
				payload,
				# The reference name is part of the key: one receipt split
				# across two orders is two events, and identical event ids
				# would have the far side drop the second as a duplicate.
				ref="%s-%s-%s" % (doc.name, row.get("reference_name"), method or "u"),
				doctype="Payment Entry",
				docname=doc.name,
			)
	except Exception:
		frappe.log_error(
			title="medusync order payment hook failed", message=frappe.get_traceback()
		)
