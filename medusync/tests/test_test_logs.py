# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""A test run leaves a trace, and the trace must not be mistaken for work.

The studio can send a real signed request to the far side — that is the
only way to prove the secret, the network and the far side's own verdict
in one go. The row it leaves behind looks exactly like a real delivery,
which is the problem: the retry sweep would retry it, the duplicate guard
would suppress a genuine event that follows it, and the site health clock
would say the store was reached when nothing real was sent.

So a test row is marked, and every reader of the log skips marked rows.
They are also pruned on a much shorter clock than real ones: nobody
audits a rehearsal a fortnight later.
"""

from unittest.mock import patch

import frappe
from frappe.utils import add_to_date, now_datetime

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import outbound, tasks


class TestLogCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._rows = []

	def tearDown(self):
		for name in self._rows:
			if frappe.db.exists("Medusync Log", name):
				frappe.delete_doc("Medusync Log", name, force=1, ignore_permissions=True)
		super().tearDown()

	def _log(self, **over):
		spec = {
			"direction": "Outbound",
			"status": "Queued",
			"event": "customer.updated",
			"event_id": "unit-%s" % frappe.generate_hash(length=8),
			"site": "default",
		}
		spec.update(over)
		doc = frappe.new_doc("Medusync Log")
		doc.update(spec)
		doc.insert(ignore_permissions=True)
		self._rows.append(doc.name)
		return doc


class TestARehearsalIsMarked(TestLogCase):
	def test_a_test_delivery_says_it_was_a_test(self):
		sent = []
		with patch("medusync.outbound.send", side_effect=lambda *a, **k: sent.append((a, k))):
			outbound.emit(
				"customer.updated", {"hello": "world"}, ref="studio-1", is_test=True
			)
		row = frappe.get_doc("Medusync Log", frappe.get_all(
			"Medusync Log", filters={"event_id": ["like", "test:%"]},
			order_by="creation desc", limit=1,
		)[0].name)
		self._rows.append(row.name)
		self.assertTrue(row.is_test)
		self.assertTrue(row.event_id.startswith("test:"))

	def test_an_ordinary_delivery_does_not(self):
		sent = []
		with patch("medusync.outbound.send", side_effect=lambda *a, **k: sent.append((a, k))):
			outbound.emit("customer.updated", {"hello": "world"}, ref="studio-2")
		row = frappe.get_all(
			"Medusync Log",
			filters={"event": "customer.updated", "direction": "Outbound"},
			fields=["name", "is_test", "event_id"],
			order_by="creation desc",
			limit=1,
		)[0]
		self._rows.append(row.name)
		self.assertFalse(row.is_test)
		self.assertFalse(row.event_id.startswith("test:"))


class TestNobodyMistakesItForWork(TestLogCase):
	def test_the_retry_sweep_leaves_it_alone(self):
		# A rehearsal that failed is information, not a delivery owed to
		# anyone. Retrying it would send a fabricated payload for real.
		due = add_to_date(now_datetime(), seconds=-60)
		real = self._log(next_attempt_at=due, attempt=1)
		rehearsal = self._log(next_attempt_at=due, attempt=1, is_test=1, event_id="test:x")
		queued = []
		with patch("frappe.enqueue", side_effect=lambda *a, **k: queued.append(k.get("log_name"))):
			tasks.retry_due()
		self.assertIn(real.name, queued)
		self.assertNotIn(rehearsal.name, queued)

	def test_a_rehearsed_success_does_not_suppress_the_real_thing(self):
		# `skip_unchanged` asks "did this exact payload already succeed?".
		# A test run must never be the reason a genuine change is dropped.
		self.assertFalse(
			outbound.already_delivered(
				doctype="Customer", docname="whoever", payload_hash="abc", site_id="default"
			)
		)
		self._log(
			status="Success",
			document_type=None,
			payload_hash="abc",
			is_test=1,
			event_id="test:y",
		)
		self.assertFalse(
			outbound.already_delivered(
				doctype=None, docname=None, payload_hash="abc", site_id="default"
			)
		)

	def test_a_rehearsal_does_not_move_the_site_health_clock(self):
		before = frappe.db.get_value("Medusync Site", "default", "last_seen_at")
		outbound._mark_site_seen("default", is_test=True)
		self.assertEqual(frappe.db.get_value("Medusync Site", "default", "last_seen_at"), before)


class TestTheyAreSweptUpSooner(TestLogCase):
	def test_yesterdays_rehearsal_is_gone_today(self):
		row = self._log(is_test=1, event_id="test:old", status="Success")
		old = add_to_date(now_datetime(), days=-3)
		frappe.db.set_value("Medusync Log", row.name, "creation", old, update_modified=False)
		tasks.prune_logs()
		self.assertFalse(frappe.db.exists("Medusync Log", row.name))

	def test_a_real_row_of_the_same_age_survives(self):
		row = self._log(status="Success")
		old = add_to_date(now_datetime(), days=-3)
		frappe.db.set_value("Medusync Log", row.name, "creation", old, update_modified=False)
		tasks.prune_logs()
		self.assertTrue(frappe.db.exists("Medusync Log", row.name))

	def test_todays_rehearsal_is_still_there_to_read(self):
		row = self._log(is_test=1, event_id="test:fresh", status="Success")
		tasks.prune_logs()
		self.assertTrue(frappe.db.exists("Medusync Log", row.name))
