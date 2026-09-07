# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Mappings are one configuration living in two systems.

Each mapping carries a shared `mapping_uid` and a `version`. A change on
either side is pushed to the other as `mapping.upserted`. Conflicts
resolve by version, and on a tie ERPNext wins, because ERPNext owns which
documents are allowed to sync at all.
"""

from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import mapping_sync


def _mapping(title, **kw):
	if frappe.db.exists("Medusync Mapping", title):
		frappe.delete_doc("Medusync Mapping", title, force=1, ignore_permissions=True)
	doc = frappe.new_doc("Medusync Mapping")
	doc.update(
		{
			"title": title,
			"enabled": 0,
			"document_type": kw.pop("document_type", "Customer"),
			"direction": kw.pop("direction", "Two-way"),
			"key_field": kw.pop("key_field", "email_id"),
			"medusa_entity": kw.pop("medusa_entity", "probe_customer"),
		}
	)
	fields = kw.pop("fields", [{"frappe_field": "email_id", "medusa_path": "email", "direction": "Two-way"}])
	doc.update(kw)
	for row in fields:
		doc.append("field_map", row)
	doc.insert(ignore_permissions=True)
	# Switching a mapping on now needs a rehearsal that matches it (see
	# medusync.studio). These fixtures are not about that gate, so they
	# record a pass for the shape they just built and set the flag straight
	# in the table — saving again would bump `version`, which one of these
	# tests checks.
	from medusync import studio

	studio.record_result(doc.name, passed=True, report="fixture")
	frappe.db.set_value("Medusync Mapping", doc.name, "enabled", 1, update_modified=False)
	doc.reload()
	return doc


class TestMappingIdentity(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._made = []

	def tearDown(self):
		for name in self._made:
			if frappe.db.exists("Medusync Mapping", name):
				frappe.delete_doc("Medusync Mapping", name, force=1, ignore_permissions=True)
		super().tearDown()

	def _make(self, title, **kw):
		doc = _mapping(title, **kw)
		self._made.append(doc.name)
		return doc

	def test_new_mapping_gets_a_uid_and_version_one(self):
		doc = self._make("T Identity")
		self.assertTrue(doc.mapping_uid)
		self.assertEqual(doc.version, 1)

	def test_saving_a_change_bumps_the_version(self):
		doc = self._make("T Version")
		uid = doc.mapping_uid
		doc.key_field = "name"
		doc.save(ignore_permissions=True)
		doc.reload()
		self.assertEqual(doc.version, 2)
		self.assertEqual(doc.mapping_uid, uid)


class TestCanonicalForm(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._made = []

	def tearDown(self):
		for name in self._made:
			if frappe.db.exists("Medusync Mapping", name):
				frappe.delete_doc("Medusync Mapping", name, force=1, ignore_permissions=True)
		super().tearDown()

	def _make(self, title, **kw):
		doc = _mapping(title, **kw)
		self._made.append(doc.name)
		return doc

	def test_direction_translates_both_ways(self):
		self.assertEqual(mapping_sync.direction_to_canonical("Two-way"), "both")
		self.assertEqual(mapping_sync.direction_to_canonical("To Medusa"), "pull")
		self.assertEqual(mapping_sync.direction_to_canonical("From Medusa"), "push")
		self.assertEqual(mapping_sync.direction_from_canonical("both"), "Two-way")
		self.assertEqual(mapping_sync.direction_from_canonical("pull"), "To Medusa")
		self.assertEqual(mapping_sync.direction_from_canonical("push"), "From Medusa")

	def test_field_direction_translates_both_ways_including_dont_sync(self):
		self.assertEqual(mapping_sync.field_direction_to_canonical("Don't Sync"), "none")
		self.assertEqual(mapping_sync.field_direction_from_canonical("none"), "Don't Sync")
		self.assertEqual(mapping_sync.field_direction_to_canonical("Two-way"), "both")
		self.assertEqual(mapping_sync.field_direction_from_canonical("push"), "From Medusa")

	def test_canonical_round_trip(self):
		doc = self._make(
			"T Canonical",
			direction="From Medusa",
			fields=[
				{"frappe_field": "email_id", "medusa_path": "email", "direction": "Two-way"},
				{"frappe_field": "customer_name", "medusa_path": "first_name", "direction": "From Medusa"},
				{"frappe_field": "image", "medusa_path": "thumbnail", "direction": "Don't Sync"},
			],
		)
		canon = mapping_sync.to_canonical(doc)
		self.assertEqual(canon["uid"], doc.mapping_uid)
		self.assertEqual(canon["version"], doc.version)
		self.assertEqual(canon["doctype"], "Customer")
		self.assertEqual(canon["medusa_entity"], "probe_customer")
		self.assertEqual(canon["direction"], "push")
		self.assertEqual(canon["key_erpnext_field"], "email_id")
		self.assertEqual(len(canon["fields"]), 3)
		self.assertEqual(canon["fields"][2]["direction"], "none")
		self.assertEqual(canon["fields"][2]["erpnext_field"], "image")


class TestConflictResolution(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._made = []

	def tearDown(self):
		for name in self._made:
			if frappe.db.exists("Medusync Mapping", name):
				frappe.delete_doc("Medusync Mapping", name, force=1, ignore_permissions=True)
		super().tearDown()

	def _make(self, title, **kw):
		doc = _mapping(title, **kw)
		self._made.append(doc.name)
		return doc

	def test_an_unknown_uid_creates_the_mapping(self):
		canon = {
			"uid": "uid-new-1",
			"version": 1,
			"name": "T Inbound New",
			"enabled": True,
			"medusa_entity": "probe_customer",
			"doctype": "Customer",
			"direction": "both",
			"key_medusa_field": "email",
			"key_erpnext_field": "email_id",
			"fields": [{"medusa_path": "email", "erpnext_field": "email_id", "direction": "both"}],
		}
		res = mapping_sync.apply_canonical(canon)
		self.assertEqual(res["action"], "created")
		self._made.append(res["name"])
		doc = frappe.get_doc("Medusync Mapping", res["name"])
		# Stored under the pair's own identity, whatever the sender called it.
		self.assertEqual(doc.mapping_uid, mapping_sync.pair_uid("probe_customer", "Customer"))
		self.assertEqual(doc.document_type, "Customer")
		self.assertEqual(len(doc.field_map), 1)
		# First contact must not switch on a rule nobody reviewed here.
		self.assertEqual(doc.enabled, 0)
		self.assertEqual(res["reason"], "created_disabled")

	def test_an_update_to_a_known_uid_applies_enabled_as_sent(self):
		doc = self._make("T Known Enable")
		doc.enabled = 0
		doc.flags.medusync_applying = True
		doc.save(ignore_permissions=True)
		canon = mapping_sync.to_canonical(doc)
		canon["version"] = doc.version + 1
		canon["enabled"] = True
		res = mapping_sync.apply_canonical(canon)
		self.assertEqual(res["action"], "updated")
		doc.reload()
		self.assertEqual(doc.enabled, 1)

	def test_a_higher_version_wins(self):
		doc = self._make("T Higher")
		canon = mapping_sync.to_canonical(doc)
		canon["version"] = doc.version + 1
		canon["key_erpnext_field"] = "name"
		res = mapping_sync.apply_canonical(canon)
		self.assertEqual(res["action"], "updated")
		doc.reload()
		self.assertEqual(doc.key_field, "name")

	def test_a_lower_version_is_refused(self):
		doc = self._make("T Lower")
		doc.key_field = "name"
		doc.save(ignore_permissions=True)  # version 2
		canon = mapping_sync.to_canonical(doc)
		canon["version"] = 1
		canon["key_erpnext_field"] = "email_id"
		res = mapping_sync.apply_canonical(canon)
		self.assertEqual(res["action"], "skipped")
		self.assertEqual(res["reason"], "stale_version")
		doc.reload()
		self.assertEqual(doc.key_field, "name")

	def test_an_equal_version_is_refused_because_erpnext_wins_the_tie(self):
		doc = self._make("T Tie")
		canon = mapping_sync.to_canonical(doc)
		canon["key_erpnext_field"] = "name"  # same version, different content
		res = mapping_sync.apply_canonical(canon)
		self.assertEqual(res["action"], "skipped")
		self.assertEqual(res["reason"], "tie_erpnext_wins")
		doc.reload()
		self.assertEqual(doc.key_field, "email_id")

	def test_applying_a_mapping_does_not_push_it_back(self):
		canon = {
			"uid": "uid-no-echo",
			"version": 1,
			"name": "T No Echo",
			"enabled": True,
			"medusa_entity": "probe_customer",
			"doctype": "Customer",
			"direction": "both",
			"key_medusa_field": "email",
			"key_erpnext_field": "email_id",
			"fields": [],
		}
		res = mapping_sync.apply_canonical(canon)
		self._made.append(res["name"])
		doc = frappe.get_doc("Medusync Mapping", res["name"])
		# the version must be the one that arrived, not bumped by our own save
		self.assertEqual(doc.version, 1)

	def test_delete_removes_the_local_copy(self):
		# This used to disable rather than delete, which left a switched-off
		# twin behind for every mapping removed on the other side.
		doc = self._make("T Delete")
		res = mapping_sync.apply_deleted(doc.mapping_uid)
		self.assertEqual(res["action"], "deleted")
		self.assertFalse(frappe.db.exists("Medusync Mapping", res["name"]))


class TestDeletingPropagates(IntegrationTestCase):
	"""A mapping removed on one side is removed on the other.

	It used to be disabled instead, which left a switched-off twin behind
	for every deletion — one configuration showing up as two.
	"""

	UID = "uid-delete-propagation"
	TITLE = "T Delete Propagation"

	def tearDown(self):
		if frappe.db.exists("Medusync Mapping", self.TITLE):
			frappe.delete_doc("Medusync Mapping", self.TITLE, force=1, ignore_permissions=True)
		super().tearDown()

	def _make(self):
		doc = frappe.new_doc("Medusync Mapping")
		doc.update(
			{
				"title": self.TITLE,
				"enabled": 0,
				"document_type": "Customer",
				"direction": "Two-way",
				"key_field": "name",
				"medusa_entity": "probe_customer",
				"mapping_uid": self.UID,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	def test_a_deletion_from_the_other_side_removes_it(self):
		# The other side names the identity both copies share: the pair's.
		doc = self._make()
		result = mapping_sync.apply_deleted(doc.mapping_uid)
		self.assertEqual(result["action"], "deleted")
		self.assertFalse(frappe.db.exists("Medusync Mapping", self.TITLE))

	def test_a_deletion_for_a_uid_we_never_had_is_not_an_error(self):
		result = mapping_sync.apply_deleted("uid-we-never-saw")
		self.assertEqual(result["action"], "skipped")
		self.assertEqual(result["reason"], "already_absent")

	def test_applying_a_deletion_does_not_echo_it_back(self):
		# Without the guard the trash hook would push mapping.deleted out
		# again, and the two sides would bounce the deletion between them.
		self._make()
		sent = []
		with patch.object(mapping_sync, "push_mapping", lambda *a, **k: sent.append(a)):
			mapping_sync.apply_deleted(self.UID)
		self.assertEqual(sent, [])
		self.assertFalse(frappe.flags.get("medusync_applying"))
