# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Guards for running domain hooks from the wildcard document event.

Two hazards live here, and both are re-entrancy.

The first is Frappe's own bookkeeping. `frappe.log_error` writes an Error
Log document, which fires the same wildcard hook that is currently
handling a failure — so a hook that raises would log, insert, re-enter,
raise, log, and never return. The hooks skip Frappe's internal log
doctypes for that reason, and a flag stops the general case even when the
re-entry arrives through a doctype nobody thought of.

The second is depth. A hook that saves a document legitimately re-enters
the wildcard hook for that document; that is normal and must keep working.
Only re-entry *while this dispatcher is already running* is refused.
"""

import frappe

#: Frappe's own bookkeeping. Writing one of these is a side effect of the
#: platform doing its job, never a business change worth syncing, and
#: Error Log in particular is written BY our own failure handling.
INTERNAL_DOCTYPES = frozenset(
	{
		"Error Log",
		"Error Snapshot",
		"Scheduled Job Log",
		"Activity Log",
		"Access Log",
		"Route History",
		"Version",
		"Notification Log",
		"Email Queue",
		"Email Queue Recipient",
		"Prepared Report",
		"Document Follow",
		"View Log",
	}
)

_RUNNING = "medusync_outbound_hooks_running"


def is_internal(doctype: str | None) -> bool:
	return bool(doctype) and doctype in INTERNAL_DOCTYPES


def already_running() -> bool:
	return bool(frappe.flags.get(_RUNNING))


class running:
	"""Mark the dispatcher as active for the duration of the hooks."""

	def __enter__(self):
		self._prev = frappe.flags.get(_RUNNING)
		frappe.flags[_RUNNING] = True
		return self

	def __exit__(self, *exc):
		frappe.flags[_RUNNING] = self._prev
		return False
