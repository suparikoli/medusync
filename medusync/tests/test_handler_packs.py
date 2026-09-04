# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Handler packs are opt-in per site via `site_config.json`:

    "medusync_handler_packs": ["commerce"]

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
from medusync.tests import probe_pack

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
		self.assertEqual(handlers.configured_packs(), ["commerce"])

	def test_site_config_list_selects_packs(self):
		self._set(["commerce"])
		self.assertEqual(handlers.configured_packs(), ["commerce"])
		registered = handlers.list_registered()
		self.assertIn("order.return_requested", registered)
		self.assertNotIn("customer.synced", registered)

	def test_site_config_string_is_split(self):
		with probe_pack.installed():
			self._set("commerce, probe")
			self.assertEqual(handlers.configured_packs(), ["commerce", "probe"])
			registered = handlers.list_registered()
			self.assertIn(probe_pack.EVENT, registered)
			self.assertIn("order.return_requested", registered)

	def test_switching_packs_reloads_registry(self):
		with probe_pack.installed():
			self._set(["probe"])
			self.assertIn(probe_pack.EVENT, handlers.list_registered())
			self._set(["commerce"])
			self.assertNotIn(probe_pack.EVENT, handlers.list_registered())

	def test_mapped_upsert_resolves_from_configured_pack(self):
		with probe_pack.installed():
			self._set(["commerce"])
			fn = handlers.get_mapped_upsert()
			self.assertEqual(fn.__module__, "medusync.handlers.commerce.mapped")
			self._set(["probe"])
			fn = handlers.get_mapped_upsert()
			self.assertIs(fn, probe_pack.upsert)

	def test_the_first_pack_that_offers_an_upsert_wins(self):
		"""Load order is the site's, and the registry keeps it."""
		with probe_pack.installed():
			self._set(["probe", "commerce"])
			self.assertIs(handlers.get_mapped_upsert(), probe_pack.upsert)
			self._set(["commerce", "probe"])
			self.assertEqual(
				handlers.get_mapped_upsert().__module__, "medusync.handlers.commerce.mapped"
			)

	def test_no_pack_means_no_mapped_upsert(self):
		self._set([])
		self.assertEqual(handlers.configured_packs(), [])
		self.assertIsNone(handlers.get_mapped_upsert())
		self.assertEqual(handlers.list_registered(), [])

	def test_unknown_pack_is_skipped_not_fatal(self):
		self._set(["does_not_exist", "commerce"])
		registered = handlers.list_registered()
		self.assertIn("order.return_requested", registered)
