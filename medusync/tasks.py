# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.utils import add_days, now_datetime

from medusync import config, envelope, outbound


def prune_logs():
	"""Delete Medusync Log rows past the configured retention.

	Sync logs contain whatever fields the mapping carries, which on a
	Customer mapping means personal data. Keeping them forever turns the
	log table into a second, unmanaged copy of it.
	"""
	try:
		days = int(config.settings().log_retention_days or 0)
	except Exception:
		return
	if days <= 0:
		return

	cutoff = add_days(now_datetime(), -days)
	frappe.db.delete("Medusync Log", {"creation": ("<", cutoff)})
	frappe.db.commit()


def retry_due(limit: int = 200):
	"""Once-a-minute sweep: re-enqueue outbound rows whose retry is due.

	A failed delivery parks its row with `next_attempt_at` (see
	`outbound._retry_or_fail`). Rows are claimed (the timestamp cleared)
	before the job is queued, so an overlapping sweep cannot pick the
	same row twice. A row that used up its attempts is `Poison` and is
	never picked up here at all.
	"""
	if not config.is_enabled():
		return
	# NB: Frappe wraps nullable columns in IFNULL(col, '') for range filters,
	# and '' sorts before every date, so a bare "<=" would also match rows
	# that never failed (next_attempt_at NULL). Require the column to be set.
	rows = frappe.get_all(
		"Medusync Log",
		filters=[
			["direction", "=", "Outbound"],
			["status", "=", "Queued"],
			["next_attempt_at", "is", "set"],
			["next_attempt_at", "<=", now_datetime()],
		],
		fields=["name", "event", "event_id", "request_body", "attempt", "site"],
		order_by="next_attempt_at asc",
		limit=limit,
	)
	for row in rows:
		frappe.db.set_value("Medusync Log", row.name, {"next_attempt_at": None}, update_modified=False)
		try:
			payload = json.loads(row.request_body or "{}")
		except Exception:
			payload = {}
		attempt = int(row.attempt or 1) + 1
		frappe.enqueue(
			"medusync.outbound.deliver",
			queue=outbound.QUEUE,
			log_name=row.name,
			event_name=row.event,
			event_id=row.event_id,
			payload=payload,
			attempt=attempt,
			site_id=row.site,
			kind=_kind_for(row.event),
			job_id=f"medusync-retry-{row.name}-{attempt}",
		)
	if rows and not frappe.flags.in_test:
		frappe.db.commit()


def _kind_for(event: str) -> str:
	"""Which envelope shape this event travels in. Mapping-configuration
	events carry the mapping; everything else carries data."""
	if (event or "").startswith("mapping."):
		return envelope.KIND_MAPPING
	return envelope.KIND_EVENT
