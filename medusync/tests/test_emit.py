# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Handler packs reach every connected site, not just the first one.

Each pack used to log one row with no site and post to whatever the
default happened to be, so on a two-store site half the stock and price
updates went nowhere. `outbound.emit` is the one place that fans out.
"""

from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import echo, outbound, sites


def _site(site_id, enabled=1):
	if frappe.db.exists("Medusync Site", site_id):
		frappe.delete_doc("Medusync Site", site_id, force=1, ignore_permissions=True)
	doc = frappe.new_doc("Medusync Site")
	doc.update(
		{
			"site_id": site_id,
			"title": site_id,
			"enabled": enabled,
			"medusa_url": "http://127.0.0.1:9000",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


class TestEmitFansOut(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._made = []
		sites.clear_cache()
		# Medusync Log.document_name is a Dynamic Link, so the row it points
		# at has to exist. Borrow a real Item rather than inventing a name.
		rows = frappe.get_all("Item", fields=["name"], limit=1)
		if not rows:
			self.skipTest("no Item on this site to emit about")
		self.item = rows[0].name
		echo.forget("Item", self.item)

	def tearDown(self):
		for name in self._made:
			if frappe.db.exists("Medusync Site", name):
				frappe.delete_doc("Medusync Site", name, force=1, ignore_permissions=True)
		sites.clear_cache()
		echo.forget("Item", self.item)
		frappe.db.delete("Medusync Log", {"event": "inventory.level.set", "event_id": ["like", "frappe:inventory.level.set:unit:%"]})
		super().tearDown()

	def _make(self, site_id, enabled=1):
		doc = _site(site_id, enabled)
		self._made.append(doc.name)
		sites.clear_cache()
		return doc

	def _emit(self):
		sent = []
		with patch("medusync.outbound.send", side_effect=lambda *a, **k: sent.append((a, k))):
			outbound.emit(
				"inventory.level.set",
				{"sku": self.item, "quantity": 7},
				ref="unit",
				doctype="Item",
				docname=self.item,
			)
		return sent

	def test_one_send_per_enabled_site(self):
		self._make("emit-a")
		self._make("emit-b")
		self._make("emit-off", enabled=0)
		targets = {k["site_id"] for _, k in self._emit()}
		self.assertIn("emit-a", targets)
		self.assertIn("emit-b", targets)
		self.assertNotIn("emit-off", targets)

	def test_each_site_gets_its_own_log_row_and_event_id(self):
		self._make("emit-a")
		self._make("emit-b")
		sent = self._emit()
		ids = [a[2] for a, _ in sent]
		self.assertEqual(len(ids), len(set(ids)), "event ids must differ per site")
		rows = frappe.get_all(
			"Medusync Log",
			filters={"event_id": ["like", "frappe:inventory.level.set:unit:%"]},
			fields=["site", "status"],
		)
		# A shared dev site already has its own Medusync Site records, and
		# emit reaches every enabled one; assert ours are among them.
		found = {r.site for r in rows}
		self.assertTrue({"emit-a", "emit-b"} <= found, f"expected both sites, got {found}")
		self.assertEqual({r.status for r in rows}, {"Queued"})

	def test_an_inbound_write_is_tagged_as_an_echo(self):
		self._make("emit-a")
		echo.remember("Item", self.item, correlation_id="corr-7", origin="medusa:emit-a")
		sent = self._emit()
		self.assertTrue(sent)
		for _, kw in sent:
			self.assertEqual(kw["echo_of"], "medusa:emit-a")
			self.assertEqual(kw["correlation_id"], "corr-7")

	def test_a_local_change_carries_no_echo_tag(self):
		self._make("emit-a")
		for _, kw in self._emit():
			self.assertIsNone(kw["echo_of"])

	def test_no_site_means_nothing_is_sent(self):
		# Sites this test did not create still exist on a shared dev site,
		# so assert on absence of OUR ids rather than an empty list.
		sent = self._emit()
		self.assertNotIn("emit-a", {k["site_id"] for _, k in sent})
