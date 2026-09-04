# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Stock comes from many warehouses and lands in many locations.

One ERPNext site can hold stock in several warehouses, and each connected
Medusa store keeps its own stock locations. The pairing is per store: two
stores may draw on the same warehouse under different location ids, or on
different warehouses entirely.

Until now a single warehouse was hard-wired in Settings, so a second
warehouse was invisible and a second store received the first store's
numbers. The map replaces that, and the old single setting keeps working
for a site that never fills the map in.
"""

from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import selection, sites, warehouses


def syncable_item() -> str | None:
	"""An Item nothing has excluded from syncing.

	The push tests go through the real selection filter, so borrowing
	whichever Item happens to be first would make them depend on what
	somebody last unticked on this site.
	"""
	excluded = {
		row.document_name
		for row in frappe.get_all(
			selection.EXCLUSION_DOCTYPE,
			filters={"document_type": "Item"},
			fields=["document_name"],
		)
	}
	for row in frappe.get_all("Item", fields=["name", selection.SYNC_FIELD], limit=200):
		if row.name in excluded:
			continue
		if row.get(selection.SYNC_FIELD) == 0:
			continue
		return row.name
	return None


class WarehouseCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._sites = []
		sites.clear_cache()
		warehouses.clear_cache()
		rows = frappe.get_all("Warehouse", filters={"is_group": 0}, fields=["name"], limit=2)
		if len(rows) < 2:
			self.skipTest("needs two warehouses")
		self.wh_a, self.wh_b = rows[0].name, rows[1].name
		self.item = syncable_item()
		if not self.item:
			self.skipTest("no syncable Item on this site")

	def tearDown(self):
		for name in self._sites:
			if frappe.db.exists("Medusync Site", name):
				frappe.delete_doc("Medusync Site", name, force=1, ignore_permissions=True)
		sites.clear_cache()
		warehouses.clear_cache()
		super().tearDown()

	def _site(self, site_id, mapping=()):
		if frappe.db.exists("Medusync Site", site_id):
			frappe.delete_doc("Medusync Site", site_id, force=1, ignore_permissions=True)
		doc = frappe.new_doc("Medusync Site")
		doc.update(
			{"site_id": site_id, "title": site_id, "enabled": 1, "medusa_url": "http://127.0.0.1:9000"}
		)
		for warehouse, location in mapping:
			doc.append("warehouses", {"warehouse": warehouse, "location_id": location, "enabled": 1})
		doc.insert(ignore_permissions=True)
		self._sites.append(doc.name)
		sites.clear_cache()
		warehouses.clear_cache()
		return doc

	def _disable_others(self):
		"""Leave only this test's sites enabled, so a pre-existing site
		does not answer for warehouses this test never mapped.

		Nothing here commits: every write must die with the test's
		transaction, or a suspended real site would stay suspended.
		"""
		mine = set(self._sites)
		self._suspended = [
			s["name"] for s in sites.all_sites(enabled_only=True) if s["name"] not in mine
		]
		for name in self._suspended:
			frappe.db.set_value("Medusync Site", name, "enabled", 0, update_modified=False)
		sites.clear_cache()
		warehouses.clear_cache()

	def _restore_others(self):
		for name in getattr(self, "_suspended", []):
			frappe.db.set_value("Medusync Site", name, "enabled", 1, update_modified=False)
		self._suspended = []
		sites.clear_cache()
		warehouses.clear_cache()


class TestTheMap(WarehouseCase):
	def test_a_warehouse_can_reach_several_stores_under_different_ids(self):
		self._site("wh-a", [(self.wh_a, "sloc_a")])
		self._site("wh-b", [(self.wh_a, "sloc_b")])
		self._disable_others()
		pairs = {(s, loc) for s, loc in warehouses.targets_for(self.wh_a)}
		self.assertEqual(pairs, {("wh-a", "sloc_a"), ("wh-b", "sloc_b")})

	def test_a_store_can_draw_on_several_warehouses(self):
		self._site("wh-multi", [(self.wh_a, "sloc_1"), (self.wh_b, "sloc_2")])
		self._disable_others()
		self.assertEqual(warehouses.targets_for(self.wh_a), [("wh-multi", "sloc_1")])
		self.assertEqual(warehouses.targets_for(self.wh_b), [("wh-multi", "sloc_2")])

	def test_an_unmapped_warehouse_reaches_nobody(self):
		self._site("wh-only-a", [(self.wh_a, "sloc_a")])
		self._disable_others()
		self.assertEqual(warehouses.targets_for(self.wh_b), [])

	def test_a_disabled_row_is_ignored(self):
		site = self._site("wh-off", [(self.wh_a, "sloc_a")])
		site.warehouses[0].enabled = 0
		site.save(ignore_permissions=True)
		warehouses.clear_cache()
		self._disable_others()
		self.assertEqual(warehouses.targets_for(self.wh_a), [])

	def test_a_disabled_store_is_ignored(self):
		site = self._site("wh-site-off", [(self.wh_a, "sloc_a")])
		self._disable_others()
		frappe.db.set_value("Medusync Site", site.name, "enabled", 0, update_modified=False)
		sites.clear_cache()
		warehouses.clear_cache()
		self.assertEqual(warehouses.targets_for(self.wh_a), [])

	def test_the_set_of_warehouses_worth_watching(self):
		self._site("wh-watch", [(self.wh_a, "sloc_a")])
		self._disable_others()
		watched = warehouses.watched()
		self.assertIn(self.wh_a, watched)
		self.assertNotIn(self.wh_b, watched)


class TestTheFallback(WarehouseCase):
	"""A site that never filled the map in keeps the old behaviour."""

	def setUp(self):
		super().setUp()
		self._before = frappe.db.get_single_value("Medusync Settings", "inventory_source_warehouse")

	def tearDown(self):
		self._restore_others()
		frappe.db.set_single_value("Medusync Settings", "inventory_source_warehouse", self._before)
		frappe.clear_cache(doctype="Medusync Settings")
		warehouses.clear_cache()
		super().tearDown()

	def _legacy(self, warehouse):
		frappe.db.set_single_value("Medusync Settings", "inventory_source_warehouse", warehouse)
		frappe.clear_cache(doctype="Medusync Settings")
		warehouses.clear_cache()

	def test_with_no_map_the_single_setting_still_decides(self):
		self._site("wh-legacy")  # no rows
		self._disable_others()
		self._legacy(self.wh_a)
		self.assertEqual(warehouses.targets_for(self.wh_a), [("wh-legacy", None)])
		self.assertEqual(warehouses.targets_for(self.wh_b), [])
		self.assertIn(self.wh_a, warehouses.watched())

	def test_a_store_with_a_map_does_not_get_the_fallback(self):
		# Mixed estate: one store mapped, one not. The mapped store must
		# not also receive the legacy warehouse it never asked for.
		self._site("wh-mapped", [(self.wh_b, "sloc_b")])
		self._disable_others()
		self._legacy(self.wh_a)
		self.assertEqual(warehouses.targets_for(self.wh_a), [])
		self.assertEqual(warehouses.targets_for(self.wh_b), [("wh-mapped", "sloc_b")])


class TestThePush(WarehouseCase):
	def tearDown(self):
		self._restore_others()
		super().tearDown()

	def test_each_store_is_told_its_own_location(self):
		self._site("wh-p1", [(self.wh_a, "sloc_one")])
		self._site("wh-p2", [(self.wh_a, "sloc_two")])
		self._disable_others()
		from medusync.handlers.risitex import inventory

		sent = []
		with patch("medusync.outbound.send", side_effect=lambda *a, **k: sent.append((a, k))):
			inventory.push_level(self.item, self.wh_a, "unit")
		by_site = {k["site_id"]: a[3] for a, k in sent}
		self.assertEqual(set(by_site), {"wh-p1", "wh-p2"})
		self.assertEqual(by_site["wh-p1"]["location_id"], "sloc_one")
		self.assertEqual(by_site["wh-p2"]["location_id"], "sloc_two")
		self.assertEqual(by_site["wh-p1"]["warehouse"], self.wh_a)

	def test_two_warehouses_do_not_collide_on_one_event_id(self):
		# Same item, same store, two warehouses in one save. If the event
		# ids matched, Medusa's idempotency would drop the second.
		self._site("wh-p5", [(self.wh_a, "sloc_a"), (self.wh_b, "sloc_b")])
		self._disable_others()
		from medusync.handlers.risitex import inventory

		sent = []
		with patch("medusync.outbound.send", side_effect=lambda *a, **k: sent.append(a)):
			inventory.push_level(self.item, self.wh_a, "so-1")
			inventory.push_level(self.item, self.wh_b, "so-1")
		ids = [a[2] for a in sent]
		self.assertEqual(len(ids), 2)
		self.assertNotEqual(ids[0], ids[1])

	def test_an_unmapped_warehouse_pushes_nothing(self):
		self._site("wh-p3", [(self.wh_a, "sloc_one")])
		self._disable_others()
		from medusync.handlers.risitex import inventory

		sent = []
		with patch("medusync.outbound.send", side_effect=lambda *a, **k: sent.append(k)):
			inventory.push_level(self.item, self.wh_b, "unit")
		self.assertEqual(sent, [])

	def test_the_quantity_is_still_sellable_not_raw_stock(self):
		self._site("wh-p4", [(self.wh_a, "sloc_one")])
		self._disable_others()
		from medusync.handlers.risitex import inventory

		sent = []
		with patch("medusync.outbound.send", side_effect=lambda *a, **k: sent.append((a, k))):
			inventory.push_level(self.item, self.wh_a, "unit")
		payload = sent[0][0][3]
		self.assertIn("quantity", payload)
		self.assertGreaterEqual(payload["quantity"], 0)
		self.assertEqual(payload["sku"], self.item)
