# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Where an order came from, and what has been paid against it.

Two things the storefront cannot work out for itself. An order placed by
a salesperson in ERPNext and an order placed by a customer on the web
look identical once they are Sales Orders, and money received through a
bank transfer never touches Medusa at all.

Both land in the Medusa order's metadata rather than in a Medusa payment
record: ERPNext is the accounting authority here, and inventing Medusa
payments it never captured would corrupt its own books.
"""

from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import order_meta


class TestTheSource(IntegrationTestCase):
	def test_an_order_medusa_sent_us_names_its_channel(self):
		doc = frappe._dict(
			doctype="Sales Order",
			name="SO-1",
			medusa_order_id="order_01",
			medusa_order_source="web",
		)
		self.assertEqual(order_meta.source_of(doc), "web")

	def test_an_order_medusa_sent_us_without_a_channel_is_just_medusa(self):
		doc = frappe._dict(doctype="Sales Order", name="SO-2", medusa_order_id="order_02")
		self.assertEqual(order_meta.source_of(doc), "medusa")

	def test_an_order_typed_into_erpnext_says_so(self):
		# The storefront should be able to show "placed by our sales team"
		# rather than pretending the customer did it online.
		doc = frappe._dict(doctype="Sales Order", name="SO-3")
		self.assertEqual(order_meta.source_of(doc), "erpnext")


class TestThePayment(IntegrationTestCase):
	def test_the_method_and_reference_travel_together(self):
		doc = frappe._dict(
			doctype="Sales Order",
			name="SO-4",
			medusa_order_id="order_04",
			medusa_payment_method="razorpay",
			medusa_payment_reference="pay_abc",
			currency="INR",
			grand_total=1000,
			advance_paid=1000,
		)
		summary = order_meta.payment_of(doc)
		self.assertEqual(summary["method"], "razorpay")
		self.assertEqual(summary["reference"], "pay_abc")
		self.assertEqual(summary["currency"], "INR")

	def test_paid_in_full_reads_as_paid(self):
		doc = frappe._dict(
			doctype="Sales Order", name="SO-5", medusa_order_id="o5",
			grand_total=500, advance_paid=500, currency="INR",
		)
		self.assertEqual(order_meta.payment_of(doc)["status"], "paid")

	def test_part_paid_reads_as_part_paid(self):
		doc = frappe._dict(
			doctype="Sales Order", name="SO-6", medusa_order_id="o6",
			grand_total=500, advance_paid=200, currency="INR",
		)
		summary = order_meta.payment_of(doc)
		self.assertEqual(summary["status"], "part_paid")
		self.assertEqual(summary["outstanding"], 300)

	def test_nothing_received_reads_as_unpaid(self):
		doc = frappe._dict(
			doctype="Sales Order", name="SO-7", medusa_order_id="o7",
			grand_total=500, advance_paid=0, currency="INR",
		)
		self.assertEqual(order_meta.payment_of(doc)["status"], "unpaid")


class TestWhatReachesMedusa(IntegrationTestCase):
	def _capture(self):
		sent = []
		return sent, patch(
			"medusync.order_meta.emit", side_effect=lambda ev, payload, **kw: sent.append((ev, payload))
		)

	def test_submitting_an_order_reports_its_source_and_payment(self):
		doc = frappe._dict(
			doctype="Sales Order", name="SO-8", docstatus=1,
			medusa_order_id="order_08", medusa_order_source="web",
			medusa_payment_method="cod", medusa_payment_reference=None,
			currency="INR", grand_total=250, advance_paid=0,
		)
		sent, cap = self._capture()
		with cap, patch("medusync.config.is_enabled", return_value=True):
			order_meta.on_sales_order(doc, method="on_submit")
		self.assertEqual([e for e, _ in sent], ["order.source.set"])
		payload = sent[0][1]
		self.assertEqual(payload["medusa_order_id"], "order_08")
		self.assertEqual(payload["source"], "web")
		self.assertEqual(payload["payment"]["method"], "cod")

	def test_an_order_medusa_never_saw_reports_nothing(self):
		doc = frappe._dict(
			doctype="Sales Order", name="SO-9", docstatus=1, currency="INR",
			grand_total=100, advance_paid=0,
		)
		sent, cap = self._capture()
		with cap, patch("medusync.config.is_enabled", return_value=True):
			order_meta.on_sales_order(doc, method="on_submit")
		self.assertEqual(sent, [])

	def test_an_inbound_write_does_not_bounce_straight_back(self):
		doc = frappe._dict(
			doctype="Sales Order", name="SO-10", docstatus=1, medusa_order_id="o10",
			currency="INR", grand_total=100, advance_paid=0,
		)
		sent, cap = self._capture()
		frappe.flags.medusync_inbound = True
		try:
			with cap, patch("medusync.config.is_enabled", return_value=True):
				order_meta.on_sales_order(doc, method="on_submit")
		finally:
			frappe.flags.medusync_inbound = False
		self.assertEqual(sent, [])

	def test_money_received_against_an_order_reaches_it(self):
		pe = frappe._dict(
			doctype="Payment Entry",
			name="PE-1",
			docstatus=1,
			payment_type="Receive",
			mode_of_payment="Bank Draft",
			reference_no="NEFT-991",
			paid_amount=250,
			paid_from_account_currency="INR",
			posting_date="2026-09-04",
			references=[
				frappe._dict(
					reference_doctype="Sales Order", reference_name="SO-11", allocated_amount=250
				)
			],
		)
		sent, cap = self._capture()
		with cap, patch("medusync.config.is_enabled", return_value=True), patch(
			"medusync.order_meta._order_id_for", return_value="order_11"
		):
			order_meta.on_payment_entry(pe, method="on_submit")
		self.assertEqual([e for e, _ in sent], ["order.payment.set"])
		payload = sent[0][1]
		self.assertEqual(payload["medusa_order_id"], "order_11")
		self.assertEqual(payload["method"], "Bank Draft")
		self.assertEqual(payload["reference"], "NEFT-991")
		self.assertEqual(payload["amount"], 250)
		self.assertEqual(payload["status"], "received")

	def test_cancelling_the_receipt_reverses_it(self):
		pe = frappe._dict(
			doctype="Payment Entry", name="PE-2", docstatus=2, payment_type="Receive",
			mode_of_payment="Bank Draft", reference_no="NEFT-992", paid_amount=250,
			paid_from_account_currency="INR", posting_date="2026-09-04",
			references=[
				frappe._dict(
					reference_doctype="Sales Order", reference_name="SO-12", allocated_amount=250
				)
			],
		)
		sent, cap = self._capture()
		with cap, patch("medusync.config.is_enabled", return_value=True), patch(
			"medusync.order_meta._order_id_for", return_value="order_12"
		):
			order_meta.on_payment_entry(pe, method="on_cancel")
		self.assertEqual(sent[0][1]["status"], "cancelled")

	def test_money_paid_out_is_not_an_order_payment(self):
		pe = frappe._dict(
			doctype="Payment Entry", name="PE-3", docstatus=1, payment_type="Pay",
			mode_of_payment="Bank Draft", paid_amount=250, posting_date="2026-09-04",
			references=[
				frappe._dict(
					reference_doctype="Purchase Order", reference_name="PO-1", allocated_amount=250
				)
			],
		)
		sent, cap = self._capture()
		with cap, patch("medusync.config.is_enabled", return_value=True):
			order_meta.on_payment_entry(pe, method="on_submit")
		self.assertEqual(sent, [])

	def test_a_receipt_split_across_two_orders_reaches_both(self):
		pe = frappe._dict(
			doctype="Payment Entry", name="PE-4", docstatus=1, payment_type="Receive",
			mode_of_payment="UPI", reference_no="UPI-1", paid_amount=300,
			paid_from_account_currency="INR", posting_date="2026-09-04",
			references=[
				frappe._dict(
					reference_doctype="Sales Order", reference_name="SO-13", allocated_amount=100
				),
				frappe._dict(
					reference_doctype="Sales Order", reference_name="SO-14", allocated_amount=200
				),
			],
		)
		sent, cap = self._capture()
		with cap, patch("medusync.config.is_enabled", return_value=True), patch(
			"medusync.order_meta._order_id_for",
			side_effect=lambda dt, dn: {"SO-13": "o13", "SO-14": "o14"}.get(dn),
		):
			order_meta.on_payment_entry(pe, method="on_submit")
		# Each order is told what was allocated to IT, not the whole receipt.
		by_order = {p["medusa_order_id"]: p["amount"] for _, p in sent}
		self.assertEqual(by_order, {"o13": 100, "o14": 200})
