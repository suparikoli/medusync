# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""One Medusync Site record per connected Medusa backend.

Connection details and the shared secrets move off the Single onto a
per-site record so several Medusa stores can talk to one ERPNext. The
Single keeps the globals (retention, queueing, timeouts).
"""

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import sites


def _site(site_id, **kw):
	if frappe.db.exists("Medusync Site", site_id):
		frappe.delete_doc("Medusync Site", site_id, force=1, ignore_permissions=True)
	doc = frappe.new_doc("Medusync Site")
	doc.update(
		{
			"site_id": site_id,
			"title": kw.pop("title", site_id),
			"enabled": kw.pop("enabled", 1),
			"medusa_url": kw.pop("medusa_url", "http://127.0.0.1:9000"),
		}
	)
	doc.update(kw)
	doc.insert(ignore_permissions=True)
	return doc


class TestSites(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._made = []
		sites.clear_cache()

	def tearDown(self):
		for name in self._made:
			if frappe.db.exists("Medusync Site", name):
				frappe.delete_doc("Medusync Site", name, force=1, ignore_permissions=True)
		sites.clear_cache()
		super().tearDown()

	def _make(self, site_id, **kw):
		doc = _site(site_id, **kw)
		self._made.append(doc.name)
		return doc

	def test_site_is_named_by_its_id(self):
		doc = self._make("alpha")
		self.assertEqual(doc.name, "alpha")

	def test_enabled_sites_only(self):
		self._make("t-on")
		self._make("t-off", enabled=0)
		ids = {s["site_id"] for s in sites.all_sites()}
		self.assertIn("t-on", ids)
		self.assertNotIn("t-off", ids)
		ids_all = {s["site_id"] for s in sites.all_sites(enabled_only=False)}
		self.assertIn("t-off", ids_all)

	def test_endpoint_joins_url_and_path(self):
		self._make("t-ep", medusa_url="http://host:9000/", inbound_path="/webhooks/erpnext-inbound")
		site = sites.get_site("t-ep")
		self.assertEqual(sites.endpoint(site), "http://host:9000/webhooks/erpnext-inbound")

	def test_our_site_ids_covers_every_configured_site(self):
		self._make("t-a")
		self._make("t-b", enabled=0)
		ours = sites.our_site_ids()
		self.assertIn("t-a", ours)
		# a disabled site is still ours — its echoes must still be recognised
		self.assertIn("t-b", ours)

	def test_a_mapping_without_a_site_targets_every_site(self):
		self._make("t-1")
		self._make("t-2")
		mapping = frappe._dict({"site": None})
		ids = {s["site_id"] for s in sites.sites_for_mapping(mapping)}
		self.assertIn("t-1", ids)
		self.assertIn("t-2", ids)

	def test_a_mapping_pinned_to_a_site_targets_only_that_site(self):
		self._make("t-1")
		self._make("t-2")
		mapping = frappe._dict({"site": "t-1"})
		ids = [s["site_id"] for s in sites.sites_for_mapping(mapping)]
		self.assertEqual(ids, ["t-1"])

	def test_a_mapping_pinned_to_a_disabled_site_targets_nothing(self):
		self._make("t-off2", enabled=0)
		mapping = frappe._dict({"site": "t-off2"})
		self.assertEqual(sites.sites_for_mapping(mapping), [])

	def test_secrets_are_stored_encrypted_and_read_back(self):
		doc = self._make("t-sec")
		doc.inbound_secret = "in-secret-value"
		doc.outbound_secret = "out-secret-value"
		doc.save(ignore_permissions=True)
		sites.clear_cache()
		self.assertEqual(sites.secret("t-sec", "inbound_secret"), "in-secret-value")
		self.assertEqual(sites.secret("t-sec", "outbound_secret"), "out-secret-value")
		# the raw column must not hold the plaintext
		raw = frappe.db.get_value("Medusync Site", "t-sec", "inbound_secret")
		self.assertNotEqual(raw, "in-secret-value")

	def test_site_for_inbound_secret_matches_the_signing_site(self):
		a = self._make("t-sig-a")
		a.inbound_secret = "secret-a"
		a.save(ignore_permissions=True)
		b = self._make("t-sig-b")
		b.inbound_secret = "secret-b"
		b.save(ignore_permissions=True)
		sites.clear_cache()
		body = b'{"hello":true}'
		from medusync.signing import sign

		found = sites.site_for_signature(body, sign(body, "secret-b"))
		self.assertIsNotNone(found)
		self.assertEqual(found["site_id"], "t-sig-b")
		self.assertIsNone(sites.site_for_signature(body, "deadbeef"))
