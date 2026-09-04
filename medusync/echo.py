# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Loop prevention that outlives the request.

`frappe.flags.medusync_inbound` stops the echo while the inbound write is
still in flight: the outbound hook sees the flag and returns. It cannot
help once the echo leaves the request — an inbound write that lands a
Stock Ledger Entry, say, is re-pushed by a background job seconds later,
by which time the flag is long gone.

So an inbound write also leaves a breadcrumb on each document it touched:
"this was last changed by correlation C, which came from medusa:site-a".
Anything the outbound side sends about that document within the window is
stamped `echo_of`, and the far side drops what it recognises as its own.
The breadcrumb expires, so a genuine later edit by a person is never
mistaken for an echo.
"""

from contextlib import contextmanager

import frappe

#: How long a document stays attributable to the inbound write that
#: touched it. Long enough for an after-commit worker to run, short enough
#: that a human editing the same document a minute later is not silenced.
TTL_SECONDS = 180

_FLAG = "medusync_inbound"
_CTX_FLAG = "medusync_inbound_ctx"


def _key(doctype: str, name: str) -> str:
	return f"medusync:echo:{doctype}:{name}"


def remember(doctype: str, name: str, *, correlation_id: str, origin: str) -> None:
	"""Record that an inbound write from `origin` touched this document."""
	if not doctype or not name:
		return
	try:
		frappe.cache().set_value(
			_key(doctype, name),
			{"correlation_id": correlation_id, "origin": origin},
			expires_in_sec=TTL_SECONDS,
		)
	except Exception:
		# A cache outage must never break the write it is annotating; the
		# worst case is one redundant round trip, which the far side dedupes
		# on event_id anyway.
		pass


def origin_of(doctype: str, name: str) -> dict | None:
	"""The breadcrumb for a document, or None when the change is local."""
	if not doctype or not name:
		return None
	try:
		value = frappe.cache().get_value(_key(doctype, name))
	except Exception:
		return None
	return value or None


def forget_all() -> None:
	"""Drop every breadcrumb on this site.

	Only a reset needs this. They expire on their own in three minutes, so
	the point is not reclaiming space: it is that a reset should not leave
	the next few pushes suppressed as echoes of a configuration that no
	longer exists.
	"""
	try:
		frappe.cache().delete_keys("medusync:echo:")
	except Exception:
		# Same reasoning as remember(): a cache outage must never turn a
		# reset into a failure. The breadcrumbs expire regardless.
		pass


def forget(doctype: str, name: str) -> None:
	try:
		frappe.cache().delete_value(_key(doctype, name))
	except Exception:
		pass


def current() -> dict | None:
	"""The inbound context this request is running inside, if any."""
	return frappe.flags.get(_CTX_FLAG)


def mark_touched(doctype: str, name: str) -> None:
	"""Attribute a document to the inbound context currently running.

	Called by the receiver after a successful write, once it knows which
	document the write produced.
	"""
	ctx = current()
	if not ctx:
		return
	remember(
		doctype,
		name,
		correlation_id=ctx.get("correlation_id"),
		origin=ctx.get("origin"),
	)


@contextmanager
def inbound_context(correlation_id: str, origin: str):
	"""Run an inbound write with the loop guard raised.

	Sets both the modern flag and the legacy `in_medusa_sync` one, because
	a site may still have another app whose outbound hooks check the old
	name. Restores whatever was there before, even when the write raises.
	"""
	prev_flag = frappe.flags.get(_FLAG)
	prev_legacy = frappe.flags.get("in_medusa_sync")
	prev_ctx = frappe.flags.get(_CTX_FLAG)
	frappe.flags[_FLAG] = True
	frappe.flags["in_medusa_sync"] = True
	frappe.flags[_CTX_FLAG] = {"correlation_id": correlation_id, "origin": origin}
	try:
		yield
	finally:
		frappe.flags[_FLAG] = prev_flag
		frappe.flags["in_medusa_sync"] = prev_legacy
		frappe.flags[_CTX_FLAG] = prev_ctx
