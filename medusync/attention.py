# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Telling somebody a mapping needs looking at.

Two things raise this. An upgrade that will not overwrite a mapping
somebody edited, and a mapping that names a field the DocType no longer
has. They are different problems with the same shape: one mapping, one
sentence about it, and somebody who has to decide.

The rule that matters is that it is *one* mapping. Refusing to upgrade a
site, or switching off everything because one rule went stale, is how a
warning turns into an outage — and an operator who upgrades once and finds
the site down does not upgrade again.

Notifications are deliberately dull and deliberately not repeated. This
runs daily; telling somebody the same thing every morning is how a
notification becomes a thing people filter out.
"""

import frappe

MAPPING_DOCTYPE = "Medusync Mapping"

#: What is being asked for. Kept short because it shows in a list view.
MAPPING_REQUIRED = "Mapping Required"
FIELD_MISSING = "Field Missing"


def flag(mapping_name: str, kind: str, detail: str, *, disable: bool = False) -> bool:
	"""Mark one mapping as needing attention. Returns True if this is news.

	Written with `db.set_value` rather than a save: the mapping's own
	validation would run, which on a mapping that names a field the
	DocType no longer has is exactly the thing that cannot pass.
	"""
	current = frappe.db.get_value(
		MAPPING_DOCTYPE, mapping_name, ["attention", "attention_detail"], as_dict=True
	)
	if not current:
		return False
	already = current.attention == kind and (current.attention_detail or "") == detail
	updates = {"attention": kind, "attention_detail": detail[:1000]}
	if disable:
		updates["enabled"] = 0
	frappe.db.set_value(MAPPING_DOCTYPE, mapping_name, updates, update_modified=False)
	return not already


def clear(mapping_name: str) -> None:
	frappe.db.set_value(
		MAPPING_DOCTYPE,
		mapping_name,
		{"attention": "", "attention_detail": None},
		update_modified=False,
	)


def notify_attention(mapping_name: str, kind: str, detail: str) -> None:
	"""Put it in front of the people who can act on it.

	Never raises. A notification that fails must not take the upgrade or
	the scheduled check down with it — the flag on the mapping is the
	durable record, and this is only the tap on the shoulder.
	"""
	try:
		subject = frappe._("{0}: {1}").format(kind, mapping_name)
		for user in _system_managers():
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"subject": subject,
					"email_content": detail,
					"for_user": user,
					"type": "Alert",
					"document_type": MAPPING_DOCTYPE,
					"document_name": mapping_name,
				}
			).insert(ignore_permissions=True)
	except Exception:
		try:
			frappe.log_error(
				title="Medusync could not raise a mapping notification",
				message=frappe.get_traceback(),
			)
		except Exception:
			pass


def _system_managers() -> list[str]:
	try:
		rows = frappe.get_all(
			"Has Role",
			filters={"role": "System Manager", "parenttype": "User"},
			fields=["parent"],
			limit=20,
		)
	except Exception:
		return []
	users = []
	for row in rows:
		user = row.parent
		if user in ("Administrator", "Guest") or user in users:
			continue
		if frappe.db.get_value("User", user, "enabled"):
			users.append(user)
	return users or ["Administrator"]


@frappe.whitelist()
def outstanding() -> list[dict]:
	"""Every mapping waiting on somebody. What a dashboard would show."""
	frappe.only_for("System Manager")
	return frappe.get_all(
		MAPPING_DOCTYPE,
		filters={"attention": ["is", "set"]},
		fields=["name", "document_type", "enabled", "attention", "attention_detail"],
		order_by="modified desc",
	)
