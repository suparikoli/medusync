# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Which ERPNext warehouse feeds which Medusa stock location.

An ERPNext site holds stock in several warehouses; a Medusa store keeps
its own stock locations. The pairing belongs to the store, not to the
site as a whole, because two stores can draw on the same warehouse under
different location ids, or on entirely different warehouses.

A single warehouse used to be named in Settings, which made a second
warehouse invisible and gave a second store the first store's numbers.
That setting is still honoured for any store that has not filled the map
in, so turning multi-warehouse on changes nothing until somebody
configures it.

Rows that exist but are switched off are still a map. A store that
disabled its only warehouse row is saying "send me nothing", not "fall
back to whatever the global default is" — the two are easy to confuse
and only one of them is what an operator meant when they unticked a box.
"""

import frappe

from medusync import config, sites

CHILD_DOCTYPE = "Medusync Warehouse Map"
PARENTFIELD = "warehouses"

_CACHE_KEY = "medusync_warehouse_map"


def clear_cache(*args, **kwargs):
	"""Bound to Medusync Site on_update/on_trash alongside sites.clear_cache."""
	frappe.local.cache.pop(_CACHE_KEY, None)


def _rows() -> list[dict]:
	try:
		return [
			dict(row)
			for row in frappe.get_all(
				CHILD_DOCTYPE,
				filters={"parenttype": sites.SITE_DOCTYPE, "parentfield": PARENTFIELD},
				fields=["parent", "warehouse", "location_id", "enabled"],
			)
		]
	except Exception:
		# Table missing (pre-migrate) — behave as "nothing configured".
		return []


def legacy_warehouse() -> str | None:
	"""The single warehouse named in Settings, from before the map."""
	try:
		return (config.settings().get("inventory_source_warehouse") or "").strip() or None
	except Exception:
		return None


def _table() -> dict:
	"""warehouse -> [(site_id, location_id), ...], cached per request."""
	cached = frappe.local.cache.get(_CACHE_KEY)
	if cached is not None:
		return cached

	stores = {row["name"]: row["site_id"] for row in sites.all_sites() if row.get("site_id")}
	table: dict[str, list] = {}
	configured: set[str] = set()

	for row in _rows():
		site_id = stores.get(row.get("parent"))
		if not site_id:
			continue
		# Having rows at all is what counts as "this store has a map".
		configured.add(site_id)
		if not row.get("enabled") or not row.get("warehouse"):
			continue
		table.setdefault(row["warehouse"], []).append((site_id, row.get("location_id") or None))

	legacy = legacy_warehouse()
	if legacy:
		for site_id in sorted(set(stores.values()) - configured):
			table.setdefault(legacy, []).append((site_id, None))

	for pairs in table.values():
		pairs.sort()
	frappe.local.cache[_CACHE_KEY] = table
	return table


def targets_for(warehouse: str) -> list[tuple]:
	"""Every (site_id, location_id) this warehouse feeds.

	`location_id` is None for a store on the legacy setting — it never
	named a location, so the receiving plugin picks its own as it always
	did.
	"""
	if not warehouse:
		return []
	return list(_table().get(warehouse, []))


def location_for(warehouse: str, site_id: str) -> str | None:
	for candidate, location in targets_for(warehouse):
		if candidate == site_id:
			return location
	return None


def watched() -> set:
	"""Warehouses any store cares about.

	The stock hook runs on every Stock Ledger Entry on the site, so it
	asks this first and returns immediately for a warehouse nobody has
	mapped.
	"""
	return set(_table().keys())


def is_watched(warehouse: str) -> bool:
	return bool(warehouse) and warehouse in _table()
