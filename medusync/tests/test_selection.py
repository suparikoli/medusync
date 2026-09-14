# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Which documents are allowed to sync is decided in ERPNext, without a
field on the document.

A doctype listed under Sync Selection runs "unless excluded" (everything
goes until switched off) or "only chosen" (nothing goes until picked).
The rule has to answer one question — may THIS document reach THAT site —
and every outbound path asks it the same way.
"""

from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import outbound, selection, sites

DT = "Item"


def _list_under_selection(mode=selection.MODE_UNLESS_EXCLUDED, *doctypes):
	settings = frappe.get_single("Medusync Settings")
	settings.set("selection_doctypes", [])
	for dt in doctypes or (DT,):
		settings.append("selection_doctypes", {"document_type": dt, "enabled": 1, "mode": mode})
	settings.flags.ignore_permissions = True
	settings.save(ignore_permissions=True)
	frappe.clear_cache(doctype="Medusync Settings")


class SelectionCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._sites = []
		sites.clear_cache()
		rows = frappe.get_all(DT, fields=["name"], limit=1)
		if not rows:
			self.skipTest("no Item on this site")
		self.item = rows[0].name
		_list_under_selection()
		self._clear_rows()

	def tearDown(self):
		self._clear_rows()
		for name in self._sites:
			if frappe.db.exists("Medusync Site", name):
				frappe.delete_doc("Medusync Site", name, force=1, ignore_permissions=True)
		sites.clear_cache()
		super().tearDown()

	def _make_site(self, site_id):
		if frappe.db.exists("Medusync Site", site_id):
			frappe.delete_doc("Medusync Site", site_id, force=1, ignore_permissions=True)
		doc = frappe.new_doc("Medusync Site")
		doc.update({"site_id": site_id, "title": site_id, "enabled": 1, "medusa_url": "http://127.0.0.1:9000"})
		doc.insert(ignore_permissions=True)
		self._sites.append(doc.name)
		sites.clear_cache()
		return doc

	def _clear_rows(self):
		for list_doctype in (selection.EXCLUSION_DOCTYPE, selection.INCLUSION_DOCTYPE):
			for row in frappe.get_all(list_doctype, filters={"document_type": DT, "document_name": self.item}):
				frappe.delete_doc(list_doctype, row.name, force=1, ignore_permissions=True)

	def _rows(self, list_doctype):
		return frappe.get_all(
			list_doctype,
			filters={"document_type": DT, "document_name": self.item},
			fields=["site", "source"] if list_doctype == selection.EXCLUSION_DOCTYPE else ["site"],
		)


class TestNothingIsAddedToTheDoctype(SelectionCase):
	def test_listing_a_doctype_creates_no_custom_field(self):
		self.assertFalse(frappe.db.exists("Custom Field", {"dt": DT, "fieldname": ["like", "medusync_%"]}))

	def test_the_desk_learns_the_doctype_from_boot(self):
		boot = frappe._dict()
		selection.boot_session(boot)
		self.assertEqual(boot.medusync_selection.get(DT), selection.MODE_UNLESS_EXCLUDED)


class TestUnlessExcluded(SelectionCase):
	def test_a_doctype_nobody_configured_is_not_restricted(self):
		self.assertTrue(selection.is_allowed("Sales Taxes and Charges Template", "whatever", "any"))

	def test_an_untouched_document_still_syncs(self):
		self._make_site("sel-a")
		self.assertTrue(selection.is_allowed(DT, self.item, "sel-a"))

	def test_an_exclusion_denies_one_site_only(self):
		self._make_site("sel-a")
		self._make_site("sel-b")
		selection.exclude(DT, self.item, site="sel-a", reason="test")
		self.assertFalse(selection.is_allowed(DT, self.item, "sel-a"))
		self.assertTrue(selection.is_allowed(DT, self.item, "sel-b"))

	def test_an_exclusion_with_no_site_denies_all_of_them(self):
		self._make_site("sel-a")
		self._make_site("sel-b")
		selection.exclude(DT, self.item, site=None, reason="test")
		self.assertFalse(selection.is_allowed(DT, self.item, "sel-a"))
		self.assertFalse(selection.is_allowed(DT, self.item, "sel-b"))

	def test_switching_one_store_off_records_only_that_store(self):
		self._make_site("sel-a")
		self._make_site("sel-b")
		selection.apply_choice(DT, self.item, ["sel-a"])
		self.assertIn("sel-b", {r.site for r in self._rows(selection.EXCLUSION_DOCTYPE)})
		self.assertNotIn("sel-a", {r.site for r in self._rows(selection.EXCLUSION_DOCTYPE)})

	def test_switching_every_store_back_on_clears_the_rows_it_made(self):
		self._make_site("sel-a")
		selection.apply_choice(DT, self.item, [])
		self.assertTrue(self._rows(selection.EXCLUSION_DOCTYPE))
		every = [s["site_id"] for s in sites.all_sites(enabled_only=False)]
		selection.apply_choice(DT, self.item, every)
		self.assertEqual(self._rows(selection.EXCLUSION_DOCTYPE), [])

	def test_a_manual_exclusion_is_not_undone_from_the_document(self):
		self._make_site("sel-a")
		selection.exclude(DT, self.item, site="sel-a", reason="ops", source="Manual")
		with self.assertRaises(frappe.ValidationError):
			selection.apply_choice(DT, self.item, ["sel-a"])

	def test_the_doctype_refuses_a_duplicate(self):
		selection.exclude(DT, self.item, site=None, reason="ops")
		dup = frappe.new_doc(selection.EXCLUSION_DOCTYPE)
		dup.update({"document_type": DT, "document_name": self.item, "source": "Manual"})
		with self.assertRaises(frappe.ValidationError):
			dup.insert(ignore_permissions=True)


class TestOnlyChosen(SelectionCase):
	def setUp(self):
		super().setUp()
		_list_under_selection(selection.MODE_ONLY_CHOSEN)

	def test_nothing_syncs_until_chosen(self):
		self._make_site("sel-a")
		self.assertFalse(selection.is_allowed(DT, self.item, "sel-a"))

	def test_choosing_a_store_lets_only_that_store_through(self):
		self._make_site("sel-a")
		self._make_site("sel-b")
		selection.apply_choice(DT, self.item, ["sel-a"])
		self.assertTrue(selection.is_allowed(DT, self.item, "sel-a"))
		self.assertFalse(selection.is_allowed(DT, self.item, "sel-b"))

	def test_choosing_every_store_is_one_row(self):
		self._make_site("sel-a")
		every = [s["site_id"] for s in sites.all_sites(enabled_only=False)]
		selection.apply_choice(DT, self.item, every)
		rows = self._rows(selection.INCLUSION_DOCTYPE)
		self.assertEqual([r.site for r in rows], [None])

	def test_the_dialog_reads_back_what_was_chosen(self):
		self._make_site("sel-a")
		self._make_site("sel-b")
		selection.set_document_selection(DT, [self.item], ["sel-b"])
		info = selection.document_selection(DT, self.item)
		allowed = {s["site_id"]: s["allowed"] for s in info["sites"]}
		self.assertTrue(allowed["sel-b"])
		self.assertFalse(allowed["sel-a"])


class TestOutboundHonoursTheChoice(SelectionCase):
	def _emit(self, ref):
		sent = []
		with patch("medusync.outbound.send", side_effect=lambda *a, **k: sent.append(k)):
			outbound.emit("inventory.level.set", {"sku": self.item, "quantity": 1}, ref=ref, doctype=DT, docname=self.item)
		return {k["site_id"] for k in sent}

	def test_a_store_switched_off_gets_nothing(self):
		self._make_site("sel-a")
		self._make_site("sel-b")
		selection.apply_choice(DT, self.item, ["sel-a"])
		targets = self._emit("selection")
		self.assertIn("sel-a", targets)
		self.assertNotIn("sel-b", targets)

	def test_a_document_excluded_everywhere_gets_nothing_at_all(self):
		self._make_site("sel-a")
		selection.exclude(DT, self.item, site=None, reason="ops")
		self.assertEqual(self._emit("selection2"), set())
