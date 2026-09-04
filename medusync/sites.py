# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""One Medusync Site per connected Medusa backend.

Connection details and the two shared secrets used to live on the Single,
which allowed exactly one Medusa store. They live on a per-site record
now; the Single keeps the settings that are genuinely global (retention,
queueing, timeouts). A failure or an outage on one site must not affect
another, so every lookup here is per-site and nothing is shared but the
document being synced.

The lookup runs on the outbound hot path, so the list is cached per
request and invalidated whenever a Medusync Site is saved.
"""

import frappe

SITE_DOCTYPE = "Medusync Site"
_CACHE_KEY = "medusync_sites"

#: Fields read on the hot path. Secrets are NOT here — they are Password
#: fields and must be read through get_password(), one document at a time.
_FIELDS = (
	"name",
	"site_id",
	"title",
	"enabled",
	"medusa_url",
	"inbound_path",
	"request_timeout",
	"verify_ssl",
	"handler_pack",
	"products_doctype",
)


def clear_cache(*args, **kwargs):
	"""Bound to Medusync Site on_update/on_trash in hooks.py."""
	frappe.local.cache.pop(_CACHE_KEY, None)


def _load() -> list[dict]:
	cached = frappe.local.cache.get(_CACHE_KEY)
	if cached is None:
		try:
			cached = [
				dict(row)
				for row in frappe.get_all(SITE_DOCTYPE, fields=list(_FIELDS), order_by="site_id asc")
			]
		except Exception:
			# Table missing (pre-migrate) — behave as "nothing configured".
			cached = []
		frappe.local.cache[_CACHE_KEY] = cached
	return cached


def all_sites(enabled_only: bool = True) -> list[dict]:
	rows = _load()
	if enabled_only:
		return [r for r in rows if r.get("enabled")]
	return list(rows)


def get_site(site_id: str, enabled_only: bool = False) -> dict | None:
	for row in _load():
		if row.get("site_id") == site_id:
			if enabled_only and not row.get("enabled"):
				return None
			return row
	return None


def our_site_ids() -> set:
	"""Every site id this instance owns, enabled or not — a disabled site's
	echo still has to be recognised as ours."""
	return {r.get("site_id") for r in _load() if r.get("site_id")}


def default_site() -> dict | None:
	"""The single site, when there is exactly one enabled. Used by callers
	that predate multi-site (the connection test, legacy delivery)."""
	enabled = all_sites()
	if len(enabled) == 1:
		return enabled[0]
	return enabled[0] if enabled else None


def sites_for_mapping(mapping) -> list[dict]:
	"""Which sites a mapping applies to. A mapping with no site applies to
	every enabled site; one pinned to a site applies only there, and to
	nothing at all when that site is disabled."""
	pinned = None
	if mapping is not None:
		pinned = mapping.get("site") if hasattr(mapping, "get") else getattr(mapping, "site", None)
	if not pinned:
		return all_sites()
	site = get_site(pinned, enabled_only=True)
	return [site] if site else []


def endpoint(site) -> str | None:
	"""Full URL this site's inbound webhook lives on."""
	if not site:
		return None
	url = (site.get("medusa_url") or "").rstrip("/")
	if not url:
		return None
	return url + (site.get("inbound_path") or "/webhooks/erpnext-inbound")


def secret(site, fieldname: str) -> str | None:
	"""Read one of a site's shared secrets.

	They are Password fields, so they are encrypted at rest and must be
	read through get_password(); the raw column holds ciphertext, which
	would silently produce a signature nobody can verify.
	"""
	site_id = site if isinstance(site, str) else (site or {}).get("site_id")
	if not site_id:
		return None
	try:
		doc = frappe.get_cached_doc(SITE_DOCTYPE, site_id)
		return doc.get_password(fieldname, raise_exception=False)
	except Exception:
		return None


def site_for_signature(raw: bytes, provided: str | None) -> dict | None:
	"""Find the site whose inbound secret signs this body.

	Multi-site means "which site is this?" is answered by the signature
	itself rather than by anything the caller claims, so a site cannot
	impersonate another by setting a header.
	"""
	if not provided:
		return None
	from medusync.signing import verify

	for row in all_sites(enabled_only=False):
		site_secret = secret(row, "inbound_secret")
		if site_secret and verify(raw, site_secret, provided):
			return row
	return None


def timeout(site) -> int:
	value = (site or {}).get("request_timeout")
	if not value:
		from medusync import config

		value = config.settings().request_timeout or 15
	return int(value)


def verify_ssl(site) -> bool:
	value = (site or {}).get("verify_ssl")
	if value is None:
		from medusync import config

		value = config.settings().verify_ssl
	return bool(value)
