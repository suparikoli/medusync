# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Shared helpers + the `ping` handler.

The ping handler returns immediately — it never touches the DB. The
helpers (`_customer_by_email`, `_compose_customer_name`,
`_populate_contact`, `_default_customer_group`, `_default_territory`,
`_upsert_mapped_customer`, `_set_doctype_fields`) are used by the
customer + order handlers.
"""

from typing import Optional

import frappe


# ── ping ─────────────────────────────────────────────────────────────


def ping(data: dict, event_id: Optional[str] = None) -> dict:
	"""Sanity check for the Medusa admin UI's 'test connection' button.

	Returns immediately with no DB read — `data` is whatever the
	sender included (often empty). `event_id` is also unused.
	"""
	return {"pong": True, "echo": data}


# ── customer helpers ─────────────────────────────────────────────────


def customer_by_email(email: str) -> Optional[str]:
	"""Find a Frappe Customer linked to this email via Contact.Dynamic Link.

	Phase 16+ model: email lives on the Contact, not the Customer. The
	Contact is linked to the Customer through `tabDynamic Link`. We
	return the Customer name or None.
	"""
	rows = frappe.db.sql(
		"""
		SELECT dl.link_name
		FROM `tabContact` c
		JOIN `tabContact Email` ce ON ce.parent = c.name
		JOIN `tabDynamic Link` dl ON dl.parent = c.name
		WHERE LOWER(ce.email_id) = %s
		  AND dl.link_doctype = 'Customer'
		ORDER BY c.creation ASC
		LIMIT 1
		""",
		(email,),
	)
	return rows[0][0] if rows else None


def compose_customer_name(data: dict) -> str:
	"""Compose the Customer's display name from Medusa first/last/email.

	Falls back to email-localpart, then to the Medusa id, then to
	"Unnamed Customer" so the Customer always has a name to save with.
	"""
	first = (data.get("first_name") or "").strip()
	last = (data.get("last_name") or "").strip()
	if first or last:
		return " ".join(p for p in (first, last) if p)
	email = data.get("email") or ""
	if "@" in email:
		return email.split("@", 1)[0]
	return data.get("id") or "Unnamed Customer"


def populate_contact(customer_name: str, data: dict):
	"""Populate the auto-created placeholder Contact (created by
	polemarch.overrides.customer.after_insert) with first/last/email/phone
	from the Medusa payload so the Customer's Phase-16 sync hook can
	back-fill `customer_name` from `full_name` correctly on next save.
	"""
	primary_contact = frappe.db.get_value(
		"Customer", customer_name, "customer_primary_contact"
	)
	if not primary_contact or not frappe.db.exists("Contact", primary_contact):
		return
	doc = frappe.get_doc("Contact", primary_contact)
	if data.get("first_name"):
		doc.first_name = data["first_name"]
	if data.get("last_name"):
		doc.last_name = data["last_name"]
	email = (data.get("email") or "").strip()
	phone = (data.get("phone") or "").strip()
	# Defensive: Contact validates email_ids and throws on a malformed
	# address. A bad value upstream should not abort the whole webhook
	# (the Customer is already committed by this point) — drop it instead.
	if email and "@" not in email:
		frappe.log_error(
			f"Dropping malformed email {email!r} for Contact {primary_contact}",
			"medusync: polemarch handler contact email",
		)
		email = ""
	if email and not any(e.email_id == email for e in (doc.email_ids or [])):
		doc.append("email_ids", {"email_id": email, "is_primary": 1})
	if phone and not any(p.phone == phone for p in (doc.phone_nos or [])):
		doc.append("phone_nos", {"phone": phone, "is_primary_mobile_no": 1})
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)


def default_customer_group() -> str:
	return (
		frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
		or "All Customer Groups"
	)


def default_territory() -> str:
	return (
		frappe.db.get_value("Territory", {"is_group": 0}, "name")
		or "All Territories"
	)


# ── generic upsert helpers (used by receive_mapped + customer) ─────


def upsert_mapped_customer(
	key_field: str,
	key_value: str,
	payload: dict,
	allow_create: bool = True,
	allow_update: bool = True,
) -> dict:
	"""Customer-specific upsert. Identity is by Contact email (Phase
	16+), so when `key_field == "email_id"` we route through
	`customer_by_email` instead of querying the Customer table
	directly. Existing customers get a column-level `set_value`; new
	customers go through the after_insert hook chain (Contact
	placeholder + customer_name back-sync) and then have their
	payload-driven Customer columns set in a second pass.
	"""
	email_lower = key_value.strip().lower()
	existing_name: Optional[str] = None
	if key_field == "email_id":
		existing_name = customer_by_email(email_lower)
	else:
		existing_name = frappe.db.get_value("Customer", {key_field: key_value}, "name")

	if existing_name:
		if not allow_update:
			return {
				"doctype": "Customer",
				"name": existing_name,
				"status": "skipped",
				"reason": "update not permitted by the sending mapping",
			}
		set_doctype_fields("Customer", existing_name, payload)
		return {
			"doctype": "Customer",
			"name": existing_name,
			"status": "updated",
		}

	if not allow_create:
		return {
			"doctype": "Customer",
			"name": None,
			"status": "skipped",
			"reason": "create not permitted by the sending mapping",
		}

	customer_name = (
		payload.get("customer_name")
		or (email_lower.split("@", 1)[0] if "@" in email_lower else email_lower)
	)
	cust_doc = frappe.get_doc({
		"doctype": "Customer",
		"customer_name": customer_name,
		"customer_type": "Individual",
		"customer_group": (
			"Polemarch"
			if frappe.db.exists("Customer Group", "Polemarch")
			else default_customer_group()
		),
		"territory": default_territory(),
	})
	cust_doc.flags.ignore_permissions = True
	cust_doc.flags.ignore_mandatory = True
	cust_doc.insert(ignore_permissions=True)

	populate_contact(
		cust_doc.name,
		{
			"first_name": customer_name.split(" ", 1)[0],
			"last_name": (
				customer_name.split(" ", 1)[1]
				if " " in customer_name
				else ""
			),
			# Only fall back to the mapping key when the mapping actually
			# keys on email. For any other key_field (customer_name, a
			# custom_* column, …) `email_lower` is just the lowercased key
			# value, and pushing it into Contact.email_ids throws
			# "<x> is not a valid Email Address" — 500ing the request AFTER
			# the Customer row was already inserted.
			"email": payload.get("email_id")
			or (email_lower if key_field == "email_id" else None),
			"phone": payload.get("mobile_no"),
		},
	)

	set_doctype_fields("Customer", cust_doc.name, payload)
	return {
		"doctype": "Customer",
		"name": cust_doc.name,
		"status": "created",
	}


def set_doctype_fields(doctype: str, name: str, payload: dict) -> None:
	"""Write the payload's scalar fields to an existing doc via
	`frappe.db.set_value`. Filters out non-existent columns so a stale
	canonical mapping (referencing a field the operator hasn't seeded
	yet) doesn't blow up the whole push — those fields simply skip.
	`customer_name` is always preserved on Customer (the field IS
	writeable, but blocking accidental overwrites of operator-edited
	legal names is more important — we only set it on first insert).

	Datetime values arrive from Medusa as ISO 8601 ("2026-05-28T07:21:
	04.555Z"); MariaDB's datetime columns want "YYYY-MM-DD HH:MM:SS"
	and reject the ISO form with error 1292. We use Frappe's standard
	`get_datetime` helper to coerce — it accepts ISO, Python datetime,
	epoch, and the MariaDB form, then returns a datetime object that
	set_value writes correctly.
	"""
	if not payload:
		return
	meta = frappe.get_meta(doctype)
	datetime_fields: set[str] = {
		f.fieldname
		for f in meta.fields
		if (f.fieldtype or "") in ("Date", "Datetime")
	}
	updates: dict = {}
	for fieldname, value in payload.items():
		if not frappe.db.has_column(doctype, fieldname):
			continue
		# Customer.customer_name is set at insert time and intentionally
		# NOT clobbered on updates — operators may have edited it to
		# match a court-corrected PAN name etc.
		if doctype == "Customer" and fieldname == "customer_name":
			continue
		# Datetime coercion — ISO 8601 strings (what Medusa emits) need
		# to become Python datetime / Frappe's expected format. Pass
		# None through untouched so `clear_value` semantics work.
		if (
			fieldname in datetime_fields
			and value
			and isinstance(value, str)
		):
			try:
				value = frappe.utils.get_datetime(value)
			except Exception:
				# If parsing fails, leave the original — Frappe will
				# surface a clear error rather than silently dropping.
				pass
		updates[fieldname] = value
	if updates:
		frappe.db.set_value(doctype, name, updates, update_modified=True)
