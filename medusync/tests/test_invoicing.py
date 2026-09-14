# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""What a store order becomes, and who numbers its invoice.

One series: ERPNext numbers the invoice and sends it to the store for the
customer. Separate series: the store numbered it, ERPNext books it under
that number and sends nothing back.
"""

from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import invoicing, links, sites
from medusync.handlers.commerce import reverse

SITE = "inv-test"


class InvoicingCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		if frappe.db.exists("Medusync Site", SITE):
			frappe.delete_doc("Medusync Site", SITE, force=1, ignore_permissions=True)
		self.site = frappe.new_doc("Medusync Site")
		self.site.update({"site_id": SITE, "title": SITE, "enabled": 1, "medusa_url": "http://127.0.0.1:9000", "record_payments": 0})
		self.site.insert(ignore_permissions=True)
		sites.clear_cache()
		frappe.flags.medusync_site_id = SITE

	def tearDown(self):
		frappe.flags.medusync_site_id = None
		frappe.db.delete(links.LINK_DOCTYPE, {"site": SITE})
		if frappe.db.exists("Medusync Site", SITE):
			frappe.delete_doc("Medusync Site", SITE, force=1, ignore_permissions=True)
		sites.clear_cache()
		super().tearDown()

	def _set(self, **values):
		for key, value in values.items():
			self.site.set(key, value)
		self.site.save(ignore_permissions=True)


class TestTheChoices(InvoicingCase):
	def test_a_site_that_chose_nothing_keeps_sales_orders_and_erpnext_numbers(self):
		opts = invoicing.options(SITE)
		self.assertEqual(invoicing.target_doctype("Sales Order", opts), "Sales Order")
		self.assertFalse(invoicing.store_numbered(opts))

	def test_an_order_can_become_an_invoice_directly(self):
		self._set(order_document=invoicing.ORDER_DOCUMENT_SI)
		opts = invoicing.options(SITE)
		self.assertEqual(invoicing.target_doctype("Sales Order", opts), "Sales Invoice")

	def test_both_keeps_the_order_and_invoices_it(self):
		self._set(order_document=invoicing.ORDER_DOCUMENT_BOTH)
		self.assertEqual(invoicing.target_doctype("Sales Order", invoicing.options(SITE)), "Sales Order")

	def test_store_numbering_needs_a_prefix(self):
		with self.assertRaises(frappe.MandatoryError):
			self._set(invoice_numbering=invoicing.NUMBERING_STORE, store_invoice_prefix=None)

	def test_a_store_numbered_invoice_without_a_number_is_refused(self):
		self._set(invoice_numbering=invoicing.NUMBERING_STORE, store_invoice_prefix="WEB-")
		doc = frappe._dict(insert=lambda **k: None)
		with self.assertRaises(frappe.ValidationError):
			invoicing.insert_invoice(doc, {}, invoicing.options(SITE))

	def test_a_store_number_becomes_the_invoice_name(self):
		self._set(invoice_numbering=invoicing.NUMBERING_STORE, store_invoice_prefix="WEB-")
		seen = {}
		doc = frappe._dict(insert=lambda **k: seen.update(k))
		invoicing.insert_invoice(doc, {"medusa_invoice_number": "WEB-00042"}, invoicing.options(SITE))
		self.assertEqual(seen.get("set_name"), "WEB-00042")


class TestWhatGoesBack(InvoicingCase):
	def _invoice(self):
		return frappe._dict(
			doctype="Sales Invoice",
			name="SINV-T-1",
			items=[],
			docstatus=1,
			grand_total=100,
			outstanding_amount=0,
			currency="INR",
			posting_date="2026-09-13",
			is_return=0,
			medusa_order_id="order_inv_1",
		)

	def _sent(self, doc):
		sent = []
		with patch.object(reverse, "_guard", return_value=True), patch.object(
			reverse, "_deliver", side_effect=lambda *a: sent.append(a)
		):
			reverse.on_sales_invoice(doc, "on_submit")
		return sent

	def test_one_series_sends_the_invoice_with_its_pdf(self):
		self._set(send_invoice_to_store=1, invoice_print_format=None)
		sent = self._sent(self._invoice())
		self.assertEqual(sent[0][0], "order.invoiced")
		self.assertEqual(sent[0][2]["pdf"]["name"], "SINV-T-1")

	def test_without_the_pdf_option_only_the_facts_go(self):
		self._set(send_invoice_to_store=0)
		sent = self._sent(self._invoice())
		self.assertNotIn("pdf", sent[0][2])

	def test_a_store_numbered_invoice_is_never_sent_back(self):
		self._set(invoice_numbering=invoicing.NUMBERING_STORE, store_invoice_prefix="WEB-")
		self.assertEqual(self._sent(self._invoice()), [])


class TestTellingTheStore(InvoicingCase):
	def test_saving_the_site_sends_it_the_invoice_choices(self):
		sent = []
		with patch("medusync.config.is_enabled", return_value=True), patch(
			"medusync.outbound.emit", side_effect=lambda *a, **k: sent.append((a, k))
		):
			self._set(invoice_numbering=invoicing.NUMBERING_STORE, store_invoice_prefix="WEB-")
		events = [a[0] for a, _ in sent]
		self.assertIn(invoicing.SETTINGS_EVENT, events)
		args, kwargs = next((a, k) for a, k in sent if a[0] == invoicing.SETTINGS_EVENT)
		self.assertEqual(args[1]["invoice_numbering"], "store")
		self.assertEqual(args[1]["store_invoice_prefix"], "WEB-")
		self.assertIsNone(kwargs["per_site"]("another-store", args[1]))


class TestWhichSeriesNumbersIt(InvoicingCase):
	"""Three answers to one question, and only one of them belongs to the store."""

	def test_the_company_series_is_what_a_site_gets_by_default(self):
		opts = invoicing.options(SITE)
		self.assertEqual(opts.invoice_numbering, invoicing.NUMBERING_ERPNEXT)
		self.assertIsNone(invoicing.separate_series(opts))
		self.assertFalse(invoicing.store_numbered(opts))

	def test_a_store_can_have_a_series_of_its_own_and_erpnext_still_numbers_it(self):
		self._set(invoice_numbering=invoicing.NUMBERING_ERPNEXT_SERIES, erpnext_invoice_series="SPX-INV-.YYYY.-")
		opts = invoicing.options(SITE)
		self.assertEqual(invoicing.separate_series(opts), "SPX-INV-.YYYY.-")
		# The store is still not the one counting, so an invoice still goes back.
		self.assertFalse(invoicing.store_numbered(opts))

	def test_the_old_single_label_still_means_the_company_series(self):
		frappe.db.set_value("Medusync Site", SITE, "invoice_numbering", invoicing.NUMBERING_ERPNEXT_LEGACY)
		opts = invoicing.options(SITE)
		self.assertEqual(opts.invoice_numbering, invoicing.NUMBERING_ERPNEXT)
		self.assertIsNone(invoicing.separate_series(opts))

	def test_a_store_series_starts_at_str_unless_somebody_changes_it(self):
		self._set(invoice_numbering=invoicing.NUMBERING_STORE)
		self.assertEqual(invoicing.options(SITE).store_invoice_prefix, invoicing.DEFAULT_STORE_PREFIX)
		self._set(store_invoice_prefix="WEB-")
		self.assertEqual(invoicing.options(SITE).store_invoice_prefix, "WEB-")


class TestTheStoreSeriesIsTheStores(InvoicingCase):
	"""Two ends counting one series hand out the same number twice."""

	def setUp(self):
		super().setUp()
		self._set(invoice_numbering=invoicing.NUMBERING_STORE, store_invoice_prefix="STR-")
		invoicing.clear_cache()

	def tearDown(self):
		invoicing.clear_cache()
		super().tearDown()

	def _invoice(self, name):
		doc = frappe.new_doc("Sales Invoice")
		doc.name = name
		return doc

	def test_erpnext_refuses_to_raise_one_in_the_stores_series(self):
		with self.assertRaises(frappe.ValidationError):
			invoicing.guard_store_series(self._invoice("STR-00042"))

	def test_the_check_does_not_care_about_case(self):
		with self.assertRaises(frappe.ValidationError):
			invoicing.guard_store_series(self._invoice("str-00042"))

	def test_another_series_is_none_of_its_business(self):
		invoicing.guard_store_series(self._invoice("SINV-00001"))

	def test_what_the_store_itself_sends_is_let_through(self):
		frappe.flags.medusync_store_series = True
		try:
			invoicing.guard_store_series(self._invoice("STR-00042"))
		finally:
			frappe.flags.medusync_store_series = False

	def test_it_leaves_every_other_doctype_alone(self):
		order = frappe.new_doc("Sales Order")
		order.name = "STR-00042"
		invoicing.guard_store_series(order)

	def test_a_store_that_does_not_number_its_own_claims_no_prefix(self):
		self._set(invoice_numbering=invoicing.NUMBERING_ERPNEXT)
		invoicing.clear_cache()
		invoicing.guard_store_series(self._invoice("STR-00042"))
