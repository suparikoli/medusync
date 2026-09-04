# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""When a store is down, stop knocking.

A store that has been unreachable for ten deliveries will be unreachable
for the eleventh, and every attempt costs a worker for the length of the
timeout. With several stores connected, one that is down can starve the
queue for the ones that are up — which is the failure mode multi-site was
built to prevent, arriving by a different door.

So the breaker counts consecutive failures per store, stops trying after
a threshold, and lets exactly one delivery through per sweep to find out
whether the store has come back. One success closes it.

Rehearsals never count. A dry run against a store that is down is a
rehearsal that failed, not a delivery that failed, and letting it trip the
breaker would take real traffic down over a test.
"""

from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import breaker, sites

SITE = "Medusync Site"


class BreakerCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		rows = frappe.get_all(SITE, fields=["name"], limit=1)
		if not rows:
			self.skipTest("no Medusync Site configured")
		self.site = rows[0].name
		self._before = frappe.db.get_value(
			SITE, self.site, ["consecutive_failures", "tripped_at", "trip_after"], as_dict=True
		)
		breaker.close(self.site)

	def tearDown(self):
		frappe.db.set_value(
			SITE,
			self.site,
			{
				"consecutive_failures": self._before.consecutive_failures or 0,
				"tripped_at": self._before.tripped_at,
				"trip_after": self._before.trip_after,
			},
			update_modified=False,
		)
		sites.clear_cache()
		super().tearDown()

	def _threshold(self):
		return breaker.threshold(self.site)

	def _fail(self, times):
		for _ in range(times):
			breaker.record_failure(self.site)


class TestCounting(BreakerCase):
	def test_one_failure_is_not_a_pattern(self):
		breaker.record_failure(self.site)
		self.assertFalse(breaker.is_tripped(self.site))

	def test_enough_of_them_is(self):
		self._fail(self._threshold())
		self.assertTrue(breaker.is_tripped(self.site))

	def test_one_success_closes_it(self):
		self._fail(self._threshold())
		breaker.record_success(self.site)
		self.assertFalse(breaker.is_tripped(self.site))
		self.assertEqual(breaker.state(self.site)["consecutive_failures"], 0)

	def test_a_success_partway_starts_the_count_again(self):
		# Consecutive is the whole point. A store that fails, works, fails
		# is flaky, not down, and retrying is the right answer.
		self._fail(self._threshold() - 1)
		breaker.record_success(self.site)
		breaker.record_failure(self.site)
		self.assertFalse(breaker.is_tripped(self.site))

	def test_a_rehearsal_never_counts(self):
		# Otherwise testing a mapping against a store that is down takes
		# real traffic down with it.
		for _ in range(self._threshold() * 2):
			breaker.record_failure(self.site, is_test=True)
		self.assertFalse(breaker.is_tripped(self.site))

	def test_and_a_rehearsal_cannot_close_it_either(self):
		self._fail(self._threshold())
		breaker.record_success(self.site, is_test=True)
		self.assertTrue(breaker.is_tripped(self.site))


class TestWhatItStops(BreakerCase):
	def test_an_open_breaker_lets_everything_through(self):
		self.assertTrue(breaker.allows(self.site))

	def test_a_tripped_one_does_not(self):
		self._fail(self._threshold())
		self.assertFalse(breaker.allows(self.site))

	def test_but_it_always_lets_a_probe_through(self):
		# Somebody has to knock, or it never learns the store came back.
		self._fail(self._threshold())
		self.assertTrue(breaker.allows(self.site, probe=True))

	def test_and_never_stops_a_rehearsal(self):
		# The operator is deliberately testing this store. Telling them
		# "the breaker is open" when they are trying to find out why is
		# the wrong answer.
		self._fail(self._threshold())
		self.assertTrue(breaker.allows(self.site, is_test=True))

	def test_an_unknown_store_is_not_something_to_block(self):
		self.assertTrue(breaker.allows("no-such-store"))


class TestOnTheDeliveryPath(BreakerCase):
	def _log(self, **over):
		spec = {
			"direction": "Outbound",
			"status": "Queued",
			"event": "customer.updated",
			"event_id": "breaker-%s" % frappe.generate_hash(length=8),
			"site": self.site,
		}
		spec.update(over)
		doc = frappe.new_doc("Medusync Log")
		doc.update(spec)
		doc.insert(ignore_permissions=True)
		self.addCleanup(
			lambda: frappe.db.exists("Medusync Log", doc.name)
			and frappe.delete_doc("Medusync Log", doc.name, force=1, ignore_permissions=True)
		)
		return doc

	def test_a_tripped_store_is_not_posted_to(self):
		from medusync import outbound

		self._fail(self._threshold())
		log = self._log()
		with patch("requests.post") as posted:
			outbound.deliver(log.name, "customer.updated", log.event_id, {"a": 1}, site_id=self.site)
		self.assertEqual(posted.call_count, 0)

	def test_and_the_row_says_why(self):
		from medusync import outbound

		self._fail(self._threshold())
		log = self._log()
		with patch("requests.post"):
			outbound.deliver(log.name, "customer.updated", log.event_id, {"a": 1}, site_id=self.site)
		log.reload()
		self.assertEqual(log.status, "Skipped")
		self.assertIn("not answering", (log.error or "").lower())

	def test_an_open_store_is_posted_to(self):
		from medusync import outbound

		log = self._log()
		with patch("requests.post") as posted:
			posted.return_value = frappe._dict(status_code=200, text='{"ok":true}')
			outbound.deliver(log.name, "customer.updated", log.event_id, {"a": 1}, site_id=self.site)
		self.assertEqual(posted.call_count, 1)
