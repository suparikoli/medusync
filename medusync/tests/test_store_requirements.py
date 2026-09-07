# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""A sync that sends to the store is refused until it fills what the
store insists on.

The store says which fields it cannot create a record without. A mapping
that leaves one unmapped used to rehearse green here and then fail on the
first real record, with the store's refusal buried in a log row.
"""

from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import studio

MAPPING = "Medusync Mapping"
ENTITY = "probe_storereq"
STORE_SAYS = {"fields": [{"path": "title", "label": "Title", "required": True}, {"path": "notes", "label": "Notes"}]}


class StoreRequirementsCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._push = patch("medusync.mapping_sync.push_mapping", return_value=None)
		self._push.start()
		self._fields = patch("medusync.medusa_fields.fields", return_value=STORE_SAYS)
		self._fields.start()
		self._clean()

	def tearDown(self):
		self._clean()
		self._fields.stop()
		self._push.stop()
		super().tearDown()

	def _clean(self):
		for name in frappe.get_all(MAPPING, filters={"medusa_entity": ENTITY}, pluck="name"):
			frappe.delete_doc(MAPPING, name, force=1, ignore_permissions=True)

	def _mapping(self, pairs, direction="To Medusa"):
		doc = frappe.new_doc(MAPPING)
		doc.update(
			{
				"title": "Store Requirements Probe",
				"enabled": 0,
				"document_type": "ToDo",
				"medusa_entity": ENTITY,
				"direction": direction,
				"key_field": "name",
				"docevents": "on_update",
			}
		)
		for row in pairs:
			doc.append("field_map", row)
		doc.insert(ignore_permissions=True)
		return doc


class TestTheStoresQuestion(StoreRequirementsCase):
	def test_a_store_required_field_nobody_sends_fails_the_rehearsal(self):
		doc = self._mapping([{"frappe_field": "status", "medusa_path": "notes"}])
		report = studio.run(doc.name)
		self.assertFalse(report["passed"])
		self.assertTrue(any("Title" in e for e in report["errors"]))

	def test_a_pair_that_sends_it_satisfies_the_store(self):
		doc = self._mapping([{"frappe_field": "description", "medusa_path": "title"}])
		report = studio.run(doc.name)
		self.assertTrue(report["passed"], report["errors"])

	def test_a_pair_that_only_receives_it_does_not_count(self):
		doc = self._mapping(
			[{"frappe_field": "description", "medusa_path": "title", "direction": "From Medusa"}],
			direction="Two-way",
		)
		report = studio.run(doc.name)
		self.assertTrue(any("Title" in e for e in report["errors"]))

	def test_a_mapping_that_never_sends_is_not_asked(self):
		doc = self._mapping([{"frappe_field": "status", "medusa_path": "notes"}], direction="From Medusa")
		report = studio.run(doc.name)
		self.assertFalse(any("Title" in e for e in report["errors"]))

	def test_a_store_that_cannot_be_reached_is_a_warning_not_a_pass(self):
		self._fields.stop()
		with patch("medusync.medusa_fields.fields", side_effect=Exception("no route to store")):
			doc = self._mapping([{"frappe_field": "description", "medusa_path": "title"}])
			report = studio.run(doc.name)
		self._fields.start()
		self.assertTrue(any("could not read" in w.lower() for w in report["warnings"]))
