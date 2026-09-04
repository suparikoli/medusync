# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""The checkbox and the exclusion list are two views of one decision.

Unchecking a document records it in the central Don't Sync list, and
adding it to that list by hand unchecks the document. If the two ever
disagree the operator cannot tell what the system will do, so they are
kept in step from both directions.
"""

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import selection, sites

DT = "Item"


class ExclusionCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._sites = []
		sites.clear_cache()
		rows = frappe.get_all(DT, fields=["name"], limit=1)
		if not rows:
			self.skipTest("no Item on this site")
		self.item = rows[0].name
		selection.ensure_selector_fields()
		self._reset()

	def tearDown(self):
		self._reset()
		for name in self._sites:
			if frappe.db.exists("Medusync Site", name):
				frappe.delete_doc("Medusync Site", name, force=1, ignore_permissions=True)
		sites.clear_cache()
		super().tearDown()

	def _make_site(self, site_id):
		if frappe.db.exists("Medusync Site", site_id):
			frappe.delete_doc("Medusync Site", site_id, force=1, ignore_permissions=True)
		doc = frappe.new_doc("Medusync Site")
		doc.update(
			{"site_id": site_id, "title": site_id, "enabled": 1, "medusa_url": "http://127.0.0.1:9000"}
		)
		doc.insert(ignore_permissions=True)
		self._sites.append(doc.name)
		sites.clear_cache()
		return doc

	def _reset(self):
		frappe.db.set_value(DT, self.item, "medusync_sync", 1, update_modified=False)
		frappe.db.delete("Medusync Site Selection", {"parenttype": DT, "parent": self.item})
		for row in frappe.get_all(
			"Medusync Exclusion", filters={"document_type": DT, "document_name": self.item}
		):
			frappe.delete_doc("Medusync Exclusion", row.name, force=1, ignore_permissions=True)

	def _exclusions(self):
		return frappe.get_all(
			"Medusync Exclusion",
			filters={"document_type": DT, "document_name": self.item},
			fields=["name", "site", "source"],
		)


class TestCheckboxWritesTheList(ExclusionCase):
	def test_unchecking_records_the_document(self):
		doc = frappe.get_doc(DT, self.item)
		doc.medusync_sync = 0
		doc.save(ignore_permissions=True)
		rows = self._exclusions()
		self.assertEqual(len(rows), 1)
		self.assertIsNone(rows[0].site)
		self.assertEqual(rows[0].source, "Unchecked")

	def test_re_checking_removes_the_record(self):
		doc = frappe.get_doc(DT, self.item)
		doc.medusync_sync = 0
		doc.save(ignore_permissions=True)
		self.assertEqual(len(self._exclusions()), 1)
		doc.reload()
		doc.medusync_sync = 1
		doc.save(ignore_permissions=True)
		self.assertEqual(self._exclusions(), [])

	def test_deselecting_one_site_records_only_that_site(self):
		self._make_site("exc-a")
		self._make_site("exc-b")
		doc = frappe.get_doc(DT, self.item)
		doc.append("medusync_sites", {"site": "exc-a"})
		doc.save(ignore_permissions=True)
		rows = {r.site for r in self._exclusions()}
		self.assertIn("exc-b", rows)
		self.assertNotIn("exc-a", rows)

	def test_a_manual_exclusion_is_left_alone_by_the_checkbox(self):
		self._make_site("exc-a")
		selection.exclude(DT, self.item, site="exc-a", reason="ops", source="Manual")
		doc = frappe.get_doc(DT, self.item)
		doc.medusync_sync = 1
		doc.save(ignore_permissions=True)
		rows = self._exclusions()
		# The operator's own entry is theirs to remove; only rows the
		# checkbox created are cleaned up by the checkbox.
		self.assertEqual([r.source for r in rows], ["Manual"])


class TestListWritesTheCheckbox(ExclusionCase):
	def test_excluding_by_hand_unchecks_the_document(self):
		selection.exclude(DT, self.item, site=None, reason="ops", source="Manual")
		self.assertEqual(frappe.db.get_value(DT, self.item, "medusync_sync"), 0)

	def test_removing_the_exclusion_checks_it_again(self):
		row = selection.exclude(DT, self.item, site=None, reason="ops", source="Manual")
		frappe.delete_doc("Medusync Exclusion", row, force=1, ignore_permissions=True)
		self.assertEqual(frappe.db.get_value(DT, self.item, "medusync_sync"), 1)

	def test_excluding_one_site_drops_it_from_the_document_list(self):
		self._make_site("exc-a")
		self._make_site("exc-b")
		doc = frappe.get_doc(DT, self.item)
		for sid in ("exc-a", "exc-b"):
			doc.append("medusync_sites", {"site": sid})
		doc.save(ignore_permissions=True)
		selection.exclude(DT, self.item, site="exc-a", reason="ops", source="Manual")
		doc.reload()
		self.assertEqual([r.site for r in doc.medusync_sites], ["exc-b"])

	def test_the_two_directions_do_not_chase_each_other(self):
		# Writing the document from the exclusion hook fires the document
		# hook, which would write the exclusion again. One pass each.
		self._make_site("exc-a")
		selection.exclude(DT, self.item, site=None, reason="ops", source="Manual")
		self.assertEqual(len(self._exclusions()), 1)
		# a second identical request changes nothing
		selection.exclude(DT, self.item, site=None, reason="ops", source="Manual")
		self.assertEqual(len(self._exclusions()), 1)

	def test_the_doctype_refuses_a_duplicate(self):
		selection.exclude(DT, self.item, site=None, reason="ops", source="Manual")
		dup = frappe.new_doc("Medusync Exclusion")
		dup.update({"document_type": DT, "document_name": self.item, "source": "Manual"})
		with self.assertRaises(frappe.ValidationError):
			dup.insert(ignore_permissions=True)


class TestOutboundHonoursTheChoice(ExclusionCase):
	def test_a_deselected_site_gets_nothing(self):
		from unittest.mock import patch

		from medusync import outbound

		self._make_site("exc-a")
		self._make_site("exc-b")
		doc = frappe.get_doc(DT, self.item)
		doc.append("medusync_sites", {"site": "exc-a"})
		doc.save(ignore_permissions=True)

		sent = []
		with patch("medusync.outbound.send", side_effect=lambda *a, **k: sent.append(k)):
			outbound.emit(
				"inventory.level.set",
				{"sku": self.item, "quantity": 1},
				ref="selection",
				doctype=DT,
				docname=self.item,
			)
		targets = {k["site_id"] for k in sent}
		self.assertIn("exc-a", targets)
		self.assertNotIn("exc-b", targets)

	def test_an_unchecked_document_gets_nothing_at_all(self):
		from unittest.mock import patch

		from medusync import outbound

		self._make_site("exc-a")
		doc = frappe.get_doc(DT, self.item)
		doc.medusync_sync = 0
		doc.save(ignore_permissions=True)

		sent = []
		with patch("medusync.outbound.send", side_effect=lambda *a, **k: sent.append(k)):
			outbound.emit(
				"inventory.level.set",
				{"sku": self.item, "quantity": 1},
				ref="selection2",
				doctype=DT,
				docname=self.item,
			)
		self.assertEqual(sent, [])
