# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""The second key to every door.

Before multi-site, one secret on the Single authenticated everything.
Sites replaced it with a pair per store, and attribution moved to the
signature — which is the property the whole multi-site design rests on:
a store cannot claim to be another, because it cannot sign as one.

The old secret still verifies, and that is the exception. A request
signed with it is accepted whichever store it claims to be from, and
because no store matched, the row says `default`. So a leak there is not
scoped to one store; it is the whole installation, logged under the wrong
name.

It stays because a site upgrading from before multi-site has its secrets
in the Single, and removing the fallback mid-deploy drops real traffic.
What it should not do is stay silently. It announces itself when used,
and it can be switched off.
"""

import json
from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import api
from medusync.signing import sign

SETTINGS = "Medusync Settings"
LEGACY_SECRET = "a-legacy-secret-from-before-sites"


class LegacyCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._before_allow = frappe.db.get_single_value(SETTINGS, "allow_legacy_secret")
		settings = frappe.get_single(SETTINGS)
		self._had_secret = settings.get_password("inbound_secret", raise_exception=False)
		settings.inbound_secret = LEGACY_SECRET
		settings.flags.ignore_permissions = True
		settings.save()
		frappe.clear_cache(doctype=SETTINGS)
		self.body = json.dumps({"event": "customer.updated"}).encode("utf-8")

	def tearDown(self):
		settings = frappe.get_single(SETTINGS)
		settings.inbound_secret = self._had_secret
		settings.allow_legacy_secret = self._before_allow
		settings.flags.ignore_permissions = True
		settings.save()
		frappe.clear_cache(doctype=SETTINGS)
		super().tearDown()

	def _allow(self, value):
		frappe.db.set_single_value(SETTINGS, "allow_legacy_secret", value)
		frappe.clear_cache(doctype=SETTINGS)


class TestTheFallback(LegacyCase):
	def test_it_still_works_by_default(self):
		# A site mid-upgrade has its secrets in the Single and nothing else.
		# Turning this off by default would drop that site's real traffic.
		self._allow(1)
		self.assertTrue(api._legacy_secret_matches(self.body, sign(self.body, LEGACY_SECRET)))

	def test_and_can_be_switched_off(self):
		self._allow(0)
		self.assertFalse(api._legacy_secret_matches(self.body, sign(self.body, LEGACY_SECRET)))

	def test_a_wrong_signature_never_passes_either_way(self):
		self._allow(1)
		self.assertFalse(api._legacy_secret_matches(self.body, sign(self.body, "not-the-secret")))
		self.assertFalse(api._legacy_secret_matches(self.body, None))

	def test_using_it_is_announced(self):
		# Silence is the actual problem. An operator should learn which
		# store has not been repaired, from the store.
		self._allow(1)
		with patch("medusync.api._warn_legacy_secret") as warned:
			api._legacy_secret_matches(self.body, sign(self.body, LEGACY_SECRET))
		self.assertTrue(warned.called)

	def test_and_nothing_is_announced_when_it_was_not_used(self):
		self._allow(1)
		with patch("medusync.api._warn_legacy_secret") as warned:
			api._legacy_secret_matches(self.body, sign(self.body, "wrong"))
		self.assertFalse(warned.called)

	def test_a_site_that_never_had_one_is_unaffected(self):
		settings = frappe.get_single(SETTINGS)
		settings.inbound_secret = None
		settings.flags.ignore_permissions = True
		settings.save()
		frappe.clear_cache(doctype=SETTINGS)
		self._allow(1)
		self.assertFalse(api._legacy_secret_matches(self.body, sign(self.body, LEGACY_SECRET)))


class TestTheWarningLandsSomewhere(LegacyCase):
	def test_the_store_it_was_attributed_to_says_so(self):
		# `default` is where an unattributable request lands, so that is
		# where somebody will look for why.
		site = frappe.get_all("Medusync Site", fields=["name"], limit=1)
		if not site:
			self.skipTest("no Medusync Site configured")
		name = site[0].name
		before = frappe.db.get_value("Medusync Site", name, "last_error")
		try:
			api._warn_legacy_secret(name)
			self.assertIn("Single", frappe.db.get_value("Medusync Site", name, "last_error") or "")
		finally:
			frappe.db.set_value("Medusync Site", name, "last_error", before, update_modified=False)

	def test_it_never_raises(self):
		# It runs inside the authentication path. A failure to warn must
		# not become a failure to authenticate.
		with patch("frappe.db.set_value", side_effect=RuntimeError("boom")):
			api._warn_legacy_secret("whatever")
