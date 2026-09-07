# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""A sync is its pair.

One Medusa entity and one DocType, per store, is one mapping — whichever
side created it and whatever it was called. The identity is derived from
the pair, so both systems agree which mapping is which without asking, and
a second mapping for the same pair is refused rather than becoming the
twin that made one sync look like two.
"""

from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import mapping_sync

MAPPING = "Medusync Mapping"
ENTITY = "probe_todo"


class PairCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._push = patch("medusync.mapping_sync.push_mapping", return_value=None)
		self._pushed = self._push.start()
		self._made = []
		for name in frappe.get_all(MAPPING, filters={"medusa_entity": ["like", "probe_%"]}, pluck="name"):
			frappe.delete_doc(MAPPING, name, force=1, ignore_permissions=True)

	def tearDown(self):
		for name in frappe.get_all(MAPPING, filters={"medusa_entity": ["like", "probe_%"]}, pluck="name"):
			frappe.delete_doc(MAPPING, name, force=1, ignore_permissions=True)
		self._push.stop()
		super().tearDown()

	def _new(self, title, doctype="ToDo", entity=ENTITY, insert=True, **over):
		doc = frappe.new_doc(MAPPING)
		doc.update(
			{
				"title": title,
				"enabled": 0,
				"document_type": doctype,
				"medusa_entity": entity,
				"direction": "Two-way",
				"key_field": "name",
			}
		)
		doc.update(over)
		doc.append("field_map", {"frappe_field": "description", "medusa_path": "title"})
		if insert:
			doc.insert(ignore_permissions=True)
		return doc


class TestTheIdentity(PairCase):
	def test_it_is_derived_from_the_pair(self):
		self.assertEqual(mapping_sync.pair_uid("order", "Sales Order"), "pair:order:sales_order")
		self.assertEqual(
			mapping_sync.pair_uid("order", "Sales Order", "shop-1"), "pair:order:sales_order:shop-1"
		)
		self.assertEqual(
			mapping_sync.pair_uid("invoice", "Sales Invoice (Return)"),
			"pair:invoice:sales_invoice_return",
		)

	def test_a_new_mapping_gets_the_pair_identity(self):
		doc = self._new("Pair Probe A")
		self.assertEqual(doc.mapping_uid, mapping_sync.pair_uid(ENTITY, "ToDo"))

	def test_a_second_mapping_for_the_same_pair_is_refused(self):
		self._new("Pair Probe A")
		with self.assertRaises(frappe.ValidationError) as caught:
			self._new("Pair Probe B")
		self.assertIn("Pair Probe A", str(caught.exception))

	def test_a_mapping_cannot_change_its_pair(self):
		doc = self._new("Pair Probe A")
		doc.document_type = "Note"
		doc.set("field_map", [])
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)


class TestTheOtherSideAgrees(PairCase):
	def test_a_legacy_identity_for_a_known_pair_lands_on_that_mapping(self):
		doc = self._new("Pair Probe A")
		canon = mapping_sync.to_canonical(doc)
		canon["uid"] = "legacy-random-identity"
		canon["version"] = int(doc.version) + 5
		canon["fields"].append({"erpnext_field": "status", "medusa_path": "state", "direction": "push"})

		result = mapping_sync.apply_canonical(canon)

		self.assertEqual(result["action"], "updated")
		self.assertEqual(result["name"], doc.name)
		self.assertEqual(frappe.db.count(MAPPING, {"medusa_entity": ENTITY, "document_type": "ToDo"}), 1)
		fresh = frappe.get_doc(MAPPING, doc.name)
		self.assertEqual(fresh.mapping_uid, mapping_sync.pair_uid(ENTITY, "ToDo"))
		self.assertIn("status", [r.frappe_field for r in fresh.field_map])

	def test_a_new_pair_arriving_under_a_random_identity_is_stored_under_the_pair(self):
		canon = {
			"uid": "random-from-the-store",
			"version": 1,
			"name": "Pair Probe Notes",
			"enabled": False,
			"medusa_entity": "probe_note",
			"doctype": "Note",
			"direction": "both",
			"key_medusa_field": "title",
			"key_erpnext_field": "title",
			"fields": [{"erpnext_field": "title", "medusa_path": "title", "direction": "both"}],
		}
		result = mapping_sync.apply_canonical(canon)
		self.assertEqual(result["action"], "created")
		self.assertEqual(
			frappe.db.get_value(MAPPING, result["name"], "mapping_uid"),
			mapping_sync.pair_uid("probe_note", "Note"),
		)


class TestFoldingTwins(PairCase):
	def test_two_mappings_for_one_pair_become_one_carrying_both_field_sets(self):
		keeper = self._new("Pair Probe A")
		twin = self._new("Pair Probe B", insert=False)
		twin.append("field_map", {"frappe_field": "status", "medusa_path": "state"})
		twin.mapping_uid = "legacy-twin"
		twin.version = 9
		# Straight in, past the rule this file adds: the rows this folds
		# are ones that already existed before the rule.
		twin.flags.ignore_validate = True
		twin.insert(ignore_permissions=True)

		report = mapping_sync.consolidate_pairs()

		pair = mapping_sync.pair_uid(ENTITY, "ToDo")
		rows = frappe.get_all(MAPPING, filters={"mapping_uid": pair}, fields=["name", "version"])
		self.assertEqual(len(rows), 1)
		self.assertEqual(frappe.db.count(MAPPING, {"medusa_entity": ENTITY, "document_type": "ToDo"}), 1)
		merged = frappe.get_doc(MAPPING, rows[0].name)
		# The higher version is the base; the other's fields are folded in.
		self.assertEqual(merged.name, twin.name)
		self.assertEqual(
			sorted(r.frappe_field for r in merged.field_map), ["description", "status"]
		)
		self.assertFalse(frappe.db.exists(MAPPING, keeper.name))
		# The site may hold twins of its own; only this pair is this test's.
		self.assertIn(pair, [m["pair"] for m in report["merged"]])


class TestTellingTheStoreEverything(PairCase):
	def test_push_all_sends_every_mapping(self):
		self._new("Pair Probe A")
		self._pushed.reset_mock()
		report = mapping_sync.push_all()
		self.assertEqual(self._pushed.call_count, frappe.db.count(MAPPING))
		self.assertEqual(report["pushed"], frappe.db.count(MAPPING))
