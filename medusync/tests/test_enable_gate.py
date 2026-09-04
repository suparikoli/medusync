# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""A mapping goes live only after somebody has seen it work.

"Enable after a successful test" sounds like a nicety until you watch an
untested mapping enabled on a Friday quietly rewrite a field on every
customer over the weekend. The gate is cheap and the failure it prevents
is not.

What counts as "this mapping" is a signature over the parts that decide
what the mapping *does* — the doctype, the direction, the key, the field
map. Not the version number, which changes on every save including the
one that only ticks a checkbox, and not the title, which changes nothing.
So a passed test survives being enabled, and does not survive somebody
adding a field afterwards.
"""

from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import studio


class GateCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._push = patch("medusync.mapping_sync.push_mapping", return_value=None)
		self._push.start()
		self._mappings = []

	def tearDown(self):
		for name in self._mappings:
			if frappe.db.exists("Medusync Mapping", name):
				frappe.delete_doc("Medusync Mapping", name, force=1, ignore_permissions=True)
		self._push.stop()
		super().tearDown()

	def _mapping(self, **over):
		spec = {
			"title": "Gate test mapping",
			"enabled": 0,
			"document_type": "Customer",
			"direction": "Two-way",
			"docevents": "on_update",
			"key_field": "customer_name",
			"allow_insert": 1,
			"allow_update": 1,
		}
		spec.update(over)
		field_map = spec.pop("field_map", [{"frappe_field": "customer_name", "medusa_path": "name"}])
		doc = frappe.new_doc("Medusync Mapping")
		doc.update(spec)
		for row in field_map:
			doc.append("field_map", row)
		doc.insert(ignore_permissions=True)
		self._mappings.append(doc.name)
		return doc

	def _pass_the_test(self, mapping):
		"""Record a green run the way the studio does."""
		studio.record_result(mapping.name, passed=True, report="ok")
		frappe.clear_cache(doctype="Medusync Mapping")


class TestTheGate(GateCase):
	def test_an_untested_mapping_cannot_be_switched_on(self):
		mapping = self._mapping()
		mapping.enabled = 1
		with self.assertRaises(frappe.ValidationError):
			mapping.save(ignore_permissions=True)

	def test_a_tested_one_can(self):
		mapping = self._mapping()
		self._pass_the_test(mapping)
		mapping = frappe.get_doc("Medusync Mapping", mapping.name)
		mapping.enabled = 1
		mapping.save(ignore_permissions=True)
		self.assertTrue(frappe.db.get_value("Medusync Mapping", mapping.name, "enabled"))

	def test_a_failed_test_is_not_a_pass(self):
		mapping = self._mapping()
		studio.record_result(mapping.name, passed=False, report="the condition never matches")
		frappe.clear_cache(doctype="Medusync Mapping")
		mapping = frappe.get_doc("Medusync Mapping", mapping.name)
		mapping.enabled = 1
		with self.assertRaises(frappe.ValidationError):
			mapping.save(ignore_permissions=True)

	def test_changing_what_it_does_puts_it_back_behind_the_gate(self):
		mapping = self._mapping()
		self._pass_the_test(mapping)
		mapping = frappe.get_doc("Medusync Mapping", mapping.name)
		mapping.append("field_map", {"frappe_field": "customer_type", "medusa_path": "kind"})
		mapping.save(ignore_permissions=True)
		mapping.enabled = 1
		with self.assertRaises(frappe.ValidationError):
			mapping.save(ignore_permissions=True)

	def test_renaming_it_does_not(self):
		# The title is documentation. It changes nothing about behaviour and
		# must not cost the operator a re-test.
		mapping = self._mapping()
		self._pass_the_test(mapping)
		mapping = frappe.get_doc("Medusync Mapping", mapping.name)
		mapping.title = "Gate test mapping, renamed"
		mapping.enabled = 1
		mapping.save(ignore_permissions=True)
		self.assertTrue(frappe.db.get_value("Medusync Mapping", mapping.name, "enabled"))

	def test_a_mapping_already_running_is_left_alone(self):
		# The gate is about switching one ON. Retro-fitting it to running
		# mappings would stop a working site on the next save of anything.
		mapping = self._mapping()
		self._pass_the_test(mapping)
		mapping = frappe.get_doc("Medusync Mapping", mapping.name)
		mapping.enabled = 1
		mapping.save(ignore_permissions=True)
		mapping.append("field_map", {"frappe_field": "customer_type", "medusa_path": "kind"})
		mapping.save(ignore_permissions=True)  # must not raise
		self.assertTrue(frappe.db.get_value("Medusync Mapping", mapping.name, "enabled"))

	def test_recording_a_result_does_not_count_as_an_edit(self):
		# If the test bumped the version or the signature it just approved,
		# the gate could never be satisfied.
		mapping = self._mapping()
		before = frappe.db.get_value("Medusync Mapping", mapping.name, "version")
		self._pass_the_test(mapping)
		after = frappe.db.get_value("Medusync Mapping", mapping.name, "version")
		self.assertEqual(before, after)


class TestAnEnableFromTheOtherSide(GateCase):
	def test_the_far_side_cannot_switch_an_untested_mapping_on_here(self):
		# The same rule as first contact: nothing runs here until somebody
		# here has looked at it.
		mapping = self._mapping()
		doc = frappe.get_doc("Medusync Mapping", mapping.name)
		doc.flags.medusync_applying = True
		doc.enabled = 1
		doc.save(ignore_permissions=True)  # must not raise — a 5xx would retry forever
		self.assertFalse(frappe.db.get_value("Medusync Mapping", mapping.name, "enabled"))

	def test_it_can_still_change_everything_else(self):
		mapping = self._mapping()
		doc = frappe.get_doc("Medusync Mapping", mapping.name)
		doc.flags.medusync_applying = True
		doc.enabled = 1
		doc.key_field = "name"
		doc.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("Medusync Mapping", mapping.name, "key_field"), "name")


class TestTestAndEnable(GateCase):
	def test_a_green_run_records_the_pass_and_turns_it_on(self):
		mapping = self._mapping()
		result = studio.test_and_enable(mapping.name)
		self.assertTrue(result["passed"])
		self.assertTrue(frappe.db.get_value("Medusync Mapping", mapping.name, "enabled"))
		self.assertEqual(
			frappe.db.get_value("Medusync Mapping", mapping.name, "last_test_status"), "Passed"
		)

	def test_a_red_run_records_the_failure_and_leaves_it_off(self):
		existing = frappe.get_all("Customer", fields=["name"], limit=1)[0].name
		mapping = self._mapping(
			field_map=[
				{"frappe_field": "customer_name", "medusa_path": "name"},
				{"frappe_field": "customer_type", "medusa_path": "kind"},
			]
		)
		result = studio.test_and_enable(
			mapping.name,
			sample={"name": existing, "customer_name": "Whoever", "kind": "Not A Real Type"},
		)
		self.assertFalse(result["passed"])
		self.assertFalse(frappe.db.get_value("Medusync Mapping", mapping.name, "enabled"))
		self.assertEqual(
			frappe.db.get_value("Medusync Mapping", mapping.name, "last_test_status"), "Failed"
		)
		self.assertTrue(frappe.db.get_value("Medusync Mapping", mapping.name, "last_test_report"))

	def test_the_report_says_what_ran(self):
		mapping = self._mapping()
		result = studio.test_and_enable(mapping.name)
		self.assertIn("outbound", result)
		self.assertIn("inbound", result)
