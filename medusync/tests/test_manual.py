# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Doing it by hand, when waiting for the next event is not an answer.

Three moments call for it. A mapping was just enabled and the store knows
nothing about the two thousand records that already exist. Something went
wrong for an hour, the rows gave up, and the cause is fixed. Somebody is
looking at one record and wants it over there now.

All three are the same operation with a different filter, and all three go
through the ordinary path — the same payload builder, the same selection
rules, the same log. A manual push that took a shortcut would be a second
implementation of the sync, and the one people reach for when something is
already wrong.
"""

from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import manual

MAPPING = "Medusync Mapping"
LOG = "Medusync Log"


class ManualCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._push = patch("medusync.mapping_sync.push_mapping", return_value=None)
		self._push.start()
		self._rows = []
		site = frappe.get_all("Medusync Site", fields=["site_id"], limit=1)
		if not site:
			self.skipTest("no Medusync Site configured")
		self.site = site[0].site_id

	def tearDown(self):
		for name in self._rows:
			if frappe.db.exists(LOG, name):
				frappe.delete_doc(LOG, name, force=1, ignore_permissions=True)
		self._push.stop()
		super().tearDown()

	def _log(self, **over):
		spec = {
			"direction": "Outbound",
			"status": "Poison",
			"event": "customer.updated",
			"event_id": "manual-%s" % frappe.generate_hash(length=8),
			"site": self.site,
			"attempt": 3,
		}
		spec.update(over)
		doc = frappe.new_doc(LOG)
		doc.update(spec)
		doc.insert(ignore_permissions=True)
		self._rows.append(doc.name)
		return doc


class TestResyncingWhatGaveUp(ManualCase):
	def test_a_row_that_gave_up_is_put_back_in_the_queue(self):
		row = self._log()
		result = manual.resync_failed()
		self.assertIn(row.name, result["requeued"])
		self.assertEqual(frappe.db.get_value(LOG, row.name, "status"), "Queued")

	def test_and_its_attempts_start_again(self):
		# Otherwise it is one attempt from giving up a second time, which
		# is not what "re-sync" means to the person clicking it.
		row = self._log(attempt=3)
		manual.resync_failed()
		self.assertEqual(frappe.db.get_value(LOG, row.name, "attempt"), 0)

	def test_something_that_worked_is_left_alone(self):
		row = self._log(status="Success")
		result = manual.resync_failed()
		self.assertNotIn(row.name, result["requeued"])

	def test_a_rehearsal_is_never_re_sent(self):
		# It would put a fabricated payload on the wire for real.
		row = self._log(is_test=1, event_id="test:manual-%s" % frappe.generate_hash(length=6))
		result = manual.resync_failed()
		self.assertNotIn(row.name, result["requeued"])

	def test_one_store_can_be_re_synced_without_the_others(self):
		mine = self._log()
		result = manual.resync_failed(site_id=self.site)
		self.assertIn(mine.name, result["requeued"])
		result = manual.resync_failed(site_id="some-other-store")
		self.assertNotIn(mine.name, result["requeued"])

	def test_it_says_it_did_nothing_rather_than_pretending(self):
		result = manual.resync_failed(site_id="a-store-with-no-failures")
		self.assertEqual(result["requeued"], [])
		self.assertEqual(result["count"], 0)


class TestPushingEverything(ManualCase):
	def _mapping(self, **over):
		spec = {
			"title": "Manual push probe %s" % frappe.generate_hash(length=5),
			"enabled": 0,
			"document_type": "Customer",
			"direction": "To Medusa",
			"docevents": "on_update",
			"key_field": "name",
		}
		spec.update(over)
		doc = frappe.new_doc(MAPPING)
		doc.update(spec)
		doc.append("field_map", {"frappe_field": "customer_name", "medusa_path": "name"})
		doc.insert(ignore_permissions=True)
		self.addCleanup(
			lambda: frappe.db.exists(MAPPING, doc.name)
			and frappe.delete_doc(MAPPING, doc.name, force=1, ignore_permissions=True)
		)
		return doc

	def _enabled_mapping(self, **over):
		"""An enabled mapping, switched on in the table.

		The rehearsal gate governs somebody switching one on through the
		form; it is not this test's subject and going through it here would
		only test the gate a second time.
		"""
		doc = self._mapping(**over)
		frappe.db.set_value(MAPPING, doc.name, "enabled", 1, update_modified=False)
		doc.reload()
		return doc

	def test_it_hands_the_work_to_the_existing_backfill(self):
		# Not a second implementation of the sync. The one people reach for
		# when something is already wrong is the worst place to have a
		# separate code path.
		mapping = self._enabled_mapping()
		with patch("medusync.backfill.run", return_value={"queued": 7}) as ran:
			result = manual.push_all(mapping.name, limit=5)
		self.assertTrue(ran.called)
		self.assertEqual(ran.call_args.kwargs["mapping"], mapping.name)
		self.assertEqual(ran.call_args.kwargs["limit"], 5)
		self.assertEqual(result["queued"], 7)

	def test_a_mapping_the_store_owns_has_nothing_to_push(self):
		mapping = self._enabled_mapping(direction="From Medusa", docevents="")
		result = manual.push_all(mapping.name)
		self.assertFalse(result["ok"])
		self.assertIn("From Medusa", result["message"])

	def test_a_mapping_that_is_switched_off_is_refused(self):
		# Pushing two thousand records through a rule nobody has enabled is
		# not something to do by accident.
		mapping = self._mapping()
		result = manual.push_all(mapping.name)
		self.assertFalse(result["ok"])
		self.assertIn("switched off", result["message"])
