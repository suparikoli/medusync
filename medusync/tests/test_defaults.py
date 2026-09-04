# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""The mappings this app ships with.

A hard reset has to put something back, and "whatever was there before"
is not a definition. So the default set is data: a fixed list with fixed
identifiers, which both systems can agree on without a handshake because
the identifier is derived from the name rather than generated.

Restoring is deliberately narrow. It puts the defaults back exactly as
they ship and switched off; it does not touch a mapping somebody wrote.
Applying *new* defaults to an installation that has edited the old ones
is a different and harder problem, and it is not this.
"""

from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import defaults

MAPPING = "Medusync Mapping"


class DefaultsCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		# Saving a mapping tells the connected stores about it. These tests
		# are about what the defaults are, not about the wire.
		self._push = patch("medusync.mapping_sync.push_mapping", return_value=None)
		self._push.start()
		self._extra = []
		self._before_version = frappe.db.get_single_value("Medusync Settings", "defaults_version")
		self._existing = {
			row.mapping_uid: row.name
			for row in frappe.get_all(MAPPING, fields=["name", "mapping_uid"])
			if row.mapping_uid
		}

	def tearDown(self):
		for name in self._extra:
			if frappe.db.exists(MAPPING, name):
				frappe.delete_doc(MAPPING, name, force=1, ignore_permissions=True)
		# Anything restore_defaults created that was not here before.
		for row in frappe.get_all(MAPPING, fields=["name", "mapping_uid"]):
			if row.mapping_uid and row.mapping_uid.startswith(defaults.UID_PREFIX):
				if row.mapping_uid not in self._existing and frappe.db.exists(MAPPING, row.name):
					frappe.delete_doc(MAPPING, row.name, force=1, ignore_permissions=True)
		frappe.db.set_single_value("Medusync Settings", "defaults_version", self._before_version)
		self._push.stop()
		super().tearDown()

	def _uid_of(self, slug):
		return defaults.UID_PREFIX + slug

	def _doc(self, slug):
		name = frappe.db.get_value(MAPPING, {"mapping_uid": self._uid_of(slug)}, "name")
		return frappe.get_doc(MAPPING, name) if name else None


class TestTheSetItself(DefaultsCase):
	def test_every_default_has_a_stable_identifier(self):
		# Both systems have to agree which mapping is which without asking
		# each other, so the id is derived, not generated.
		first = [m["uid"] for m in defaults.default_mappings()]
		second = [m["uid"] for m in defaults.default_mappings()]
		self.assertEqual(first, second)
		self.assertTrue(all(uid.startswith(defaults.UID_PREFIX) for uid in first))

	def test_the_identifiers_are_unique(self):
		uids = [m["uid"] for m in defaults.default_mappings()]
		self.assertEqual(len(uids), len(set(uids)))

	def test_it_covers_the_three_things_every_store_needs(self):
		entities = {m["medusa_entity"] for m in defaults.default_mappings()}
		self.assertEqual(entities, {"customer", "product", "order"})

	def test_the_catalogue_default_follows_the_configured_doctype(self):
		# A site that keeps its products somewhere other than Item said so
		# in Settings; the default has to land there, not on Item.
		before = frappe.db.get_single_value("Medusync Settings", "products_doctype")
		frappe.db.set_single_value("Medusync Settings", "products_doctype", "Customer")
		frappe.clear_cache(doctype="Medusync Settings")
		try:
			catalogue = next(
				m for m in defaults.default_mappings() if m["medusa_entity"] == "product"
			)
			self.assertEqual(catalogue["document_type"], "Customer")
		finally:
			frappe.db.set_single_value("Medusync Settings", "products_doctype", before)
			frappe.clear_cache(doctype="Medusync Settings")

	def test_the_order_default_carries_where_the_order_came_from(self):
		# Without this pair the channel Medusa reports has nowhere to land,
		# and every web order reads as "medusa" rather than as itself.
		order = next(m for m in defaults.default_mappings() if m["medusa_entity"] == "order")
		pairs = {(f[0], f[1]) for f in order["fields"]}
		self.assertIn(("medusa_order_source", "source"), pairs)

	def test_nothing_ships_switched_on(self):
		for mapping in defaults.default_mappings():
			self.assertFalse(mapping.get("enabled"))


class TestRestoring(DefaultsCase):
	def test_it_puts_every_default_back(self):
		result = defaults.restore_defaults()
		self.assertEqual(len(result["mappings"]), len(defaults.default_mappings()))
		for spec in defaults.default_mappings():
			doc = frappe.db.get_value(MAPPING, {"mapping_uid": spec["uid"]}, "name")
			self.assertTrue(doc, spec["uid"])

	def test_what_it_puts_back_is_switched_off(self):
		# A reset that left mappings running would be a reset that changed
		# what the site does without anybody looking at it.
		defaults.restore_defaults()
		for spec in defaults.default_mappings():
			enabled = frappe.db.get_value(MAPPING, {"mapping_uid": spec["uid"]}, "enabled")
			self.assertFalse(enabled, spec["uid"])

	def test_doing_it_twice_creates_nothing_extra(self):
		defaults.restore_defaults()
		count = frappe.db.count(MAPPING)
		defaults.restore_defaults()
		self.assertEqual(frappe.db.count(MAPPING), count)

	def test_it_undoes_an_edit_to_a_default(self):
		defaults.restore_defaults()
		spec = defaults.default_mappings()[0]
		doc = self._doc(spec["uid"][len(defaults.UID_PREFIX) :])
		doc.key_field = "name"
		doc.field_map = []
		doc.save(ignore_permissions=True)

		defaults.restore_defaults()
		doc.reload()
		self.assertEqual(doc.key_field, spec["key_field"])
		self.assertEqual(len(doc.field_map), len(spec["fields"]))

	def test_it_leaves_a_mapping_somebody_wrote_alone(self):
		mine = frappe.new_doc(MAPPING)
		mine.update(
			{
				"title": "Hand written, not a default",
				"enabled": 0,
				"document_type": "Customer",
				"direction": "To Medusa",
				"docevents": "on_update",
				"key_field": "name",
			}
		)
		mine.insert(ignore_permissions=True)
		self._extra.append(mine.name)

		defaults.restore_defaults()
		mine.reload()
		self.assertEqual(mine.key_field, "name")
		self.assertEqual(mine.direction, "To Medusa")
		self.assertTrue(frappe.db.exists(MAPPING, mine.name))

	def test_it_records_which_defaults_are_installed(self):
		defaults.restore_defaults()
		self.assertEqual(
			frappe.db.get_single_value("Medusync Settings", "defaults_version"),
			defaults.DEFAULTS_VERSION,
		)

	def test_a_title_somebody_else_took_does_not_stop_it(self):
		spec = defaults.default_mappings()[0]
		# Set up the collision this test is about: the default's preferred
		# title held by a mapping that is not the default. On a site where
		# the defaults are already installed, the default itself holds that
		# title, which is a different situation entirely.
		installed = frappe.db.get_value(MAPPING, {"mapping_uid": spec["uid"]}, "name")
		if installed:
			frappe.delete_doc(MAPPING, installed, force=1, ignore_permissions=True)

		squatter = frappe.new_doc(MAPPING)
		squatter.update(
			{
				"title": spec["title"],
				"enabled": 0,
				"document_type": "Customer",
				"direction": "To Medusa",
				"docevents": "on_update",
				"key_field": "name",
			}
		)
		squatter.insert(ignore_permissions=True)
		self._extra.append(squatter.name)

		result = defaults.restore_defaults()
		self.assertEqual(len(result["mappings"]), len(defaults.default_mappings()))
		# The default exists under a title of its own, and the squatter is
		# untouched — a reset must not rename somebody's work.
		restored = frappe.db.get_value(MAPPING, {"mapping_uid": spec["uid"]}, "name")
		self.assertTrue(restored)
		self.assertNotEqual(restored, squatter.name)
		self.assertTrue(frappe.db.exists(MAPPING, squatter.name))
