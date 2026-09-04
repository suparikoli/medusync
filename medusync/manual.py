# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Doing it by hand, when waiting for the next event is not an answer.

Two moments call for it on this side. A mapping was just enabled and the
store knows nothing about the two thousand records that already exist. Or
something was broken for an hour, the rows used up their attempts, and the
cause is now fixed.

Both go through the ordinary path — the same payload builder, the same
selection rules, the same log rows, the same retry schedule. A manual push
that took a shortcut would be a second implementation of the sync, and the
one people reach for when something is already wrong is the worst possible
place to keep one.

Pulling is not here, and that is not an omission. Nothing on this side
reads from Medusa: the store pushes to us and we push to it. "Pull now"
lives in the store's own admin, where the reader is.
"""

import frappe

from medusync import backfill, config

LOG_DOCTYPE = "Medusync Log"

#: Statuses worth offering to send again. `Queued` is left out on purpose:
#: it is already going to be tried, and re-queueing it would double it.
RESYNCABLE = ("Poison", "Failed")


@frappe.whitelist()
def resync_failed(site_id: str | None = None, limit: int = 500) -> dict:
	"""Put deliveries that gave up back in the queue.

	Attempts are reset to zero, not incremented. A row one attempt from
	giving up again is not what "re-sync" means to the person clicking it:
	they have fixed something and want a fair run.

	Rehearsals are never re-sent. The payload in a test row was made up,
	and sending it for real is the opposite of what a dry run is for.
	"""
	filters = [
		["direction", "=", "Outbound"],
		["status", "in", list(RESYNCABLE)],
		["is_test", "=", 0],
	]
	if site_id:
		filters.append(["site", "=", site_id])

	rows = frappe.get_all(
		LOG_DOCTYPE, filters=filters, fields=["name"], order_by="creation asc", limit=limit
	)
	requeued = []
	for row in rows:
		# Back to Queued with the clock cleared: the once-a-minute sweep
		# picks it up from here, so this shares the delivery path and the
		# backoff rather than re-implementing them.
		frappe.db.set_value(
			LOG_DOCTYPE,
			row.name,
			{"status": "Queued", "attempt": 0, "next_attempt_at": frappe.utils.now_datetime()},
			update_modified=False,
		)
		requeued.append(row.name)
	frappe.db.commit()
	return {"ok": True, "count": len(requeued), "requeued": requeued, "site": site_id}


@frappe.whitelist()
def push_all(mapping_name: str, limit: int = 0, filters=None) -> dict:
	"""Send everything this mapping covers, once.

	For the day a mapping goes live and the store has never heard of any of
	it. Refused for a mapping that is switched off: pushing two thousand
	records through a rule nobody has enabled is not something to do by
	accident.
	"""
	mapping = frappe.get_doc(config.MAPPING_DOCTYPE, mapping_name)
	if mapping.direction == "From Medusa":
		return {
			"ok": False,
			"message": "This mapping is From Medusa. Nothing leaves this site through it.",
		}
	if not mapping.enabled:
		return {
			"ok": False,
			"message": "This mapping is switched off. Rehearse and enable it first — a bulk push "
			"through a rule nobody has reviewed is exactly what the gate exists to prevent.",
		}
	if isinstance(filters, str):
		import json

		filters = json.loads(filters or "null")
	result = backfill.run(mapping=mapping_name, limit=int(limit or 0), filters=filters)
	out = {"ok": True, "mapping": mapping_name}
	out.update(result if isinstance(result, dict) else {"result": result})
	return out


@frappe.whitelist()
def waiting() -> dict:
	"""What is outstanding right now, for the top of a mapping form."""
	frappe.only_for("System Manager")
	return {
		"queued": frappe.db.count(LOG_DOCTYPE, {"status": "Queued", "is_test": 0}),
		"gave_up": frappe.db.count(LOG_DOCTYPE, {"status": ["in", list(RESYNCABLE)], "is_test": 0}),
	}
