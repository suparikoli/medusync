# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""A mapping that was fine last year and names a field that is gone.

ERPNext moves. A customisation is removed, an app is uninstalled, a field
is renamed, and a mapping that has worked for a year starts referring to
something that no longer exists. Nothing fails loudly: the outbound
payload quietly stops carrying that field, or the inbound write quietly
drops it. Somebody notices in a month, from the wrong end.

So this runs after every migrate and once a day, and says so. It switches
off the mapping it found, because that one genuinely cannot do what it
claims — and only that one. It never raises: an operator whose site is
down after an upgrade because one mapping went stale does not upgrade
again.
"""

import frappe

from medusync import config
from medusync.attention import FIELD_MISSING, clear, flag, notify_attention

#: Always addressable, whatever the DocType's own field list says.
ALWAYS_VALID = frozenset({"name", "owner", "creation", "modified", "docstatus", "idx"})


def _missing_fields(doctype: str, field_map) -> list[str]:
	"""Fields this mapping names that the DocType no longer has.

	A DocType that has gone entirely counts as everything missing, which
	is both true and the right severity: the mapping cannot run at all.
	"""
	if not frappe.db.exists("DocType", doctype):
		return ["(the DocType %s does not exist on this site)" % doctype]
	valid = {df.fieldname for df in frappe.get_meta(doctype).fields} | ALWAYS_VALID
	return [row.frappe_field for row in (field_map or []) if row.frappe_field not in valid]


def check() -> dict:
	"""Look at every enabled mapping. Never raises."""
	report = {"checked": 0, "flagged": [], "cleared": [], "errors": []}

	try:
		rows = frappe.get_all(
			config.MAPPING_DOCTYPE,
			filters={"enabled": 1},
			fields=["name", "document_type", "include_all_fields", "attention"],
		)
	except Exception:
		report["errors"].append("could not list mappings")
		return report

	for row in rows:
		report["checked"] += 1
		try:
			if row.include_all_fields:
				# Nothing to drift from: the payload is whatever the
				# document has at the time.
				continue
			mapping = frappe.get_cached_doc(config.MAPPING_DOCTYPE, row.name)
			missing = _missing_fields(row.document_type, mapping.field_map)
			if not missing:
				if row.attention == FIELD_MISSING:
					clear(row.name)
					report["cleared"].append({"name": row.name})
				continue

			detail = frappe._("{0} no longer has: {1}. The mapping is switched off until it does, or until the field map stops asking for it.").format(
				row.document_type, ", ".join(missing)
			)
			is_news = flag(row.name, FIELD_MISSING, detail, disable=True)
			report["flagged"].append({"name": row.name, "missing": missing})
			if is_news:
				notify_attention(row.name, FIELD_MISSING, detail)
		except Exception as exc:
			# One unreadable mapping must not stop the sweep looking at the
			# rest, and must not stop the migrate that called it.
			report["errors"].append({"name": row.name, "error": str(exc)})

	try:
		frappe.db.commit()
	except Exception:
		pass
	return report


def run() -> dict:
	"""Scheduler entry point. Same thing, but never lets anything out."""
	try:
		return check()
	except Exception:
		frappe.log_error(title="Medusync drift check failed", message=frappe.get_traceback())
		return {"checked": 0, "flagged": [], "cleared": [], "errors": ["check raised"]}


@frappe.whitelist()
def check_now() -> dict:
	frappe.only_for("System Manager")
	return check()
