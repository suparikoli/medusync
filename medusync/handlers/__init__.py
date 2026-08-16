# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Registry of Medusa inbound event handlers.

Polemarch ships a pack at `medusync.handlers.polemarch` that registers
11 handlers (`ping`, `wallet.deposit.captured`, `wallet.withdrawal.posted`,
`customer.created`, `customer.updated`, `customer.synced`,
`customer.kyc.synced`, `product.created`, `product.updated`,
`order.placed`, `order.canceled`). medusync itself is site-agnostic —
the registry is the seam where site-specific behaviour plugs in.

`medusync.api.receive` and `medusync.api.receive_mapped` both call
`dispatch(event, payload, event_id=...)` after the HMAC + idempotency
check, so handlers never deal with auth, retries, or the audit log row.

Registration is idempotent — re-running `register()` during `after_install`
won't clobber an existing handler unless `replace=True`.
"""

from typing import Any, Callable

HANDLERS: dict[str, Callable] = {}


def register_handler(event: str, fn: Callable, *, replace: bool = False) -> None:
	"""Register one event handler. No-op on duplicate unless `replace=True`."""
	if not replace and event in HANDLERS:
		return
	HANDLERS[event] = fn


def unregister_handler(event: str) -> None:
	HANDLERS.pop(event, None)


def dispatch(event: str, payload: Any, *, event_id: str = "") -> dict:
	"""Call the registered handler for `event`. Returns a dict envelope.

	On no handler: `{"status": "skipped", "reason": "no_handler_for_event", "event": event}`
	On handler error: the exception propagates so `medusync.api.receive`
	can write a Failed `Medusync Log` row and return 5xx (the Medusa
	plugin's retry logic will then back off and retry).
	"""
	fn = HANDLERS.get(event)
	if fn is None:
		return {"status": "skipped", "reason": "no_handler_for_event", "event": event}
	return fn(payload, event_id=event_id)


def list_registered() -> list[str]:
	"""Sorted list of registered event names — used by `health` and tests."""
	return sorted(HANDLERS.keys())


def clear() -> None:
	"""Test-only — drop every registered handler. Not used in production."""
	HANDLERS.clear()
