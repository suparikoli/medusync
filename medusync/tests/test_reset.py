# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Starting over, with both hands on the switch.

A hard reset throws away configuration that took somebody a week to get
right, so the interesting question is not what it does but who is allowed
to ask for it. The answer here is: nobody, alone. Each side generates a
secret and shows it once; each side has to be handed the other's. Neither
can reset itself and neither can reset the other.

The secrets are 32 random bytes, live for three minutes, work once, and
are stored only as a hash. Most of these tests are about those four
properties, because they are what stands between a reset and an accident.

The rest are about what survives. A reset that took the customer ids with
it would not be a reset, it would be a divorce: the two systems would
still hold the same records and no longer know it.
"""

import json
from unittest.mock import patch

import frappe
from frappe.utils import add_to_date, now_datetime

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import defaults, reset

REQUEST = "Medusync Reset Request"
MAPPING = "Medusync Mapping"


class ResetCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._push = patch("medusync.mapping_sync.push_mapping", return_value=None)
		self._push.start()
		# Nothing in these tests should reach the network. A verify that
		# actually left the machine would be a test that fails on a train.
		self._deliver = patch("medusync.reset.deliver_verify", return_value={"ok": True})
		self._deliver_mock = self._deliver.start()
		self._made = []
		self._mappings = []
		self.site = self._some_site()
		# `perform` commits, as a background job should. So the usual test
		# rollback does not undo it and this fixture has to: what mappings
		# existed, whether each was on, and which default set was recorded.
		self._before = {
			row.name: (row.mapping_uid, row.enabled)
			for row in frappe.get_all(MAPPING, fields=["name", "mapping_uid", "enabled"])
		}
		self._defaults_version = frappe.db.get_single_value("Medusync Settings", "defaults_version")

	def tearDown(self):
		for name in self._made:
			if frappe.db.exists(REQUEST, name):
				frappe.delete_doc(REQUEST, name, force=1, ignore_permissions=True)
		for name in self._mappings:
			if frappe.db.exists(MAPPING, name):
				frappe.delete_doc(MAPPING, name, force=1, ignore_permissions=True)
		self._put_the_site_back()
		self._deliver.stop()
		self._push.stop()
		super().tearDown()

	def _put_the_site_back(self):
		for row in frappe.get_all(MAPPING, fields=["name", "mapping_uid", "enabled"]):
			if row.name not in self._before:
				if defaults.owns(row.mapping_uid):
					frappe.delete_doc(MAPPING, row.name, force=1, ignore_permissions=True)
				continue
			was_enabled = self._before[row.name][1]
			if row.enabled != was_enabled:
				frappe.db.set_value(MAPPING, row.name, "enabled", was_enabled, update_modified=False)
		frappe.db.set_single_value("Medusync Settings", "defaults_version", self._defaults_version)
		frappe.db.commit()

	def _some_site(self):
		rows = frappe.get_all("Medusync Site", fields=["site_id"], limit=1)
		if not rows:
			self.skipTest("no Medusync Site configured")
		return rows[0].site_id

	def _request(self):
		out = reset.request(self.site)
		self._made.append(out["name"])
		return out


class TestAskingForOne(ResetCase):
	def test_it_hands_back_a_secret_once(self):
		out = self._request()
		self.assertTrue(out["secret"])
		self.assertEqual(out["site"], self.site)

	def test_the_secret_is_long_enough_to_be_worth_nothing_to_guess(self):
		secret = self._request()["secret"]
		# 32 bytes, url-safe base64. Anything shorter is a password.
		self.assertGreaterEqual(len(secret), 40)

	def test_two_requests_never_produce_the_same_secret(self):
		first = self._request()["secret"]
		second = self._request()["secret"]
		self.assertNotEqual(first, second)

	def test_the_secret_is_not_in_the_record(self):
		out = self._request()
		row = frappe.get_doc(REQUEST, out["name"])
		stored = json.dumps(row.as_dict(), default=str)
		self.assertNotIn(out["secret"], stored)
		self.assertTrue(row.secret_hash)

	def test_it_dies_in_three_minutes(self):
		out = self._request()
		row = frappe.get_doc(REQUEST, out["name"])
		remaining = (row.expires_at - now_datetime()).total_seconds()
		self.assertGreater(remaining, 60)
		self.assertLessEqual(remaining, reset.WINDOW_SECONDS + 5)

	def test_asking_again_retires_the_last_one(self):
		# One live secret per store. Two would mean an operator holding a
		# slip of paper they can no longer tell apart.
		first = self._request()
		second = self._request()
		self.assertEqual(frappe.db.get_value(REQUEST, first["name"], "status"), "Cancelled")
		self.assertEqual(frappe.db.get_value(REQUEST, second["name"], "status"), "Pending")


class TestProvingIt(ResetCase):
	def test_the_right_secret_is_accepted(self):
		out = self._request()
		result = reset.verify_local(out["secret"])
		self.assertTrue(result["ok"])
		self.assertEqual(result["name"], out["name"])
		self.assertTrue(frappe.db.get_value(REQUEST, out["name"], "local_verified_at"))

	def test_a_wrong_secret_is_refused(self):
		out = self._request()
		result = reset.verify_local("not-the-secret-at-all")
		self.assertFalse(result["ok"])
		self.assertFalse(frappe.db.get_value(REQUEST, out["name"], "local_verified_at"))

	def test_a_wrong_secret_does_not_burn_the_request(self):
		# Otherwise a typo, or anyone who can reach the endpoint, costs the
		# operator the three minutes and the trip.
		out = self._request()
		reset.verify_local("wrong")
		self.assertTrue(reset.verify_local(out["secret"])["ok"])

	def test_the_same_secret_does_not_work_twice(self):
		out = self._request()
		self.assertTrue(reset.verify_local(out["secret"])["ok"])
		second = reset.verify_local(out["secret"])
		self.assertFalse(second["ok"])

	def test_an_expired_secret_is_refused(self):
		out = self._request()
		frappe.db.set_value(
			REQUEST, out["name"], "expires_at", add_to_date(now_datetime(), seconds=-1),
			update_modified=False,
		)
		result = reset.verify_local(out["secret"])
		self.assertFalse(result["ok"])
		self.assertIn("expired", result["reason"])

	def test_a_cancelled_request_is_refused(self):
		out = self._request()
		self._request()  # supersedes it
		self.assertFalse(reset.verify_local(out["secret"])["ok"])


class TestTheSecretNeverReachesTheLog(ResetCase):
	def test_verifying_over_the_wire_logs_nothing_readable(self):
		from medusync import api, envelope

		out = self._request()
		event_id = "reset-%s" % frappe.generate_hash(length=8)
		body = envelope.build(
			reset.VERIFY_EVENT, event_id, site_id=self.site, data={"secret": out["secret"]}
		)
		env = envelope.parse(body)
		frappe.local.response = frappe._dict()
		api._reset_verify(env, event_id, self.site)

		rows = frappe.get_all(
			"Medusync Log",
			filters={"event_id": event_id},
			fields=["name", "request_body", "response_body", "error"],
		)
		self.assertTrue(rows, "the attempt should still be audited")
		for row in rows:
			blob = json.dumps(dict(row), default=str)
			self.assertNotIn(out["secret"], blob)
			frappe.delete_doc("Medusync Log", row.name, force=1, ignore_permissions=True)


class TestBothSidesOrNeither(ResetCase):
	def test_our_side_alone_is_not_enough(self):
		out = self._request()
		reset.verify_local(out["secret"])  # they hold ours
		self.assertFalse(reset.ready(out["name"]))

	def test_their_side_alone_is_not_enough(self):
		out = self._request()
		reset.confirm_remote(out["name"], "whatever-they-gave-us")  # we hold theirs
		self.assertFalse(reset.ready(out["name"]))

	def test_both_together_are(self):
		out = self._request()
		reset.verify_local(out["secret"])
		reset.confirm_remote(out["name"], "whatever-they-gave-us")
		self.assertTrue(reset.ready(out["name"]))

	def test_the_far_side_refusing_our_secret_is_not_a_confirmation(self):
		out = self._request()
		self._deliver_mock.return_value = {"ok": False, "reason": "no matching request"}
		result = reset.confirm_remote(out["name"], "stale-secret")
		self.assertFalse(result["ok"])
		self.assertFalse(frappe.db.get_value(REQUEST, out["name"], "remote_confirmed_at"))


class TestWhatAResetDoes(ResetCase):
	def setUp(self):
		super().setUp()
		self.request_row = self._request()
		reset.verify_local(self.request_row["secret"])
		reset.confirm_remote(self.request_row["name"], "theirs")

	def _handmade(self):
		doc = frappe.new_doc(MAPPING)
		doc.update(
			{
				"title": "Hand written, survives a reset",
				"enabled": 0,
				"document_type": "Customer",
				"direction": "To Medusa",
				"docevents": "on_update",
				"key_field": "name",
			}
		)
		doc.insert(ignore_permissions=True)
		self._mappings.append(doc.name)
		from medusync import studio

		studio.record_result(doc.name, passed=True, report="fixture")
		frappe.db.set_value(MAPPING, doc.name, "enabled", 1, update_modified=False)
		return doc

	def test_the_defaults_come_back_switched_off(self):
		reset.perform(self.request_row["name"])
		for spec in defaults.default_mappings():
			name = frappe.db.get_value(MAPPING, {"mapping_uid": spec["uid"]}, "name")
			self.assertTrue(name, spec["uid"])
			self.assertFalse(frappe.db.get_value(MAPPING, name, "enabled"))

	def test_a_mapping_somebody_wrote_is_switched_off_but_kept(self):
		# Deleting it would throw away work over a configuration change.
		# Leaving it running would mean the reset changed nothing.
		mine = self._handmade()
		reset.perform(self.request_row["name"])
		self.assertTrue(frappe.db.exists(MAPPING, mine.name))
		self.assertFalse(frappe.db.get_value(MAPPING, mine.name, "enabled"))

	def test_the_log_is_cleared(self):
		row = frappe.new_doc("Medusync Log")
		row.update(
			{
				"direction": "Outbound",
				"status": "Queued",
				"event": "customer.updated",
				"event_id": "reset-clears-%s" % frappe.generate_hash(length=6),
				"site": self.site,
			}
		)
		row.insert(ignore_permissions=True)
		reset.perform(self.request_row["name"])
		self.assertFalse(frappe.db.exists("Medusync Log", row.name))

	def test_the_stores_and_their_secrets_survive(self):
		# Losing these would mean the reset also disconnected the site, and
		# an operator would have to re-pair from scratch to recover from a
		# configuration mistake.
		before = frappe.get_all("Medusync Site", fields=["name"])
		hash_before = frappe.db.get_value("Medusync Site", self.site, "medusa_url")
		reset.perform(self.request_row["name"])
		self.assertEqual(
			{r.name for r in frappe.get_all("Medusync Site", fields=["name"])},
			{r.name for r in before},
		)
		self.assertEqual(frappe.db.get_value("Medusync Site", self.site, "medusa_url"), hash_before)

	def test_what_the_operator_excluded_stays_excluded(self):
		# These are decisions about individual documents. Clearing them
		# would silently start syncing something somebody deliberately
		# stopped, which is the one thing a reset must not do quietly.
		before = frappe.db.count("Medusync Exclusion")
		reset.perform(self.request_row["name"])
		self.assertEqual(frappe.db.count("Medusync Exclusion"), before)

	def test_the_cross_system_ids_survive(self):
		rows = frappe.get_all(
			"Customer", filters={"medusa_customer_id": ["is", "set"]}, fields=["name", "medusa_customer_id"], limit=1
		)
		if not rows:
			self.skipTest("no linked Customer to check")
		before = rows[0]
		reset.perform(self.request_row["name"])
		self.assertEqual(
			frappe.db.get_value("Customer", before.name, "medusa_customer_id"),
			before.medusa_customer_id,
		)

	def test_it_tells_nobody_about_the_mappings_it_rewrote(self):
		# Both sides restore the same identifiers at the same moment. If
		# each pushed its copy, the two would collide on version and the
		# conflict rule would pick a winner nobody asked for.
		with patch("medusync.mapping_sync.push_mapping") as pushed:
			reset.perform(self.request_row["name"])
		self.assertEqual(pushed.call_count, 0)

	def test_the_request_records_what_happened(self):
		reset.perform(self.request_row["name"])
		row = frappe.get_doc(REQUEST, self.request_row["name"])
		self.assertEqual(row.status, "Completed")
		self.assertTrue(row.completed_at)
		self.assertTrue(row.reset_report)
		self.assertIn("mappings", row.reset_report)

	def test_it_refuses_to_run_half_verified(self):
		out = self._request()
		reset.verify_local(out["secret"])
		with self.assertRaises(frappe.ValidationError):
			reset.perform(out["name"])
