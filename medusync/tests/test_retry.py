# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Outbound retries wait for a real backoff.

`frappe.enqueue` has no delay parameter, so re-enqueueing a failed
delivery immediately retried it within milliseconds and burned every
attempt during a single outage. A failed delivery now parks the row with
`next_attempt_at`; a minute sweep (`medusync.tasks.retry_due`) re-enqueues
rows whose time has come, and a row that used up its attempts becomes
`Poison`, which the sweep never touches again.

The sweep is global by design, and a shared dev site may hold other due
rows, so assertions are scoped to the rows each test creates.
"""

import json
from unittest.mock import patch

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import outbound, tasks


def _queued_log(**overrides):
	log = frappe.new_doc("Medusync Log")
	log.update(
		{
			"direction": "Outbound",
			"status": "Queued",
			"event": "customer.updated",
			"event_id": "test:" + frappe.generate_hash(length=8),
			"attempt": 1,
		}
	)
	log.update(overrides)
	log.insert(ignore_permissions=True)
	return log


def _calls_for(enqueue, *names):
	wanted = set(names)
	return [c for c in enqueue.call_args_list if c.kwargs.get("log_name") in wanted]


class TestRetryBackoff(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.settings = frappe.get_single("Medusync Settings")
		self._max_attempts = self.settings.max_attempts
		self._log_payloads = self.settings.log_payloads
		self._enabled = self.settings.enabled

	def tearDown(self):
		frappe.db.set_single_value(
			"Medusync Settings",
			{
				"max_attempts": self._max_attempts,
				"log_payloads": self._log_payloads,
				"enabled": self._enabled,
			},
		)
		frappe.clear_cache(doctype="Medusync Settings")
		super().tearDown()

	def _configure(self, **values):
		frappe.db.set_single_value("Medusync Settings", values)
		frappe.clear_cache(doctype="Medusync Settings")

	def test_delay_grows_with_attempts(self):
		self.assertEqual(outbound.retry_delay_seconds(1), 30)
		self.assertLess(outbound.retry_delay_seconds(1), outbound.retry_delay_seconds(2))
		self.assertLess(outbound.retry_delay_seconds(2), outbound.retry_delay_seconds(3))

	def test_transient_failure_parks_the_row_instead_of_re_enqueueing(self):
		self._configure(max_attempts=3, log_payloads=1)
		log = _queued_log()
		with patch("frappe.enqueue") as enqueue:
			outbound._retry_or_fail(log.name, "customer.updated", log.event_id, {"a": 1}, 1, "boom", status_code=0)
			enqueue.assert_not_called()
		row = frappe.db.get_value(
			"Medusync Log", log.name, ["status", "attempt", "next_attempt_at", "error"], as_dict=True
		)
		self.assertEqual(row.status, "Queued")
		self.assertEqual(row.attempt, 1)
		self.assertEqual(row.error, "boom")
		self.assertIsNotNone(row.next_attempt_at)
		self.assertGreater(get_datetime(row.next_attempt_at), now_datetime())

	def test_exhausted_attempts_park_the_row_as_poison(self):
		self._configure(max_attempts=2, log_payloads=1)
		log = _queued_log(attempt=2)
		outbound._retry_or_fail(log.name, "customer.updated", log.event_id, {"a": 1}, 2, "still down", status_code=503)
		row = frappe.db.get_value("Medusync Log", log.name, ["status", "next_attempt_at"], as_dict=True)
		self.assertEqual(row.status, "Poison")
		self.assertIsNone(row.next_attempt_at)

	def test_a_poison_row_is_never_swept_again(self):
		self._configure(max_attempts=3, log_payloads=1, enabled=1)
		dead = _queued_log(
			status="Poison",
			next_attempt_at=add_to_date(now_datetime(), seconds=-5),
			request_body=json.dumps({"x": 9}),
		)
		with patch("frappe.enqueue") as enqueue:
			tasks.retry_due(limit=10000)
			self.assertEqual(_calls_for(enqueue, dead.name), [])

	def test_payload_is_kept_for_the_retry_even_when_bodies_are_not_logged(self):
		self._configure(max_attempts=3, log_payloads=0)
		log = _queued_log()
		outbound._retry_or_fail(log.name, "customer.updated", log.event_id, {"kept": True}, 1, "boom")
		body = frappe.db.get_value("Medusync Log", log.name, "request_body")
		self.assertEqual(json.loads(body), {"kept": True})

	def test_sweep_enqueues_due_rows_once(self):
		self._configure(max_attempts=3, log_payloads=1, enabled=1)
		due = _queued_log(
			next_attempt_at=add_to_date(now_datetime(), seconds=-5),
			request_body=json.dumps({"x": 1}),
		)
		not_yet = _queued_log(
			next_attempt_at=add_to_date(now_datetime(), seconds=600),
			request_body=json.dumps({"x": 2}),
		)
		fresh = _queued_log(request_body=json.dumps({"x": 3}))  # never failed, no next_attempt_at
		with patch("frappe.enqueue") as enqueue:
			tasks.retry_due(limit=10000)
			mine = _calls_for(enqueue, due.name, not_yet.name, fresh.name)
			self.assertEqual(len(mine), 1)
			kwargs = mine[0].kwargs
			self.assertEqual(kwargs["log_name"], due.name)
			self.assertEqual(kwargs["attempt"], 2)
			self.assertEqual(kwargs["payload"], {"x": 1})
			self.assertEqual(kwargs["event_name"], "customer.updated")
			self.assertEqual(kwargs["event_id"], due.event_id)
		# claimed: a second sweep must not pick it again
		self.assertIsNone(frappe.db.get_value("Medusync Log", due.name, "next_attempt_at"))
		with patch("frappe.enqueue") as enqueue:
			tasks.retry_due(limit=10000)
			self.assertEqual(_calls_for(enqueue, due.name, not_yet.name, fresh.name), [])
		self.assertIsNotNone(frappe.db.get_value("Medusync Log", not_yet.name, "next_attempt_at"))
		self.assertIsNone(frappe.db.get_value("Medusync Log", fresh.name, "next_attempt_at"))

	def test_the_sweep_carries_the_site_and_the_envelope_kind(self):
		self._configure(max_attempts=3, log_payloads=1, enabled=1)
		row = _queued_log(
			event="mapping.upserted",
			site=None,
			next_attempt_at=add_to_date(now_datetime(), seconds=-5),
			request_body=json.dumps({"mapping": {"uid": "u1", "version": 2}}),
		)
		with patch("frappe.enqueue") as enqueue:
			tasks.retry_due(limit=10000)
			mine = _calls_for(enqueue, row.name)
			self.assertEqual(len(mine), 1)
			self.assertEqual(mine[0].kwargs["kind"], "mapping")

	def test_sweep_is_inert_when_sync_is_disabled(self):
		self._configure(max_attempts=3, log_payloads=1, enabled=0)
		_queued_log(next_attempt_at=add_to_date(now_datetime(), seconds=-5), request_body="{}")
		with patch("frappe.enqueue") as enqueue:
			tasks.retry_due()
			enqueue.assert_not_called()
