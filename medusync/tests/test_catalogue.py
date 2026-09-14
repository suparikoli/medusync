# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Which DocType holds the catalogue is one setting, and both systems
have to agree on it.

It is Item on a stock ERPNext, but a project may keep its catalogue
somewhere else. Changing it here has to move the per-document selector
onto the new doctype and tell every connected Medusa store, or the
plugin's "link this product to an existing one" search would go on
looking in the wrong place.
"""

from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import selection, sites


class TestCatalogueDoctype(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		sites.clear_cache()
		self.settings = frappe.get_single("Medusync Settings")
		self._before = self.settings.get("products_doctype")

	def tearDown(self):
		# Provisioning a selector runs DDL, and DDL commits implicitly in
		# MariaDB — so a value this test changed has already escaped the
		# test transaction and the rollback will not undo it. Put it back
		# and commit, or every later module sees the wrong catalogue.
		frappe.db.set_single_value("Medusync Settings", "products_doctype", self._before or "Item")
		frappe.db.commit()
		frappe.clear_cache(doctype="Medusync Settings")
		sites.clear_cache()
		super().tearDown()

	def test_the_catalogue_doctype_is_a_setting(self):
		self.assertTrue(frappe.get_meta("Medusync Settings").has_field("products_doctype"))

	def test_it_defaults_to_item(self):
		self.assertEqual(self._before or "Item", "Item")

	def test_the_catalogue_is_under_selection_only_when_listed(self):
		# It used to carry the selector on its own, which is how an Item
		# form grew fields nobody asked for. Now listing it is the request.
		settings = frappe.get_single("Medusync Settings")
		settings.set("selection_doctypes", [])
		settings.flags.ignore_permissions = True
		settings.save(ignore_permissions=True)
		frappe.clear_cache(doctype="Medusync Settings")
		self.assertNotIn("Item", selection.selection_doctypes())
		settings.append("selection_doctypes", {"document_type": "Item", "enabled": 1})
		settings.save(ignore_permissions=True)
		frappe.clear_cache(doctype="Medusync Settings")
		self.assertIn("Item", selection.selection_doctypes())

	def test_changing_the_catalogue_doctype_adds_no_field(self):
		frappe.db.set_single_value("Medusync Settings", "products_doctype", "Customer")
		frappe.clear_cache(doctype="Medusync Settings")
		self.assertNotIn("Customer", selection.selection_doctypes())
		self.assertFalse(frappe.db.exists("Custom Field", {"dt": "Customer", "fieldname": ["like", "medusync_%"]}))

	def test_changing_it_tells_every_site(self):
		announced = []
		with patch("medusync.outbound.emit", side_effect=lambda *a, **k: announced.append((a, k))):
			doc = frappe.get_single("Medusync Settings")
			doc.products_doctype = "Customer"
			doc.save(ignore_permissions=True)
		events = [a[0] for a, _ in announced]
		self.assertIn("medusync.settings.changed", events)
		payload = [a[1] for a, _ in announced if a[0] == "medusync.settings.changed"][0]
		self.assertEqual(payload["products_doctype"], "Customer")

	def test_saving_without_a_change_announces_nothing(self):
		announced = []
		with patch("medusync.outbound.emit", side_effect=lambda *a, **k: announced.append(a)):
			doc = frappe.get_single("Medusync Settings")
			doc.save(ignore_permissions=True)
		self.assertEqual([a for a in announced if a[0] == "medusync.settings.changed"], [])
