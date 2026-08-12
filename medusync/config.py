# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Settings access + the mapping lookup the hot path depends on.

The outbound hook runs on EVERY document save on the site, so "is there
a mapping for this doctype?" has to be cheap. It is answered from
frappe's request cache, refreshed whenever a Medusync Mapping is saved.
"""

import frappe

SETTINGS_DOCTYPE = "Medusync Settings"
MAPPING_DOCTYPE = "Medusync Mapping"

_MAPPING_CACHE_KEY = "medusync_mappings_by_doctype"


def settings():
	"""The Single, cached per request."""
	return frappe.get_cached_doc(SETTINGS_DOCTYPE)


def is_enabled() -> bool:
	try:
		return bool(settings().enabled)
	except Exception:
		# During `bench install-app` the Single may not exist yet.
		return False


def get_secret(fieldname: str) -> str | None:
	"""Read one of the shared secrets.

	These are Password fields, so they are encrypted at rest and must be
	read through get_password() — reading the raw column returns
	ciphertext, which silently produces a signature nobody can verify.
	"""
	try:
		return settings().get_password(fieldname, raise_exception=False)
	except Exception:
		return None


def medusa_endpoint() -> str | None:
	cfg = settings()
	if not cfg.medusa_url:
		return None
	return cfg.medusa_url.rstrip("/") + (cfg.inbound_path or "/webhooks/erpnext-inbound")


def mappings_for(doctype: str) -> list[dict]:
	"""Enabled mappings targeting `doctype`, newest config first.

	Returns plain dicts of the parent row; callers that need the field
	map load the full doc. Keeping this projection small matters — it is
	consulted on every save of every doctype on the site.
	"""
	cache = frappe.local.cache.setdefault(_MAPPING_CACHE_KEY, None)
	if cache is None:
		cache = {}
		try:
			rows = frappe.get_all(
				MAPPING_DOCTYPE,
				filters={"enabled": 1},
				fields=["name", "document_type", "direction"],
			)
		except Exception:
			# Table missing (pre-migrate) — behave as "nothing configured".
			rows = []
		for row in rows:
			cache.setdefault(row.document_type, []).append(dict(row))
		frappe.local.cache[_MAPPING_CACHE_KEY] = cache
	return cache.get(doctype, [])


def clear_mapping_cache(*args, **kwargs):
	"""Bound to Medusync Mapping's on_update/on_trash in hooks.py."""
	frappe.local.cache.pop(_MAPPING_CACHE_KEY, None)
	frappe.cache().delete_value(_MAPPING_CACHE_KEY)
