# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""A mapping that was fine last year and names a field that is gone.

ERPNext moves. A customisation is removed, an app is uninstalled, a field
is renamed, and a mapping that has worked for a year starts referring to
something that does not exist. Nothing fails loudly: the payload simply
stops carrying that field, or the inbound write silently drops it.

So the drift check runs on a schedule and after every migrate, and says
so. It switches off the mapping it found, because that one genuinely
cannot do what it says — and only that one. An upgrade that stopped a
whole site over one stale mapping is an upgrade nobody runs twice.
"""

from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import drift

MAPPING = "Medusync Mapping"


class DriftCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._push = patch("medusync.mapping_sync.push_mapping", return_value=None)
		self._push.start()
		self._notify = patch("medusync.drift.notify_attention", return_value=None)
		self._notified = self._notify.start()
		self._made = []

	def tearDown(self):
		for name in self._made:
			if frappe.db.exists(MAPPING, name):
				frappe.delete_doc(MAPPING, name, force=1, ignore_permissions=True)
		# `drift.check` commits, as a scheduled job should, so these
		# fixtures are durable by the time the test ends and the usual
		# rollback would undo the cleanup rather than the fixtures.
		frappe.db.commit()
		self._notify.stop()
		self._push.stop()
		super().tearDown()

	def _mapping(self, fields, doctype="Customer", enabled=True):
		doc = frappe.new_doc(MAPPING)
		doc.update(
			{
				"title": "Drift probe %s" % frappe.generate_hash(length=6),
				"enabled": 0,
				"document_type": doctype,
				"direction": "To Medusa",
				"docevents": "on_update",
				"key_field": "name",
			}
		)
		for frappe_field, medusa_path in fields:
			doc.append("field_map", {"frappe_field": frappe_field, "medusa_path": medusa_path})
		# Bypass validation, which is the point: these mappings were valid
		# when they were written and the DocType moved underneath them.
		# `ignore_links` matters as much as `ignore_validate` — the real
		# scenario is a DocType that existed at insert time and does not
		# now, which no fixture can reproduce without uninstalling an app.
		doc.flags.ignore_validate = True
		doc.insert(ignore_permissions=True, ignore_mandatory=True, ignore_links=True)
		self._made.append(doc.name)
		if enabled:
			frappe.db.set_value(MAPPING, doc.name, "enabled", 1, update_modified=False)
		return doc


class TestFindingIt(DriftCase):
	def test_a_field_that_still_exists_is_fine(self):
		doc = self._mapping([("customer_name", "name")])
		report = drift.check()
		self.assertNotIn(doc.name, [m["name"] for m in report["flagged"]])
		doc.reload()
		self.assertEqual(doc.attention, "")
		self.assertTrue(doc.enabled)

	def test_a_field_that_is_gone_is_found(self):
		doc = self._mapping([("customer_name", "name"), ("a_field_nobody_has", "x")])
		report = drift.check()
		self.assertIn(doc.name, [m["name"] for m in report["flagged"]])
		doc.reload()
		self.assertEqual(doc.attention, "Field Missing")
		self.assertIn("a_field_nobody_has", doc.attention_detail)

	def test_the_mapping_it_found_stops(self):
		# This one really cannot do what it says.
		doc = self._mapping([("a_field_nobody_has", "x")])
		drift.check()
		doc.reload()
		self.assertFalse(doc.enabled)

	def test_every_other_mapping_keeps_running(self):
		broken = self._mapping([("a_field_nobody_has", "x")])
		fine = self._mapping([("customer_name", "name")])
		drift.check()
		self.assertFalse(frappe.db.get_value(MAPPING, broken.name, "enabled"))
		self.assertTrue(frappe.db.get_value(MAPPING, fine.name, "enabled"))

	def test_a_doctype_that_is_gone_is_the_same_problem(self):
		doc = self._mapping([("whatever", "x")], doctype="Some Uninstalled DocType")
		drift.check()
		doc.reload()
		self.assertEqual(doc.attention, "Field Missing")
		self.assertFalse(doc.enabled)

	def test_a_mapping_already_switched_off_is_left_alone(self):
		# Nothing is at risk, and flagging it would fill the list with
		# mappings somebody already retired.
		doc = self._mapping([("a_field_nobody_has", "x")], enabled=False)
		report = drift.check()
		self.assertNotIn(doc.name, [m["name"] for m in report["flagged"]])

	def test_send_all_fields_has_no_field_map_to_drift(self):
		doc = self._mapping([])
		frappe.db.set_value(MAPPING, doc.name, "include_all_fields", 1, update_modified=False)
		report = drift.check()
		self.assertNotIn(doc.name, [m["name"] for m in report["flagged"]])


class TestTellingSomebody(DriftCase):
	def test_somebody_is_told_once(self):
		doc = self._mapping([("a_field_nobody_has", "x")])
		drift.check()
		self.assertEqual(self._notified.call_count, 1)

	def test_and_not_told_again_about_the_same_thing(self):
		# The check runs daily. Repeating yesterday's news every morning is
		# how a notification becomes something people filter out.
		doc = self._mapping([("a_field_nobody_has", "x")])
		drift.check()
		self._notified.reset_mock()
		drift.check()
		self.assertEqual(self._notified.call_count, 0)


class TestItNeverBreaksTheUpgrade(DriftCase):
	def test_a_broken_mapping_does_not_raise(self):
		self._mapping([("a_field_nobody_has", "x")], doctype="Some Uninstalled DocType")
		# Called from a patch on every migrate. An operator whose site is
		# down after an upgrade because one mapping went stale does not
		# upgrade again.
		self.assertIsInstance(drift.check(), dict)

	def test_even_when_the_lookup_itself_explodes(self):
		self._mapping([("customer_name", "name")])
		with patch("medusync.drift._missing_fields", side_effect=RuntimeError("boom")):
			report = drift.check()
		self.assertIsInstance(report, dict)
		self.assertTrue(report["errors"])
