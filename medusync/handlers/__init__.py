# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Registry of Medusa inbound event handlers, loaded from opt-in packs.

medusync's core is site-agnostic. Domain behaviour lives in *handler
packs* (`medusync.handlers.<pack>`), each exposing:

    register()      -> calls `register_handler(event, fn)` for its events
    MAPPED_UPSERT   -> dotted path of the doctype-aware upsert that
                       `medusync.api.receive_mapped` should use (optional)

Which packs a site loads is that site's decision, in `site_config.json`:

    "medusync_handler_packs": ["risitex"]

Nothing is registered at import time — a bench CLI process has no site
context yet — so the registry is (re)built lazily on first use, per site.
When the key is absent the Polemarch pack loads, which keeps installations
that predate this setting behaving exactly as before.

`medusync.api.receive` and `medusync.api.receive_mapped` call
`dispatch(event, payload, event_id=...)` after the HMAC + idempotency
check, so handlers never deal with auth, retries, or the audit log row.
"""

import importlib
import os
from typing import Any, Callable

import frappe

HANDLERS: dict[str, Callable] = {}

CONF_KEY = "medusync_handler_packs"
DEFAULT_PACKS = ("polemarch",)

# site -> the packs tuple the registry currently reflects. One process may
# serve several sites; a different pack list rebuilds the registry rather
# than merging into it.
_loaded_for: dict[str, tuple] = {}


def configured_packs() -> list[str]:
	"""Pack names for the current site, in load order."""
	try:
		raw = frappe.conf.get(CONF_KEY)
	except Exception:
		raw = None
	if raw is None:
		packs = list(DEFAULT_PACKS)
	elif isinstance(raw, str):
		packs = [p.strip() for p in raw.split(",") if p.strip()]
	else:
		packs = [str(p).strip() for p in raw if str(p).strip()]
	# Operator/test escape hatch that predates the site_config key.
	if os.environ.get("MEDUSYNC_SKIP_POLEMARCH"):
		packs = [p for p in packs if p != "polemarch"]
	return packs


def _pack_module(name: str):
	return importlib.import_module(f"medusync.handlers.{name}")


def _log_pack_failure(name: str) -> None:
	try:
		frappe.log_error(
			title=f"medusync: failed to load handler pack '{name}'",
			message=frappe.get_traceback(),
		)
	except Exception:
		pass


def ensure_packs_loaded(force: bool = False) -> list[str]:
	"""Make HANDLERS reflect the current site's configured packs."""
	site = getattr(frappe.local, "site", None) or ""
	packs = tuple(configured_packs())
	if not force and _loaded_for.get(site) == packs:
		return list(packs)
	HANDLERS.clear()
	for name in packs:
		try:
			_pack_module(name).register()
		except Exception:
			# A broken or missing pack must not take the whole registry
			# down; the other packs still load and the failure is logged.
			_log_pack_failure(name)
	_loaded_for[site] = packs
	return list(packs)


def get_mapped_upsert() -> Callable | None:
	"""The doctype-aware upsert `receive_mapped` should use: the first
	configured pack that declares `MAPPED_UPSERT`, else None."""
	for name in configured_packs():
		try:
			mod = _pack_module(name)
		except Exception:
			_log_pack_failure(name)
			continue
		path = getattr(mod, "MAPPED_UPSERT", None)
		if not path:
			continue
		module_path, _, fn_name = path.rpartition(".")
		return getattr(importlib.import_module(module_path), fn_name)
	return None


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
	ensure_packs_loaded()
	fn = HANDLERS.get(event)
	if fn is None:
		return {"status": "skipped", "reason": "no_handler_for_event", "event": event}
	return fn(payload, event_id=event_id)


def list_registered() -> list[str]:
	"""Sorted list of registered event names — used by `health` and tests."""
	ensure_packs_loaded()
	return sorted(HANDLERS.keys())


def clear() -> None:
	"""Test-only — drop every registered handler and forget what was loaded."""
	HANDLERS.clear()
	_loaded_for.clear()
