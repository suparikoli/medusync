# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""ERPNext owns the catalogue, and owning it means Medusa cannot quietly
rewrite or destroy it.

Two rules the brief is explicit about: Medusa updates must not
automatically overwrite ERPNext product fields, and a Medusa product
deletion must never delete the ERPNext Item.

Neither is a mapping question. A mapping that carries a title in both
directions is a reasonable thing to configure; the guard is what stops
that configuration from silently overwriting a description someone in
purchasing wrote, and what turns a storefront delete into unlinking
rather than destroying an Item with stock and purchase history.

Nothing here commits. Every write must die with the test's transaction,
or a real catalogue record would keep whatever the test left on it.
"""

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import catalogue

ALLOW_FIELD = "allow_medusa_catalogue_updates"


class CatalogueCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		rows = frappe.get_all("Item", fields=["name"], limit=1)
		if not rows:
			self.skipTest("no Item on this site")
		self.item = rows[0].name
		self._allow_before = frappe.db.get_single_value("Medusync Settings", ALLOW_FIELD)
		self._link_before = frappe.db.get_value("Item", self.item, "medusa_product_id")
		self._set_allow(0)

	def tearDown(self):
		self._set_allow(self._allow_before or 0)
		frappe.db.set_value(
			"Item", self.item, "medusa_product_id", self._link_before, update_modified=False
		)
		super().tearDown()

	def _set_allow(self, value):
		frappe.db.set_single_value("Medusync Settings", ALLOW_FIELD, value)
		frappe.clear_cache(doctype="Medusync Settings")


class TestUpdatesAreRefusedByDefault(CatalogueCase):
	def test_an_update_to_an_existing_catalogue_record_is_held_back(self):
		verdict = catalogue.guard("Item", "name", self.item, "product.updated")
		self.assertTrue(verdict.blocked)
		self.assertEqual(verdict.reason, "catalogue-protected")

	def test_creating_one_is_still_allowed(self):
		# Whether a Medusa-created product may become an Item at all is the
		# plugin's policy to decide; this guard is only about overwriting
		# what ERPNext already has.
		verdict = catalogue.guard("Item", "name", "NO-SUCH-ITEM-XYZ", "product.created")
		self.assertFalse(verdict.blocked)

	def test_turning_it_on_lets_updates_through(self):
		self._set_allow(1)
		verdict = catalogue.guard("Item", "name", self.item, "product.updated")
		self.assertFalse(verdict.blocked)

	def test_another_doctype_is_none_of_its_business(self):
		verdict = catalogue.guard("Customer", "name", "whoever", "customer.updated")
		self.assertFalse(verdict.blocked)

	def test_it_looks_the_record_up_by_the_key_it_was_given(self):
		frappe.db.set_value(
			"Item", self.item, "medusa_product_id", "prod_guard_1", update_modified=False
		)
		verdict = catalogue.guard("Item", "medusa_product_id", "prod_guard_1", "product.updated")
		self.assertTrue(verdict.blocked)


class TestDeletionNeverDestroys(CatalogueCase):
	def test_a_medusa_delete_only_unlinks(self):
		# Whether the borrowed Item happens to be enabled is none of this
		# test's business. That the guard left it exactly as it found it is.
		was_disabled = frappe.db.get_value("Item", self.item, "disabled")
		frappe.db.set_value(
			"Item", self.item, "medusa_product_id", "prod_guard_2", update_modified=False
		)
		verdict = catalogue.guard("Item", "medusa_product_id", "prod_guard_2", "product.deleted")
		self.assertTrue(verdict.blocked)
		self.assertEqual(verdict.reason, "catalogue-unlinked")
		# the Item is still here, untouched, and simply no longer claimed
		self.assertTrue(frappe.db.exists("Item", self.item))
		self.assertEqual(frappe.db.get_value("Item", self.item, "disabled"), was_disabled)
		self.assertFalse(frappe.db.get_value("Item", self.item, "medusa_product_id"))

	def test_deleting_something_that_was_never_linked_is_harmless(self):
		verdict = catalogue.guard("Item", "medusa_product_id", "prod-never-existed", "product.deleted")
		self.assertTrue(verdict.blocked)
		self.assertTrue(frappe.db.exists("Item", self.item))

	def test_the_setting_does_not_license_deletion(self):
		# "Medusa may update catalogue fields" is about fields. Nothing
		# turns a storefront delete into an ERPNext delete.
		self._set_allow(1)
		frappe.db.set_value(
			"Item", self.item, "medusa_product_id", "prod_guard_3", update_modified=False
		)
		verdict = catalogue.guard("Item", "medusa_product_id", "prod_guard_3", "product.deleted")
		self.assertTrue(verdict.blocked)
		self.assertEqual(verdict.reason, "catalogue-unlinked")
		self.assertTrue(frappe.db.exists("Item", self.item))


class TestTheCatalogueIsWhateverSettingsSays(CatalogueCase):
	"""The guard must follow the configured catalogue DocType, not a
	hard-coded Item — a site that sells Assets or Services moved it."""

	def test_the_configured_doctype_is_the_one_protected(self):
		before = frappe.db.get_single_value("Medusync Settings", "products_doctype")
		frappe.db.set_single_value("Medusync Settings", "products_doctype", "Customer")
		frappe.clear_cache(doctype="Medusync Settings")
		try:
			customer = frappe.get_all("Customer", fields=["name"], limit=1)
			if not customer:
				self.skipTest("no Customer on this site")
			self.assertTrue(catalogue.guard("Customer", "name", customer[0].name, "product.updated").blocked)
			self.assertFalse(catalogue.guard("Item", "name", self.item, "product.updated").blocked)
		finally:
			frappe.db.set_single_value("Medusync Settings", "products_doctype", before)
			frappe.clear_cache(doctype="Medusync Settings")
