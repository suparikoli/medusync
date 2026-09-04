# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""When a store is down, stop knocking.

A store that has been unreachable for ten deliveries will be unreachable
for the eleventh, and every attempt costs a worker for the length of the
timeout. With several stores connected, one that is down can starve the
queue for the ones that are up — which is exactly the failure multi-site
was built to prevent, arriving through a different door.

So: count consecutive failures per store, stop trying past a threshold,
and let one delivery through per retry sweep to find out whether the store
has come back. One success closes it. The counter is on the store record
rather than in a cache, because an operator looking at why a store is
quiet should be able to see the answer on the store.

Two things never touch it. A rehearsal that failed is a rehearsal, not a
delivery, and letting one trip the breaker would take real traffic down
over a test — nor can a rehearsal close it, for the same reason. And the
reset handshake goes out on its own path, so it reaches a store the
breaker has given up on, which is precisely when somebody is most likely
to be resetting.
"""

import frappe
from frappe.utils import now_datetime

SITE_DOCTYPE = "Medusync Site"

#: Consecutive failures before a store is left alone. Ten is roughly a
#: deploy's worth of downtime at the retry backoff, so a rolling restart
#: does not trip it and a real outage does.
DEFAULT_TRIP_AFTER = 10


def threshold(site_id: str) -> int:
	try:
		value = int(frappe.db.get_value(SITE_DOCTYPE, site_id, "trip_after") or 0)
	except Exception:
		value = 0
	return value if value > 0 else DEFAULT_TRIP_AFTER


def state(site_id: str) -> dict:
	try:
		row = frappe.db.get_value(
			SITE_DOCTYPE,
			site_id,
			["consecutive_failures", "tripped_at", "trip_after", "last_error"],
			as_dict=True,
		)
	except Exception:
		row = None
	if not row:
		return {"known": False, "consecutive_failures": 0, "tripped": False}
	return {
		"known": True,
		"consecutive_failures": int(row.consecutive_failures or 0),
		"tripped_at": row.tripped_at,
		"tripped": bool(row.tripped_at),
		"trip_after": threshold(site_id),
		"last_error": row.last_error,
	}


def is_tripped(site_id: str) -> bool:
	return bool(state(site_id).get("tripped"))


def allows(site_id: str, *, is_test: bool = False, probe: bool = False) -> bool:
	"""May this delivery go out?

	A rehearsal always may: the operator is deliberately testing this
	store, and answering "the breaker is open" while they are trying to
	find out why is the wrong answer at the wrong moment. A probe always
	may, because somebody has to knock.
	"""
	if is_test or probe or not site_id:
		return True
	return not is_tripped(site_id)


def record_failure(site_id: str, is_test: bool = False) -> dict:
	"""One more failure in a row. Trips at the threshold."""
	if not site_id or is_test:
		return state(site_id)
	try:
		count = int(frappe.db.get_value(SITE_DOCTYPE, site_id, "consecutive_failures") or 0) + 1
		updates = {"consecutive_failures": count}
		if count >= threshold(site_id) and not is_tripped(site_id):
			updates["tripped_at"] = now_datetime()
		frappe.db.set_value(SITE_DOCTYPE, site_id, updates, update_modified=False)
	except Exception:
		# The breaker is an optimisation. Failing to record a failure must
		# never turn one failed delivery into two.
		pass
	return state(site_id)


def record_success(site_id: str, is_test: bool = False) -> None:
	"""The store answered. Start counting again from nothing."""
	if not site_id or is_test:
		return
	try:
		frappe.db.set_value(
			SITE_DOCTYPE,
			site_id,
			{"consecutive_failures": 0, "tripped_at": None},
			update_modified=False,
		)
	except Exception:
		pass


def close(site_id: str) -> dict:
	"""Forget the failures and start trying again. What the button does."""
	record_success(site_id)
	return state(site_id)


@frappe.whitelist()
def close_now(site_id: str) -> dict:
	frappe.only_for("System Manager")
	return close(site_id)


@frappe.whitelist()
def tripped_sites() -> list[dict]:
	"""Every store currently being left alone. What a dashboard shows."""
	frappe.only_for("System Manager")
	rows = frappe.get_all(
		SITE_DOCTYPE,
		filters={"tripped_at": ["is", "set"]},
		fields=["name", "site_id", "consecutive_failures", "tripped_at", "last_error"],
	)
	return [dict(row) for row in rows]
