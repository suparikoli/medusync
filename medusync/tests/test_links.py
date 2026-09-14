# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Medusa ids live in Medusync Link, not on the documents.

Mappings still name `medusa_order_id` and its relatives; those are link
keys, and reading or writing one must behave like a field without one
ever being added to the doctype.
"""

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import links, sites

SITE = "links-test"


class LinkCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		if not frappe.db.exists("Medusync Site", SITE):
			doc = frappe.new_doc("Medusync Site")
			doc.update({"site_id": SITE, "title": SITE, "enabled": 1, "medusa_url": "http://127.0.0.1:9000"})
			doc.insert(ignore_permissions=True)
		sites.clear_cache()
		rows = frappe.get_all("Customer", fields=["name"], limit=2)
		if len(rows) < 2:
			self.skipTest("needs two Customers")
		self.customer, self.other = rows[0].name, rows[1].name
		frappe.flags.medusync_site_id = SITE

	def tearDown(self):
		frappe.flags.medusync_site_id = None
		frappe.db.delete(links.LINK_DOCTYPE, {"site": SITE})
		super().tearDown()


class TestRemembering(LinkCase):
	def test_a_link_reads_back_both_ways(self):
		links.remember("Customer", self.customer, "cus_1", entity="customer")
		self.assertEqual(links.name_for("Customer", "cus_1", entity="customer"), self.customer)
		self.assertEqual(links.medusa_id_for("Customer", self.customer, entity="customer"), "cus_1")

	def test_remembering_twice_keeps_one_link(self):
		links.remember("Customer", self.customer, "cus_1", entity="customer")
		links.remember("Customer", self.customer, "cus_1", entity="customer")
		self.assertEqual(frappe.db.count(links.LINK_DOCTYPE, {"site": SITE, "document_name": self.customer}), 1)

	def test_a_relinked_document_follows_the_store(self):
		links.remember("Customer", self.customer, "cus_1", entity="customer")
		links.remember("Customer", self.customer, "cus_2", entity="customer")
		self.assertEqual(links.medusa_id_for("Customer", self.customer, entity="customer"), "cus_2")
		self.assertIsNone(links.name_for("Customer", "cus_1", entity="customer"))

	def test_one_medusa_record_cannot_be_two_documents(self):
		links.remember("Customer", self.customer, "cus_1", entity="customer")
		with self.assertRaises(frappe.ValidationError):
			links.remember("Customer", self.other, "cus_1", entity="customer")

	def test_another_store_has_its_own_ids(self):
		links.remember("Customer", self.customer, "cus_1", entity="customer")
		self.assertIsNone(links.medusa_id_for("Customer", self.customer, site="some-other-store", entity="customer"))

	def test_forgetting_drops_the_link(self):
		links.remember("Customer", self.customer, "cus_1", entity="customer")
		self.assertEqual(links.forget("Customer", self.customer), 1)
		self.assertIsNone(links.name_for("Customer", "cus_1"))


class TestLinkKeys(LinkCase):
	def test_link_keys_are_lifted_off_a_payload(self):
		payload = {"customer_name": "X", "medusa_customer_id": "cus_9"}
		taken = links.take_link_keys(payload, "Customer")
		self.assertEqual(payload, {"customer_name": "X"})
		self.assertEqual(taken, {"medusa_customer_id": "cus_9"})

	def test_order_details_ride_on_the_order_link(self):
		orders = frappe.get_all("Sales Order", fields=["name"], limit=1)
		if not orders:
			self.skipTest("no Sales Order")
		so = orders[0].name
		links.remember_all(
			"Sales Order",
			so,
			{"medusa_order_id": "order_1", "medusa_display_id": 42, "medusa_payment_method": "captured"},
		)
		doc = frappe.get_doc("Sales Order", so)
		self.assertEqual(links.value_for(doc, "medusa_order_id"), "order_1")
		self.assertEqual(links.value_for(doc, "medusa_display_id"), 42)
		self.assertEqual(links.value_for(doc, "medusa_payment_method"), "captured")

	def test_a_key_lookup_finds_the_document_through_its_link(self):
		links.remember("Customer", self.customer, "cus_7", entity="customer")
		self.assertEqual(links.find_by_key("Customer", "medusa_customer_id", "cus_7"), self.customer)

	def test_an_ordinary_key_is_still_a_column(self):
		email = frappe.db.get_value("Customer", self.customer, "email_id")
		if not email:
			self.skipTest("customer has no email")
		self.assertEqual(links.find_by_key("Customer", "email_id", email), self.customer)

	def test_no_custom_field_is_involved(self):
		self.assertFalse(frappe.db.exists("Custom Field", {"fieldname": ["like", "medusa_%_id"]}))
