# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Every mapped field carries its own direction, including Don't Sync.

"Product images flow ERPNext to Medusa but never back" is a per-field
decision, so the field map is where it lives. Don't Sync is the fourth
value: the pair stays documented in the mapping but moves in neither
direction.
"""

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import api, outbound

TITLE = "T Field Direction"


class TestFieldDirection(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		if frappe.db.exists("Medusync Mapping", TITLE):
			frappe.delete_doc("Medusync Mapping", TITLE, force=1, ignore_permissions=True)
		doc = frappe.new_doc("Medusync Mapping")
		doc.update(
			{
				"title": TITLE,
				"enabled": 1,
				"document_type": "Customer",
				"direction": "Two-way",
				"key_field": "email_id",
				"medusa_entity": "customer",
			}
		)
		for row in (
			{"frappe_field": "email_id", "medusa_path": "email", "direction": "Two-way"},
			{"frappe_field": "customer_name", "medusa_path": "name_out", "direction": "To Medusa"},
			{"frappe_field": "customer_group", "medusa_path": "group_in", "direction": "From Medusa"},
			{"frappe_field": "image", "medusa_path": "thumbnail", "direction": "Don't Sync"},
		):
			doc.append("field_map", row)
		doc.insert(ignore_permissions=True)
		self.mapping = doc

	def tearDown(self):
		if frappe.db.exists("Medusync Mapping", TITLE):
			frappe.delete_doc("Medusync Mapping", TITLE, force=1, ignore_permissions=True)
		super().tearDown()

	def test_outbound_payload_skips_dont_sync_and_inbound_only_fields(self):
		doc = frappe._dict(
			{
				"doctype": "Customer",
				"name": "CUST-FD-1",
				"email_id": "fd@example.com",
				"customer_name": "Field Direction",
				"customer_group": "Commercial",
				"image": "/files/secret.png",
				"as_dict": lambda **kw: dict(
					doctype="Customer",
					name="CUST-FD-1",
					email_id="fd@example.com",
					customer_name="Field Direction",
					customer_group="Commercial",
					image="/files/secret.png",
				),
			}
		)
		payload = outbound.build_payload(self.mapping, doc)
		self.assertEqual(payload["email"], "fd@example.com")
		self.assertEqual(payload["name_out"], "Field Direction")
		self.assertNotIn("thumbnail", payload)
		self.assertNotIn("group_in", payload)

	def test_inbound_translate_skips_dont_sync_and_outbound_only_fields(self):
		data = {
			"email": "fd@example.com",
			"name_out": "Should Not Land",
			"group_in": "Commercial",
			"thumbnail": "/files/from-medusa.png",
		}
		out = api._translate(self.mapping, data)
		self.assertEqual(out["email_id"], "fd@example.com")
		self.assertEqual(out["customer_group"], "Commercial")
		self.assertNotIn("customer_name", out)
		self.assertNotIn("image", out)

	def test_dont_sync_survives_a_round_trip_through_the_canonical_form(self):
		from medusync import mapping_sync

		canon = mapping_sync.to_canonical(self.mapping)
		by_field = {f["erpnext_field"]: f["direction"] for f in canon["fields"]}
		self.assertEqual(by_field["image"], "none")
		self.assertEqual(by_field["customer_name"], "pull")
		self.assertEqual(by_field["customer_group"], "push")
		self.assertEqual(by_field["email_id"], "both")
