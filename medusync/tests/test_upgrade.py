# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Shipping a new default set to a site that has been running for a year.

The rule the brief is explicit about: new defaults never auto-replace what
somebody has configured. Which needs a definition of "somebody has
configured it", and there already is one — the signature over what a
mapping does, the same fingerprint the enable gate and the studio use. A
default whose signature still matches what shipped has never been touched
and may be replaced. One that differs was edited, and the upgrade says so
instead of overwriting it.

The other half is drift: a mapping that was fine last year and now names a
field the DocType no longer has. That one really cannot work, so it is
switched off — but only it. An upgrade that stopped a site because one
mapping went stale is an upgrade nobody runs twice.
"""

from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import defaults

MAPPING = "Medusync Mapping"


class UpgradeCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._push = patch("medusync.mapping_sync.push_mapping", return_value=None)
		self._push.start()
		self._notify = patch("medusync.defaults.notify_attention", return_value=None)
		self._notified = self._notify.start()
		self._extra = []
		self._before_version = frappe.db.get_single_value("Medusync Settings", "defaults_version")
		self._known = {
			row.mapping_uid for row in frappe.get_all(MAPPING, fields=["mapping_uid"]) if row.mapping_uid
		}

	def tearDown(self):
		for name in self._extra:
			if frappe.db.exists(MAPPING, name):
				frappe.delete_doc(MAPPING, name, force=1, ignore_permissions=True)
		for row in frappe.get_all(MAPPING, fields=["name", "mapping_uid"]):
			if defaults.owns(row.mapping_uid) and row.mapping_uid not in self._known:
				if frappe.db.exists(MAPPING, row.name):
					frappe.delete_doc(MAPPING, row.name, force=1, ignore_permissions=True)
		frappe.db.set_single_value("Medusync Settings", "defaults_version", self._before_version)
		self._notify.stop()
		self._push.stop()
		super().tearDown()

	def _installed(self, uid):
		name = frappe.db.get_value(MAPPING, {"mapping_uid": uid}, "name")
		return frappe.get_doc(MAPPING, name) if name else None

	def _first_default(self):
		return defaults.default_mappings()[0]


class TestAFreshSite(UpgradeCase):
	def test_it_gets_the_whole_set(self):
		for row in frappe.get_all(MAPPING, fields=["name", "mapping_uid"]):
			if defaults.owns(row.mapping_uid):
				frappe.delete_doc(MAPPING, row.name, force=1, ignore_permissions=True)

		result = defaults.apply_defaults()
		self.assertEqual(len(result["created"]), len(defaults.default_mappings()))
		self.assertEqual(result["flagged"], [])

	def test_and_records_the_version_it_got(self):
		result = defaults.apply_defaults()
		self.assertEqual(result["version"], defaults.DEFAULTS_VERSION)
		self.assertEqual(
			frappe.db.get_single_value("Medusync Settings", "defaults_version"),
			defaults.DEFAULTS_VERSION,
		)


class TestASiteThatHasNotTouchedThem(UpgradeCase):
	def setUp(self):
		super().setUp()
		defaults.restore_defaults()

	def test_running_the_upgrade_again_changes_nothing(self):
		# The commonest case by far: somebody upgrades and has never opened
		# a mapping. It has to be silent, not "3 mappings updated".
		result = defaults.apply_defaults()
		self.assertEqual(result["created"], [])
		self.assertEqual(result["flagged"], [])
		self.assertEqual(len(result["applied"]), 0)
		self.assertEqual(len(result["unchanged"]), len(defaults.default_mappings()))

	def test_a_default_that_drifted_from_what_shipped_is_replaced(self):
		# Not edited: the SHIPPED set moved. The mapping still carries the
		# fingerprint of the old shipped shape, so nobody has touched it and
		# it is ours to update.
		spec = self._first_default()
		doc = self._installed(spec["uid"])
		doc.key_field = "name"
		doc.save(ignore_permissions=True)
		# Pretend this is exactly what the previous version shipped.
		frappe.db.set_value(
			MAPPING, doc.name, "shipped_signature", doc.test_signature(), update_modified=False
		)
		frappe.clear_cache(doctype=MAPPING)

		result = defaults.apply_defaults()
		self.assertIn(doc.name, [m["name"] for m in result["applied"]])
		doc.reload()
		self.assertEqual(doc.key_field, spec["key_field"])
		self.assertEqual(doc.attention, "")


class TestASiteThatHasEditedThem(UpgradeCase):
	def setUp(self):
		super().setUp()
		defaults.restore_defaults()
		self.spec = self._first_default()
		self.doc = self._installed(self.spec["uid"])
		self.doc.append("field_map", {"frappe_field": "customer_group", "medusa_path": "group"})
		self.doc.save(ignore_permissions=True)

	def test_the_edit_survives_the_upgrade(self):
		defaults.apply_defaults()
		self.doc.reload()
		fields = {row.frappe_field for row in self.doc.field_map}
		self.assertIn("customer_group", fields)

	def test_and_the_mapping_is_told_to_look(self):
		result = defaults.apply_defaults()
		self.assertIn(self.doc.name, [m["name"] for m in result["flagged"]])
		self.doc.reload()
		self.assertEqual(self.doc.attention, "Mapping Required")
		self.assertTrue(self.doc.attention_detail)

	def test_it_keeps_running(self):
		# An upgrade that switched off a mapping somebody had working, over
		# a difference of opinion about defaults, is an upgrade that broke
		# the site. Being told is enough.
		frappe.db.set_value(MAPPING, self.doc.name, "enabled", 1, update_modified=False)
		defaults.apply_defaults()
		self.assertTrue(frappe.db.get_value(MAPPING, self.doc.name, "enabled"))

	def test_somebody_is_told(self):
		defaults.apply_defaults()
		self.assertTrue(self._notified.called)

	def test_the_version_waits_until_nothing_is_outstanding(self):
		frappe.db.set_single_value("Medusync Settings", "defaults_version", 0)
		defaults.apply_defaults()
		self.assertEqual(frappe.db.get_single_value("Medusync Settings", "defaults_version"), 0)

	def test_being_explicit_about_it_replaces_it_anyway(self):
		# The "Apply new defaults" button. The operator has seen the
		# notification and decided.
		defaults.apply_defaults(force=True)
		self.doc.reload()
		fields = {row.frappe_field for row in self.doc.field_map}
		self.assertNotIn("customer_group", fields)
		self.assertEqual(self.doc.attention, "")


class TestAMappingSomebodyWrote(UpgradeCase):
	def test_is_never_touched_by_an_upgrade(self):
		mine = frappe.new_doc(MAPPING)
		mine.update(
			{
				"title": "Mine, not a default, upgrade",
				"enabled": 0,
				"document_type": "Customer",
				"direction": "To Medusa",
				"docevents": "on_update",
				"key_field": "name",
			}
		)
		mine.insert(ignore_permissions=True)
		self._extra.append(mine.name)

		defaults.apply_defaults()
		mine.reload()
		self.assertEqual(mine.key_field, "name")
		self.assertEqual(mine.attention, "")
