# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""A rehearsal that crosses the wire.

The local dry run proves the translation and nothing else. It cannot
prove the shared secret, the network between two machines, the replay
window, or the far side's own opinion of the payload — which between them
are most of the reasons a sync actually fails.

So a rehearsal can travel: an ordinary signed request carrying `dry_run`,
which the receiver checks exactly as it checks anything else and then
stops before the write. What comes back is the plan, not a result.
"""

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import api, envelope


class TestTheFlagTravels(IntegrationTestCase):
	def test_an_ordinary_envelope_does_not_carry_it(self):
		# Absent rather than false, so a receiver that predates the flag
		# sees exactly the body it has always seen.
		body = envelope.build("customer.updated", "e1", site_id="default", data={})
		self.assertNotIn("dry_run", body)
		self.assertFalse(envelope.parse(body).dry_run)

	def test_a_rehearsal_says_so(self):
		body = envelope.build("customer.updated", "e2", site_id="default", data={}, dry_run=True)
		self.assertTrue(body["dry_run"])
		self.assertTrue(envelope.parse(body).dry_run)

	def test_it_survives_the_round_trip_on_a_mapped_push(self):
		body = envelope.build(
			"product.updated",
			"e3",
			site_id="default",
			kind=envelope.KIND_MAPPED,
			doctype="Item",
			key_field="item_code",
			key_value="whatever",
			payload={"item_name": "x"},
			dry_run=True,
		)
		parsed = envelope.parse(body)
		self.assertTrue(parsed.dry_run)
		self.assertEqual(parsed.kind, envelope.KIND_MAPPED)


class TestTheReceiverPlansAndStops(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._logs = []

	def tearDown(self):
		for name in self._logs:
			if frappe.db.exists("Medusync Log", name):
				frappe.delete_doc("Medusync Log", name, force=1, ignore_permissions=True)
		super().tearDown()

	def _rehearse(self, **kw):
		"""Call the receiver the way a request would.

		`_respond` writes into `frappe.local.response` and returns nothing,
		because Frappe wraps a whitelisted return value in `message` and
		the wire contract is flat. So the answer is read from there.
		"""
		event_id = "test:remote-%s" % frappe.generate_hash(length=8)
		body = envelope.build(kw.pop("event"), event_id, site_id="default", dry_run=True, **kw)
		env = envelope.parse(body)
		frappe.local.response = frappe._dict()
		api._rehearse(env, event_id, "default")
		response = dict(frappe.local.response)
		rows = frappe.get_all("Medusync Log", filters={"event_id": event_id}, fields=["name"])
		self._logs.extend(r.name for r in rows)
		return response, rows

	def test_it_reports_what_a_catalogue_write_would_do(self):
		item = frappe.get_all("Item", fields=["name"], limit=1)
		if not item:
			self.skipTest("no Item on this site")
		response, _ = self._rehearse(
			event="product.updated",
			kind=envelope.KIND_MAPPED,
			doctype="Item",
			key_field="item_code",
			key_value=item[0].name,
			payload={"item_name": "Renamed by a rehearsal"},
		)
		result = response["result"]
		# The catalogue guard is part of the answer, so the rehearsal tells
		# the truth about a store whose updates ERPNext will not take.
		self.assertEqual(result["action"], "skipped")
		self.assertEqual(result["reason"], "catalogue-protected")

	def test_it_changes_nothing(self):
		rows = frappe.get_all("Item", fields=["name", "item_name"], limit=1)
		if not rows:
			self.skipTest("no Item on this site")
		item = rows[0]
		before = frappe.db.count("Item")
		self._rehearse(
			event="product.updated",
			kind=envelope.KIND_MAPPED,
			doctype="Item",
			key_field="item_code",
			key_value=item.name,
			payload={"item_name": "Renamed by a rehearsal"},
		)
		self.assertEqual(frappe.db.count("Item"), before)
		self.assertEqual(frappe.db.get_value("Item", item.name, "item_name"), item.item_name)

	def test_the_row_it_leaves_is_marked_a_rehearsal(self):
		response, rows = self._rehearse(event="nothing.mapped.to.this", data={})
		self.assertTrue(rows)
		row = frappe.get_doc("Medusync Log", rows[0].name)
		self.assertTrue(row.is_test)
		self.assertEqual(row.status, "Skipped")
		self.assertEqual(row.direction, "Inbound")

	def test_an_event_nothing_maps_is_an_answer_not_an_error(self):
		# Telling the sender to retry would be wrong twice over: nothing
		# went wrong, and nothing will be different next time.
		response, _ = self._rehearse(event="nothing.mapped.to.this", data={})
		self.assertTrue(response["ok"])
		self.assertEqual(response["status"], "dry_run")
		self.assertEqual(response["result"]["action"], "skipped")
		self.assertIn("no inbound mapping", response["result"]["reason"])
