# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""One translation per message, and the envelope kind says which side does it.

An `event` body carries the sender's own fieldnames: the receiver maps. A
`mapped` body carries the receiver's fieldnames: the receiver applies it as
it is. Both receivers already did this. `build_payload` was the one sender
that did not, so every mapped field whose two names differed was dropped on
the ERPNext → Medusa webhook, while the pull cron, which reads raw Frappe
rows, carried the same field fine.

Every pair here has a different name on each side on purpose. A same-named
pair survives either convention, which is how the drop stayed invisible.
"""

from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import api, envelope, outbound

TITLE = "T Wire Convention"


def _item(**values):
	fields = {
		"doctype": "Item",
		"name": "ITEM-WIRE-1",
		"item_code": "ITEM-WIRE-1",
		"item_name": "Wire Convention Tee",
		"description": "Cotton",
		"stock_uom": "Nos",
	}
	fields.update(values)
	doc = frappe._dict(fields)
	doc.as_dict = lambda **kw: dict(fields)
	return doc


class TestWireConvention(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		if frappe.db.exists("Medusync Mapping", TITLE):
			frappe.delete_doc("Medusync Mapping", TITLE, force=1, ignore_permissions=True)
		doc = frappe.new_doc("Medusync Mapping")
		doc.update(
			{
				"title": TITLE,
				"enabled": 0,
				"document_type": "Item",
				"direction": "Two-way",
				"key_field": "item_code",
				"medusa_entity": "probe_product",
			}
		)
		for row in (
			{"frappe_field": "item_code", "medusa_path": "handle", "direction": "Two-way"},
			{"frappe_field": "item_name", "medusa_path": "title", "direction": "Two-way"},
			{"frappe_field": "description", "medusa_path": "description", "direction": "Two-way"},
			{"frappe_field": "stock_uom", "medusa_path": "unit", "direction": "From Medusa"},
		):
			doc.append("field_map", row)
		doc.insert(ignore_permissions=True)
		self.mapping = doc

	def tearDown(self):
		if frappe.db.exists("Medusync Mapping", TITLE):
			frappe.delete_doc("Medusync Mapping", TITLE, force=1, ignore_permissions=True)
		super().tearDown()

	# ── Outbound: an event names things the way this site does ─────────

	def test_an_outbound_event_is_keyed_by_the_frappe_fieldname(self):
		data = outbound.build_payload(self.mapping, _item())
		self.assertEqual(data["item_code"], "ITEM-WIRE-1")
		self.assertEqual(data["item_name"], "Wire Convention Tee")
		self.assertEqual(data["description"], "Cotton")

	def test_the_medusa_path_never_appears_in_an_outbound_event(self):
		data = outbound.build_payload(self.mapping, _item())
		self.assertNotIn("handle", data)
		self.assertNotIn("title", data)

	def test_an_inbound_only_pair_still_stays_home(self):
		data = outbound.build_payload(self.mapping, _item())
		self.assertNotIn("stock_uom", data)
		self.assertNotIn("unit", data)

	def test_a_mapped_event_is_the_send_all_event_cut_down_to_the_map(self):
		# The pull cron reads raw Frappe rows and was always right. The
		# webhook must put the same shape on the wire, so Medusa can apply
		# one mapping to both transports.
		doc = _item()
		mapped = outbound.build_payload(self.mapping, doc)
		self.mapping.include_all_fields = 1
		everything = outbound.build_payload(self.mapping, doc)
		self.mapping.include_all_fields = 0
		for key, value in mapped.items():
			self.assertIn(key, everything)
			self.assertEqual(value, everything[key])

	# ── Inbound: an event from Medusa names things the way Medusa does ──

	def test_an_inbound_event_from_medusa_is_read_by_its_medusa_path(self):
		out = api._translate(self.mapping, {"handle": "tee", "title": "Tee", "unit": "Nos"})
		self.assertEqual(out["item_code"], "tee")
		self.assertEqual(out["item_name"], "Tee")
		self.assertEqual(out["stock_uom"], "Nos")

	def test_an_inbound_event_keyed_by_our_own_names_carries_nothing(self):
		# No second convention on the receiver. A body that arrived already
		# translated would be applied twice by a receiver that accepted
		# both, and that is exactly the bug this file exists to keep out.
		out = api._translate(self.mapping, {"item_code": "tee", "item_name": "Tee"})
		self.assertNotIn("item_code", out)
		self.assertNotIn("item_name", out)


class TestAMappedPushIsAppliedAsItIs(IntegrationTestCase):
	"""A `mapped` body is already in this site's fieldnames.

	Medusa ran the field map before sending, so the receiver hands the
	payload to the handler pack untouched. Running `_translate` over it
	would be the second translation this file exists to keep out.
	"""

	def setUp(self):
		super().setUp()
		self._logs = []

	def tearDown(self):
		for name in self._logs:
			if frappe.db.exists("Medusync Log", name):
				frappe.delete_doc("Medusync Log", name, force=1, ignore_permissions=True)
		super().tearDown()

	def test_the_handler_pack_receives_the_payload_untranslated(self):
		event_id = "test:wire-%s" % frappe.generate_hash(length=8)
		body = envelope.build(
			"todo.created",
			event_id,
			site_id="default",
			kind=envelope.KIND_MAPPED,
			doctype="ToDo",
			key_field="name",
			key_value="TODO-WIRE-NOPE",
			payload={"description": "already in our names", "priority": "Low"},
		)
		env = envelope.parse(body)
		received = {}

		def fake_upsert(**kw):
			received.update(kw)
			return {"doctype": "ToDo", "name": None, "status": "skipped", "reason": "test"}

		frappe.local.response = frappe._dict()
		with patch("medusync.handlers.get_mapped_upsert", return_value=fake_upsert):
			api._apply_mapped(env, event_id, "default")
		self._logs.extend(
			r.name for r in frappe.get_all("Medusync Log", filters={"event_id": event_id})
		)

		self.assertEqual(received["doctype"], "ToDo")
		self.assertEqual(received["key_field"], "name")
		self.assertEqual(received["key_value"], "TODO-WIRE-NOPE")
		self.assertEqual(
			received["payload"], {"description": "already in our names", "priority": "Low"}
		)
