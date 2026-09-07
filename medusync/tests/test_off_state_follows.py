# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""A mapping runs only when both sides have it on.

Switched off anywhere, it is off everywhere, and the other side is told
why. Switching on still needs each side's own rehearsal; a side that
declines a remote enable says so, one version up, so the side that asked
turns it off too rather than believing it runs.
"""

from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import attention, mapping_sync, studio

MAPPING = "Medusync Mapping"
ENTITY = "probe_offstate"


class OffStateCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._push = patch("medusync.mapping_sync.push_mapping", return_value=None)
		self._pushed = self._push.start()
		self._clean()

	def tearDown(self):
		self._clean()
		self._push.stop()
		super().tearDown()

	def _clean(self):
		for name in frappe.get_all(MAPPING, filters={"medusa_entity": ENTITY}, pluck="name"):
			frappe.delete_doc(MAPPING, name, force=1, ignore_permissions=True)

	def _mapping(self, enabled=False):
		doc = frappe.new_doc(MAPPING)
		doc.update(
			{
				"title": "Off State Probe",
				"enabled": 0,
				"document_type": "ToDo",
				"medusa_entity": ENTITY,
				"direction": "Two-way",
				"key_field": "name",
				"docevents": "on_update",
			}
		)
		doc.append("field_map", {"frappe_field": "description", "medusa_path": "title"})
		doc.insert(ignore_permissions=True)
		if enabled:
			studio.record_result(doc.name, passed=True, report="fixture")
			frappe.db.set_value(MAPPING, doc.name, "enabled", 1, update_modified=False)
			doc.reload()
		return doc


class TestOffTravels(OffStateCase):
	def test_the_reason_travels_only_when_it_is_off(self):
		doc = self._mapping(enabled=True)
		self.assertNotIn("attention", mapping_sync.to_canonical(doc))
		frappe.db.set_value(
			MAPPING,
			doc.name,
			{"enabled": 0, "attention": "Field Missing", "attention_detail": "ToDo lost a field"},
			update_modified=False,
		)
		doc.reload()
		canon = mapping_sync.to_canonical(doc)
		self.assertEqual(canon["attention"], "Field Missing")
		self.assertEqual(canon["attention_detail"], "ToDo lost a field")

	def test_switched_off_over_there_is_off_here_and_says_why(self):
		doc = self._mapping(enabled=True)
		canon = mapping_sync.to_canonical(doc)
		canon["enabled"] = False
		canon["version"] = int(doc.version) + 1
		canon["attention"] = "Field Missing"
		canon["attention_detail"] = "the store lost a column"

		result = mapping_sync.apply_canonical(canon)

		self.assertEqual(result["action"], "updated")
		fresh = frappe.get_doc(MAPPING, doc.name)
		self.assertEqual(fresh.enabled, 0)
		self.assertEqual(fresh.attention, "Field Missing")
		self.assertEqual(fresh.attention_detail, "the store lost a column")

	def test_a_local_disable_for_a_missing_field_is_announced(self):
		doc = self._mapping(enabled=True)
		self._pushed.reset_mock()
		attention.flag(doc.name, attention.FIELD_MISSING, "gone", disable=True)
		self.assertEqual(frappe.db.get_value(MAPPING, doc.name, "enabled"), 0)
		self.assertEqual(self._pushed.call_count, 1)
		announced = self._pushed.call_args.args[0]
		self.assertEqual(announced.enabled, 0)
		self.assertGreater(int(announced.version), int(doc.version))


class TestADeclinedEnableIsAnswered(OffStateCase):
	def test_an_enable_this_side_has_not_rehearsed_is_answered_with_off(self):
		doc = self._mapping(enabled=False)
		canon = mapping_sync.to_canonical(doc)
		canon["enabled"] = True
		canon["version"] = int(doc.version) + 3
		self._pushed.reset_mock()

		result = mapping_sync.apply_canonical(canon)

		self.assertEqual(result["action"], "declined")
		fresh = frappe.get_doc(MAPPING, doc.name)
		self.assertEqual(fresh.enabled, 0)
		self.assertEqual(fresh.attention, "Mapping Required")
		self.assertEqual(int(fresh.version), int(doc.version) + 4)
		self.assertEqual(self._pushed.call_count, 1)
		self.assertEqual(self._pushed.call_args.args[0].enabled, 0)

	def test_an_enable_this_side_has_rehearsed_is_taken_and_clears_the_flag(self):
		doc = self._mapping(enabled=False)
		studio.record_result(doc.name, passed=True, report="fixture")
		frappe.db.set_value(
			MAPPING, doc.name, {"attention": "Mapping Required", "attention_detail": "old"}, update_modified=False
		)
		doc.reload()
		canon = mapping_sync.to_canonical(doc)
		canon["enabled"] = True
		canon["version"] = int(doc.version) + 1
		self._pushed.reset_mock()

		result = mapping_sync.apply_canonical(canon)

		self.assertEqual(result["action"], "updated")
		fresh = frappe.get_doc(MAPPING, doc.name)
		self.assertEqual(fresh.enabled, 1)
		self.assertFalse(fresh.attention)
		self.assertEqual(self._pushed.call_count, 0)
