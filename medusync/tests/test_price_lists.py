# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Each price list travels in its own direction.

Prices are bidirectional by default, but the direction is configurable
independently for each Price List. Retail might flow ERPNext to Medusa, a
wholesale list might feed B2B tiers, and a cost list must never leave the
building at all. One global "selling price list" setting could express
none of that.

The map is per store, because two stores can price the same catalogue
differently. A site that never fills it in keeps the old behaviour.
"""

from contextlib import contextmanager
from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import price_lists, sites


class PriceListCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._sites = []
		self._suspended = []
		sites.clear_cache()
		price_lists.clear_cache()
		rows = frappe.get_all("Price List", filters={"selling": 1}, fields=["name"], limit=2)
		if len(rows) < 2:
			self.skipTest("needs two selling price lists")
		self.pl_a, self.pl_b = rows[0].name, rows[1].name
		item = frappe.get_all("Item", fields=["name"], limit=1)
		if not item:
			self.skipTest("no Item on this site")
		self.item = item[0].name

	def tearDown(self):
		self._restore_others()
		for name in self._sites:
			if frappe.db.exists("Medusync Site", name):
				frappe.delete_doc("Medusync Site", name, force=1, ignore_permissions=True)
		sites.clear_cache()
		price_lists.clear_cache()
		super().tearDown()

	def _site(self, site_id, rules=()):
		if frappe.db.exists("Medusync Site", site_id):
			frappe.delete_doc("Medusync Site", site_id, force=1, ignore_permissions=True)
		doc = frappe.new_doc("Medusync Site")
		doc.update(
			{"site_id": site_id, "title": site_id, "enabled": 1, "medusa_url": "http://127.0.0.1:9000"}
		)
		for rule in rules:
			doc.append("price_lists", rule)
		doc.insert(ignore_permissions=True)
		self._sites.append(doc.name)
		sites.clear_cache()
		price_lists.clear_cache()
		return doc

	def _disable_others(self):
		"""Leave only this test's stores answering. Nothing commits, so a
		real site suspended here is restored by the rollback as well."""
		mine = set(self._sites)
		self._suspended = [s["name"] for s in sites.all_sites() if s["name"] not in mine]
		for name in self._suspended:
			frappe.db.set_value("Medusync Site", name, "enabled", 0, update_modified=False)
		sites.clear_cache()
		price_lists.clear_cache()

	def _restore_others(self):
		for name in getattr(self, "_suspended", []):
			frappe.db.set_value("Medusync Site", name, "enabled", 1, update_modified=False)
		self._suspended = []
		sites.clear_cache()
		price_lists.clear_cache()

	def _item_price(self, price_list, rate=100, packing_unit=0):
		"""A stand-in Item Price. It is never inserted, so the tests using
		it must keep `Medusync Log` out of the way — its `document_name`
		is a Dynamic Link and would refuse a name that does not exist."""
		return frappe._dict(
			{
				"doctype": "Item Price",
				"name": "ip-%s" % price_list,
				"item_code": self.item,
				"price_list": price_list,
				"price_list_rate": rate,
				"currency": "INR",
				"packing_unit": packing_unit,
				"valid_from": None,
				"valid_upto": None,
			}
		)

	@contextmanager
	def _capture(self):
		"""Collect what would have gone on the wire, without logging it."""
		sent = []
		with patch(
			"medusync.outbound._create_log",
			side_effect=lambda **kw: frappe._dict(name="log-%s" % len(sent)),
		), patch("medusync.outbound.send", side_effect=lambda *a, **k: sent.append((a, k))):
			yield sent


class TestTheRules(PriceListCase):
	def test_a_base_price_list_reaches_its_store(self):
		self._site("pl-a", [{"price_list": self.pl_a, "direction": "To Medusa", "role": "Base Price"}])
		self._disable_others()
		rules = price_lists.rules_for(self.pl_a)
		self.assertEqual(len(rules), 1)
		self.assertEqual(rules[0]["site_id"], "pl-a")
		self.assertEqual(rules[0]["role"], "Base Price")

	def test_dont_sync_stops_a_list_leaving(self):
		self._site("pl-b", [{"price_list": self.pl_a, "direction": "Don't Sync", "role": "Base Price"}])
		self._disable_others()
		self.assertEqual(price_lists.rules_for(self.pl_a), [])

	def test_a_list_medusa_owns_does_not_travel_outbound(self):
		self._site("pl-c", [{"price_list": self.pl_a, "direction": "From Medusa", "role": "Base Price"}])
		self._disable_others()
		self.assertEqual(price_lists.rules_for(self.pl_a), [])

	def test_two_way_counts_as_outbound(self):
		self._site("pl-d", [{"price_list": self.pl_a, "direction": "Two-way", "role": "Base Price"}])
		self._disable_others()
		self.assertEqual(len(price_lists.rules_for(self.pl_a)), 1)

	def test_a_tier_list_carries_its_code(self):
		self._site(
			"pl-e",
			[
				{
					"price_list": self.pl_a,
					"direction": "To Medusa",
					"role": "Tier Price",
					"tier_code": "local_mbo",
				}
			],
		)
		self._disable_others()
		rule = price_lists.rules_for(self.pl_a)[0]
		self.assertEqual(rule["role"], "Tier Price")
		self.assertEqual(rule["tier_code"], "local_mbo")

	def test_a_list_nobody_mapped_goes_nowhere(self):
		self._site("pl-f", [{"price_list": self.pl_a, "direction": "To Medusa", "role": "Base Price"}])
		self._disable_others()
		self.assertEqual(price_lists.rules_for(self.pl_b), [])

	def test_the_same_list_can_be_a_base_price_here_and_a_tier_there(self):
		self._site("pl-g", [{"price_list": self.pl_a, "direction": "To Medusa", "role": "Base Price"}])
		self._site(
			"pl-h",
			[
				{
					"price_list": self.pl_a,
					"direction": "To Medusa",
					"role": "Tier Price",
					"tier_code": "wholesale",
				}
			],
		)
		self._disable_others()
		by_site = {r["site_id"]: r["role"] for r in price_lists.rules_for(self.pl_a)}
		self.assertEqual(by_site, {"pl-g": "Base Price", "pl-h": "Tier Price"})

	def test_the_same_list_listed_twice_for_one_store_is_refused(self):
		# Two rules for one list at one store is not a conflict the code can
		# see; the second simply wins, and the store starts pricing from a
		# rule nobody chose.
		site = self._site(
			"pl-dupe", [{"price_list": self.pl_a, "direction": "To Medusa", "role": "Base Price"}]
		)
		site.append(
			"price_lists",
			{"price_list": self.pl_a, "direction": "To Medusa", "role": "Tier Price", "tier_code": "t"},
		)
		with self.assertRaises(frappe.ValidationError):
			site.save(ignore_permissions=True)

	def test_a_tier_row_without_a_code_is_refused(self):
		# It could never be applied at the far end, and the failure would
		# arrive as a stream of skipped events rather than as the
		# configuration mistake it is.
		with self.assertRaises(frappe.ValidationError):
			self._site(
				"pl-notier",
				[{"price_list": self.pl_a, "direction": "To Medusa", "role": "Tier Price"}],
			)

	def test_a_disabled_row_is_ignored(self):
		site = self._site(
			"pl-i", [{"price_list": self.pl_a, "direction": "To Medusa", "role": "Base Price"}]
		)
		site.price_lists[0].enabled = 0
		site.save(ignore_permissions=True)
		price_lists.clear_cache()
		self._disable_others()
		self.assertEqual(price_lists.rules_for(self.pl_a), [])


class TestTheFallback(PriceListCase):
	"""Before the map, one global setting named the selling list and a
	Custom Field on Price List named the tier. Both still work."""

	def setUp(self):
		super().setUp()
		self._before = frappe.db.get_single_value("Medusync Settings", "pricing_selling_price_list")

	def tearDown(self):
		frappe.db.set_single_value("Medusync Settings", "pricing_selling_price_list", self._before)
		frappe.clear_cache(doctype="Medusync Settings")
		price_lists.clear_cache()
		super().tearDown()

	def _legacy(self, price_list):
		frappe.db.set_single_value("Medusync Settings", "pricing_selling_price_list", price_list)
		frappe.clear_cache(doctype="Medusync Settings")
		price_lists.clear_cache()

	def test_the_configured_selling_list_is_still_the_base_price(self):
		self._site("pl-legacy")  # no rules
		self._disable_others()
		self._legacy(self.pl_a)
		rules = price_lists.rules_for(self.pl_a)
		self.assertEqual([(r["site_id"], r["role"]) for r in rules], [("pl-legacy", "Base Price")])

	def test_a_tier_code_on_the_price_list_is_still_honoured(self):
		self._site("pl-legacy2")
		self._disable_others()
		# Pin the base price to the OTHER list, so this one can only be
		# reached as a tier. Left to whatever the site has configured, the
		# two could be the same list and the assertion would follow the
		# data rather than the code.
		self._legacy(self.pl_a)
		frappe.db.set_value(
			"Price List", self.pl_b, "medusa_customer_tier", "legacy_tier", update_modified=False
		)
		price_lists.clear_cache()
		try:
			rules = price_lists.rules_for(self.pl_b)
			self.assertEqual(len(rules), 1)
			self.assertEqual(rules[0]["role"], "Tier Price")
			self.assertEqual(rules[0]["tier_code"], "legacy_tier")
		finally:
			frappe.db.set_value(
				"Price List", self.pl_b, "medusa_customer_tier", None, update_modified=False
			)
			price_lists.clear_cache()


class TestThePush(PriceListCase):
	def test_a_base_list_sends_the_variant_price(self):
		self._site("pl-push", [{"price_list": self.pl_a, "direction": "To Medusa", "role": "Base Price"}])
		self._disable_others()
		from medusync.handlers.commerce import pricing

		with self._capture() as sent:
			pricing.on_item_price(self._item_price(self.pl_a, rate=799), method="on_update")
		self.assertEqual([a[1] for a, _ in sent], ["variant.price.set"])
		self.assertEqual(sent[0][0][3]["amount"], 799)

	def test_a_tier_list_sends_a_tier_price_with_its_bracket(self):
		self._site(
			"pl-push2",
			[
				{
					"price_list": self.pl_a,
					"direction": "To Medusa",
					"role": "Tier Price",
					"tier_code": "local_mbo",
				}
			],
		)
		self._disable_others()
		from medusync.handlers.commerce import pricing

		with self._capture() as sent:
			pricing.on_item_price(
				self._item_price(self.pl_a, rate=640, packing_unit=50), method="on_update"
			)
		self.assertEqual([a[1] for a, _ in sent], ["variant.tier_price.set"])
		payload = sent[0][0][3]
		self.assertEqual(payload["tier_code"], "local_mbo")
		self.assertEqual(payload["min_quantity"], 50)

	def test_a_dont_sync_list_sends_nothing(self):
		self._site(
			"pl-push3", [{"price_list": self.pl_a, "direction": "Don't Sync", "role": "Base Price"}]
		)
		self._disable_others()
		from medusync.handlers.commerce import pricing

		with self._capture() as sent:
			pricing.on_item_price(self._item_price(self.pl_a), method="on_update")
		self.assertEqual(sent, [])

	def test_each_store_gets_only_the_rule_it_asked_for(self):
		self._site("pl-x", [{"price_list": self.pl_a, "direction": "To Medusa", "role": "Base Price"}])
		self._site(
			"pl-y",
			[{"price_list": self.pl_a, "direction": "To Medusa", "role": "Tier Price", "tier_code": "t2"}],
		)
		self._disable_others()
		from medusync.handlers.commerce import pricing

		with self._capture() as sent:
			pricing.on_item_price(self._item_price(self.pl_a), method="on_update")
		by_site = {k["site_id"]: a[1] for a, k in sent}
		self.assertEqual(by_site, {"pl-x": "variant.price.set", "pl-y": "variant.tier_price.set"})
