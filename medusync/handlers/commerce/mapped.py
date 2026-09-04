# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Canonical-mapping upsert for `medusync.api.receive_mapped`.

Ordinary commerce semantics: a Medusa product becomes an Item, an order
becomes a Sales Order with its line items, an invoice becomes a Sales
Invoice, a customer becomes a Customer.

Writes go through the doctype layer rather than `db_set`, so ERPNext's own
validation runs and the connector never has to reimplement it. Everything
here runs under `frappe.flags.medusync_inbound`, so the save this makes is
recognised as an inbound write and is not echoed back to the store.
"""

import frappe
from medusync.handlers.commerce.sales_financials import apply_financials
from medusync.handlers.commerce.address_sync import sync_customer_addresses
from medusync.handlers.commerce.contact_sync import sync_customer_contact

_INSERT_DEFAULTS = {
	"Item": {"item_group": "Products", "stock_uom": "Nos", "is_stock_item": 1},
	"Customer": {
		"customer_type": "Individual",
		"customer_group": "Individual",
		"territory": "India",
	},
}
_SALES_DOCS = ("Sales Order", "Sales Invoice")


def _cust_result(doctype, doc, addresses, phone, status):
	# Customer branch also syncs Address docs + a Contact for the phone (a flat
	# mapping cannot create the linked Address/Contact doctypes, and mobile_no is
	# read-only); non-Customer doctypes get the plain result.
	r = {"doctype": doctype, "name": doc.name, "status": status}
	if doctype == "Customer":
		if addresses is not None:
			r["addresses"] = sync_customer_addresses(doc.name, addresses)
		if phone:
			r["contact"] = sync_customer_contact(doc.name, phone, first_name=doc.get("customer_name"))
	return r


def _apply_defaults(doc, doctype):
	for field, value in _INSERT_DEFAULTS.get(doctype, {}).items():
		if doc.get(field):
			continue
		meta_field = doc.meta.get_field(field)
		if meta_field and meta_field.fieldtype == "Link":
			if frappe.db.exists(meta_field.options, value):
				doc.set(field, value)
		elif meta_field:
			doc.set(field, value)


def _ensure_item_group(name):
	# Item Group is a Link AND a tree doctype: a payload item_group that
	# does not exist fails the Item save. Auto-create as a leaf under the
	# root so category values from Medusa metadata just work.
	if not name:
		return
	if frappe.db.exists("Item Group", name):
		return
	frappe.get_doc({
		"doctype": "Item Group",
		"item_group_name": name,
		"parent_item_group": "All Item Groups",
		"is_group": 0,
	}).insert(ignore_permissions=True)


def _ensure_item(item_code, item_name=None):
	"""Guarantee an Item exists so a Sales Order/Invoice line can link."""
	if not item_code:
		return None
	if not frappe.db.exists("Item", {"item_code": item_code}):
		it = frappe.new_doc("Item")
		it.item_code = item_code
		it.item_name = item_name or item_code
		it.item_group = "Products"
		it.stock_uom = "Nos"
		it.is_stock_item = 1
		it.insert(ignore_permissions=True)
	return item_code


def _set_fields(doc, payload):
	"""Write scalar fields, skipping None (a null Data field crashes
	Frappe's .strip()) and anything that isn't a real field."""
	for field, value in payload.items():
		if value is None:
			continue
		if doc.meta.get_field(field):
			doc.set(field, value)


def _upsert_sales_doc(doctype, key_field, key_value, payload, event, event_id):
	payload = dict(payload)
	payload.pop("grand_total", None)  # read-only / computed
	medusa_customer_id = payload.pop("medusa_customer_id", None)
	items = payload.pop("medusa_items", None) or []
	contact_email = payload.get("contact_email")

	customer = None
	if medusa_customer_id:
		customer = frappe.db.get_value(
			"Customer", {"medusa_customer_id": medusa_customer_id}, "name"
		)
	if not customer and contact_email:
		customer = frappe.db.get_value("Customer", {"email_id": contact_email}, "name")

	existing = (
		frappe.db.get_value(doctype, {key_field: key_value}, "name")
		if key_value not in (None, "")
		else None
	)

	if existing:
		doc = frappe.get_doc(doctype, existing)
		_set_fields(doc, payload)
		doc.save(ignore_permissions=True)
		return {"doctype": doctype, "name": doc.name, "status": "updated"}

	if not customer:
		raise Exception(
			"no matching Customer (medusa_customer_id=%s / email=%s)"
			% (medusa_customer_id, contact_email)
		)

	doc = frappe.new_doc(doctype)
	doc.customer = customer
	# Stamp the mapping key (e.g. medusa_order_id) so a retry updates this
	# doc instead of creating a duplicate.
	if key_field and key_field != "name" and key_value not in (None, "") and doc.meta.get_field(key_field):
		doc.set(key_field, key_value)
	if not doc.get("company"):
		doc.company = frappe.defaults.get_user_default("Company") or frappe.db.get_value(
			"Company", {}, "name"
		)
	if doctype == "Sales Order":
		doc.delivery_date = frappe.utils.add_days(frappe.utils.nowdate(), 7)
	_set_fields(doc, payload)
	for row in items:
		code = _ensure_item(row.get("item_code"), row.get("item_name"))
		if not code:
			continue
		doc.append(
			"items",
			{"item_code": code, "qty": row.get("qty") or 1, "rate": row.get("rate") or 0},
		)
	if not doc.get("items"):
		raise Exception("no valid line items for %s" % doctype)
	apply_financials(doc, customer, payload)
	doc.insert(ignore_permissions=True)
	return {"doctype": doctype, "name": doc.name, "status": "created"}


def upsert_via_mapping(
	doctype,
	key_field,
	key_value,
	payload,
	event,
	event_id,
	allow_create=True,
	allow_update=True,
):
	# Stop both apps' outbound hooks from echoing this inbound write.
	frappe.flags.medusync_inbound = True
	frappe.flags.in_medusa_sync = True

	is_delete = bool(event) and event.endswith(".deleted")
	addresses = payload.pop("medusa_addresses", None) if doctype == "Customer" else None
	phone = payload.pop("mobile_no", None) if doctype == "Customer" else None
	if doctype == "Item" and payload.get("item_group"):
		_ensure_item_group(str(payload.get("item_group")))

	if doctype in _SALES_DOCS and not is_delete:
		return _upsert_sales_doc(doctype, key_field, key_value, payload, event, event_id)

	existing = (
		frappe.db.get_value(doctype, {key_field: key_value}, "name")
		if key_value not in (None, "")
		else None
	)

	if is_delete:
		if not existing:
			return {"doctype": doctype, "name": None, "status": "skipped", "reason": "already absent"}
		doc = frappe.get_doc(doctype, existing)
		# Safe delete semantics: never destroy a submitted (accounting)
		# document — cancel it. Masters that carry a `disabled` flag are
		# disabled. Only trivial draft docs with no disable flag are
		# actually hard-deleted.
		if getattr(doc, "docstatus", 0) == 1:
			doc.cancel()
			return {"doctype": doctype, "name": existing, "status": "updated", "action": "cancelled"}
		if doc.meta.get_field("disabled"):
			doc.db_set("disabled", 1)
			return {"doctype": doctype, "name": existing, "status": "updated", "action": "disabled"}
		status_field = doc.meta.get_field("status")
		if status_field and "Cancelled" in (status_field.options or ""):
			doc.db_set("status", "Cancelled")
			return {"doctype": doctype, "name": existing, "status": "updated", "action": "cancelled"}
		frappe.delete_doc(doctype, existing, ignore_permissions=True)
		return {"doctype": doctype, "name": existing, "status": "updated", "action": "deleted"}

	if existing:
		if not allow_update:
			return {"doctype": doctype, "name": existing, "status": "skipped", "reason": "update not permitted"}
		doc = frappe.get_doc(doctype, existing)
		_set_fields(doc, payload)
		doc.save(ignore_permissions=True)
		return _cust_result(doctype, doc, addresses, phone, "updated")

	# Item dedupe: a stub may already exist under this item_code (created
	# as a Sales Order line) with no medusa_product_id — update it rather
	# than colliding on the primary key.
	if doctype == "Item" and payload.get("item_code") and frappe.db.exists(
		"Item", payload.get("item_code")
	):
		doc = frappe.get_doc("Item", payload.get("item_code"))
		_set_fields(doc, payload)
		doc.save(ignore_permissions=True)
		return _cust_result(doctype, doc, addresses, phone, "updated")

	if not allow_create:
		return {"doctype": doctype, "name": None, "status": "skipped", "reason": "create not permitted"}

	doc = frappe.new_doc(doctype)
	if key_field != "name" and key_value not in (None, ""):
		doc.set(key_field, key_value)
	_set_fields(doc, payload)
	_apply_defaults(doc, doctype)
	if doctype == "Customer" and not doc.get("customer_name"):
		doc.customer_name = (
			doc.get("email_id") or (str(key_value) if key_value else None) or "Medusa Customer"
		)
	doc.insert(ignore_permissions=True)
	return _cust_result(doctype, doc, addresses, phone, "created")
