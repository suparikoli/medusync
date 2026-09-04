# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Which documents are allowed to sync is decided in ERPNext.

Each configured doctype carries the choice on the document itself: a
single Check while one store is connected, a list of stores once there
are several. The rule has to answer one question — may THIS document
reach THAT site — and every outbound path asks it the same way.

Defaults matter more than the feature: turning selection on must not
silently stop a site that was syncing fine, so an untouched document is
allowed.
"""

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import selection, sites

DT = "Item"


def _site(site_id, enabled=1):
	if frappe.db.exists("Medusync Site", site_id):
		frappe.delete_doc("Medusync Site", site_id, force=1, ignore_permissions=True)
	doc = frappe.new_doc("Medusync Site")
	doc.update(
		{
			"site_id": site_id,
			"title": site_id,
			"enabled": enabled,
			"medusa_url": "http://127.0.0.1:9000",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


class SelectionCase(IntegrationTestCase):
	"""Shared fixture: a real Item to hang the selector on, plus sites."""

	def setUp(self):
		super().setUp()
		self._sites = []
		sites.clear_cache()
		rows = frappe.get_all(DT, fields=["name"], limit=1)
		if not rows:
			self.skipTest("no Item on this site")
		self.item = rows[0].name
		selection.ensure_selector_fields()
		self._clear_doc()
		self._clear_exclusions()

	def tearDown(self):
		self._clear_doc()
		self._clear_exclusions()
		for name in self._sites:
			if frappe.db.exists("Medusync Site", name):
				frappe.delete_doc("Medusync Site", name, force=1, ignore_permissions=True)
		sites.clear_cache()
		super().tearDown()

	def _make_site(self, site_id, enabled=1):
		doc = _site(site_id, enabled)
		self._sites.append(doc.name)
		sites.clear_cache()
		return doc

	def _clear_doc(self):
		frappe.db.set_value(DT, self.item, "medusync_sync", 1, update_modified=False)
		frappe.db.delete(
			"Medusync Site Selection", {"parenttype": DT, "parent": self.item}
		)

	def _clear_exclusions(self):
		for row in frappe.get_all(
			"Medusync Exclusion", filters={"document_type": DT, "document_name": self.item}
		):
			frappe.delete_doc("Medusync Exclusion", row.name, force=1, ignore_permissions=True)

	def _set_sites(self, *site_ids):
		doc = frappe.get_doc(DT, self.item)
		doc.set("medusync_sites", [])
		for sid in site_ids:
			doc.append("medusync_sites", {"site": sid})
		doc.flags.medusync_selection_applying = True
		doc.save(ignore_permissions=True)


class TestSelectorFields(SelectionCase):
	def test_the_check_is_created_on_a_configured_doctype(self):
		self.assertTrue(frappe.db.exists("Custom Field", {"dt": DT, "fieldname": "medusync_sync"}))
		meta = frappe.get_meta(DT)
		field = meta.get_field("medusync_sync")
		self.assertEqual(field.fieldtype, "Check")
		self.assertEqual(field.default, "1")

	def test_the_site_list_is_created_too(self):
		self.assertTrue(frappe.db.exists("Custom Field", {"dt": DT, "fieldname": "medusync_sites"}))
		field = frappe.get_meta(DT).get_field("medusync_sites")
		self.assertEqual(field.fieldtype, "Table MultiSelect")
		self.assertEqual(field.options, "Medusync Site Selection")

	def test_one_site_shows_the_check_and_hides_the_list(self):
		self._make_site("sel-only")
		# every other site off, so exactly one is connected
		others = [s for s in sites.all_sites() if s["site_id"] != "sel-only"]
		for other in others:
			frappe.db.set_value("Medusync Site", other["name"], "enabled", 0, update_modified=False)
		sites.clear_cache()
		try:
			selection.ensure_selector_fields()
			self.assertFalse(selection.field_hidden(DT, "medusync_sync"))
			self.assertTrue(selection.field_hidden(DT, "medusync_sites"))
		finally:
			for other in others:
				frappe.db.set_value("Medusync Site", other["name"], "enabled", 1, update_modified=False)
			sites.clear_cache()

	def test_several_sites_show_the_list_and_hide_the_check(self):
		self._make_site("sel-a")
		self._make_site("sel-b")
		selection.ensure_selector_fields()
		self.assertTrue(selection.field_hidden(DT, "medusync_sync"))
		self.assertFalse(selection.field_hidden(DT, "medusync_sites"))

	def test_provisioning_is_idempotent(self):
		before = frappe.db.count("Custom Field", {"dt": DT, "fieldname": ["like", "medusync_%"]})
		selection.ensure_selector_fields()
		selection.ensure_selector_fields()
		self.assertEqual(
			frappe.db.count("Custom Field", {"dt": DT, "fieldname": ["like", "medusync_%"]}), before
		)


class TestTheRule(SelectionCase):
	def test_a_doctype_nobody_configured_is_not_restricted(self):
		# Selection governs the doctypes an operator listed; everything else
		# is governed by mappings alone, exactly as before this feature.
		self.assertTrue(selection.is_allowed("Sales Taxes and Charges Template", "whatever", "any"))

	def test_an_untouched_document_still_syncs(self):
		self._make_site("sel-a")
		self.assertTrue(selection.is_allowed(DT, self.item, "sel-a"))

	def test_unchecking_stops_every_site(self):
		self._make_site("sel-a")
		self._make_site("sel-b")
		frappe.db.set_value(DT, self.item, "medusync_sync", 0, update_modified=False)
		self.assertFalse(selection.is_allowed(DT, self.item, "sel-a"))
		self.assertFalse(selection.is_allowed(DT, self.item, "sel-b"))

	def test_a_site_list_allows_only_the_listed_sites(self):
		self._make_site("sel-a")
		self._make_site("sel-b")
		self._set_sites("sel-a")
		self.assertTrue(selection.is_allowed(DT, self.item, "sel-a"))
		self.assertFalse(selection.is_allowed(DT, self.item, "sel-b"))

	def test_an_empty_site_list_falls_back_to_the_check(self):
		self._make_site("sel-a")
		self._make_site("sel-b")
		self.assertTrue(selection.is_allowed(DT, self.item, "sel-b"))
		frappe.db.set_value(DT, self.item, "medusync_sync", 0, update_modified=False)
		self.assertFalse(selection.is_allowed(DT, self.item, "sel-b"))

	def test_an_exclusion_denies_one_site_only(self):
		self._make_site("sel-a")
		self._make_site("sel-b")
		selection.exclude(DT, self.item, site="sel-a", reason="test", source="Manual")
		self.assertFalse(selection.is_allowed(DT, self.item, "sel-a"))
		self.assertTrue(selection.is_allowed(DT, self.item, "sel-b"))

	def test_an_exclusion_with_no_site_denies_all_of_them(self):
		self._make_site("sel-a")
		self._make_site("sel-b")
		selection.exclude(DT, self.item, site=None, reason="test", source="Manual")
		self.assertFalse(selection.is_allowed(DT, self.item, "sel-a"))
		self.assertFalse(selection.is_allowed(DT, self.item, "sel-b"))

	def test_sites_allowed_filters_a_candidate_list(self):
		self._make_site("sel-a")
		self._make_site("sel-b")
		self._set_sites("sel-a")
		candidates = [{"site_id": "sel-a"}, {"site_id": "sel-b"}]
		allowed = [s["site_id"] for s in selection.sites_allowed(DT, self.item, candidates)]
		self.assertEqual(allowed, ["sel-a"])

	def test_the_rule_reads_a_document_it_is_handed(self):
		# The outbound hook already holds the doc; re-reading it per site
		# would be a query per site on every save.
		self._make_site("sel-a")
		doc = frappe.get_doc(DT, self.item)
		doc.medusync_sync = 0
		self.assertFalse(selection.is_allowed(DT, self.item, "sel-a", doc=doc))
