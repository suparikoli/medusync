# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""A mapped field can carry a fixed value instead of a Medusa path.

Some mandatory ERPNext fields have no counterpart in a store at all.
`Item.item_group` and `Item.stock_uom` are mandatory Links with no default,
and nothing about a Medusa product answers either — so without a fixed
value a product push cannot satisfy the DocType. `Customer.customer_type`
is the same shape.

A fixed value only ever flows INTO this site. It is our answer to ERPNext's
requirement, not a fact about the store, so sending it back out to Medusa
would invent a field on the store's record.
"""

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import api, mapping_sync, outbound

TITLE = "T Fixed Value"


class TestFixedValueFields(IntegrationTestCase):
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
			{"frappe_field": "item_group", "constant_value": "Products", "direction": "Two-way"},
			{"frappe_field": "stock_uom", "constant_value": "Nos", "direction": "Two-way"},
		):
			doc.append("field_map", row)
		doc.insert(ignore_permissions=True)
		self.mapping = doc

	def tearDown(self):
		if frappe.db.exists("Medusync Mapping", TITLE):
			frappe.delete_doc("Medusync Mapping", TITLE, force=1, ignore_permissions=True)
		super().tearDown()

	def test_a_row_with_no_medusa_path_is_allowed_when_it_has_a_fixed_value(self):
		rows = {r.frappe_field: r for r in self.mapping.field_map}
		self.assertEqual(rows["item_group"].constant_value, "Products")
		self.assertFalse(rows["item_group"].medusa_path)

	def test_inbound_writes_the_fixed_value_even_though_medusa_never_sent_it(self):
		translated = api._translate(self.mapping, {"handle": "t-shirt"})
		self.assertEqual(translated["item_code"], "t-shirt")
		self.assertEqual(translated["item_group"], "Products")
		self.assertEqual(translated["stock_uom"], "Nos")

	def test_inbound_fixed_value_wins_over_anything_medusa_sends(self):
		# The store has no say in it: the value exists to satisfy ERPNext.
		translated = api._translate(self.mapping, {"handle": "t-shirt", "item_group": "Junk"})
		self.assertEqual(translated["item_group"], "Products")

	def test_outbound_payload_never_carries_a_fixed_value(self):
		doc = frappe._dict(
			{
				"doctype": "Item",
				"name": "ITEM-FV-1",
				"item_code": "t-shirt",
				"item_group": "Products",
				"stock_uom": "Nos",
				"as_dict": lambda **kw: {
					"doctype": "Item",
					"name": "ITEM-FV-1",
					"item_code": "t-shirt",
					"item_group": "Products",
					"stock_uom": "Nos",
				},
			}
		)
		data = outbound.build_payload(self.mapping, doc)
		self.assertEqual(data["item_code"], "t-shirt")
		self.assertNotIn("item_group", data)
		self.assertNotIn("stock_uom", data)

	def test_the_fixed_value_travels_to_the_other_side(self):
		canon = mapping_sync.to_canonical(self.mapping)
		by_field = {f["erpnext_field"]: f for f in canon["fields"]}
		self.assertEqual(by_field["item_group"].get("constant"), "Products")
		# An ordinary pair carries no constant at all, rather than a null.
		self.assertNotIn("constant", by_field["item_code"])

	def test_a_fixed_value_arriving_from_medusa_is_stored(self):
		canon = mapping_sync.to_canonical(self.mapping)
		canon["uid"] = "uid-fixed-value-test"
		canon["version"] = 99
		canon["name"] = TITLE + " Applied"
		result = mapping_sync.apply_canonical(canon)
		self.addCleanup(
			frappe.delete_doc,
			"Medusync Mapping",
			result["name"],
			force=1,
			ignore_permissions=True,
		)
		applied = frappe.get_doc("Medusync Mapping", result["name"])
		rows = {r.frappe_field: r for r in applied.field_map}
		self.assertEqual(rows["item_group"].constant_value, "Products")

	def test_changing_a_fixed_value_invalidates_the_rehearsal(self):
		# The rehearsal proved what "Products" writes. Switching it to
		# "Raw Material" writes something nobody checked, so the pass that
		# lets the mapping be switched on must stop matching.
		before = self.mapping.test_signature()
		for row in self.mapping.field_map:
			if row.frappe_field == "item_group":
				row.constant_value = "Raw Material"
		self.assertNotEqual(before, self.mapping.test_signature())

	def test_a_fixed_value_is_not_confused_with_a_path_of_the_same_text(self):
		as_constant = self.mapping.test_signature()
		for row in self.mapping.field_map:
			if row.frappe_field == "item_group":
				row.constant_value = None
				row.medusa_path = "Products"
		self.assertNotEqual(as_constant, self.mapping.test_signature())
