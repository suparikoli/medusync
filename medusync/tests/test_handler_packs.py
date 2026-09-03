# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Handler packs are opt-in per site via `site_config.json`:

    "medusync_handler_packs": ["risitex"]

Nothing domain-specific may load unless the site asks for it, and the
mapped-push receiver resolves its upsert from the configured pack instead
of a hardcoded import.
"""

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import handlers

CONF_KEY = handlers.CONF_KEY


class TestHandlerPacks(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._saved = frappe.local.conf.get(CONF_KEY, "__absent__")
		handlers.clear()

	def tearDown(self):
		if self._saved == "__absent__":
			frappe.local.conf.pop(CONF_KEY, None)
		else:
			frappe.local.conf[CONF_KEY] = self._saved
		handlers.clear()
		super().tearDown()

	def _set(self, value):
		if value is None:
			frappe.local.conf.pop(CONF_KEY, None)
		else:
			frappe.local.conf[CONF_KEY] = value

	def test_default_pack_when_site_config_is_silent(self):
		self._set(None)
		self.assertEqual(handlers.configured_packs(), ["polemarch"])

	def test_site_config_list_selects_packs(self):
		self._set(["risitex"])
		self.assertEqual(handlers.configured_packs(), ["risitex"])
		registered = handlers.list_registered()
		self.assertIn("order.return_requested", registered)
		self.assertNotIn("customer.synced", registered)

	def test_site_config_string_is_split(self):
		self._set("polemarch, risitex")
		self.assertEqual(handlers.configured_packs(), ["polemarch", "risitex"])
		registered = handlers.list_registered()
		self.assertIn("customer.synced", registered)
		self.assertIn("order.return_requested", registered)

	def test_switching_packs_reloads_registry(self):
		self._set(["polemarch"])
		self.assertIn("customer.synced", handlers.list_registered())
		self._set(["risitex"])
		self.assertNotIn("customer.synced", handlers.list_registered())

	def test_mapped_upsert_resolves_from_configured_pack(self):
		self._set(["risitex"])
		fn = handlers.get_mapped_upsert()
		self.assertEqual(fn.__module__, "medusync.handlers.risitex.mapped")
		self._set(["polemarch"])
		fn = handlers.get_mapped_upsert()
		self.assertEqual(fn.__module__, "medusync.handlers.polemarch.order")

	def test_no_pack_means_no_mapped_upsert(self):
		self._set([])
		self.assertEqual(handlers.configured_packs(), [])
		self.assertIsNone(handlers.get_mapped_upsert())
		self.assertEqual(handlers.list_registered(), [])

	def test_unknown_pack_is_skipped_not_fatal(self):
		self._set(["does_not_exist", "risitex"])
		registered = handlers.list_registered()
		self.assertIn("order.return_requested", registered)
