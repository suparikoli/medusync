# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Which price list travels where.

Prices sync in both directions by default, but the direction is settable
independently for each Price List: a retail list can flow ERPNext to
Medusa while a cost list never leaves the building. A single global
"selling price list" setting could express neither.

The map is per store, because two stores can sell from different lists.
Customer-specific prices are not expressed here: they belong to Medusa
customer groups and price lists.

A store with no rows falls back to the Settings selling list, the
arrangement every existing installation is running today.
"""

import frappe

from medusync import config, sites

CHILD_DOCTYPE = "Medusync Price List Map"
PARENTFIELD = "price_lists"

_CACHE_KEY = "medusync_price_list_map"

#: Legacy default, kept so a site that never configured anything behaves
#: exactly as it did before the map existed.
DEFAULT_SELLING_PRICE_LIST = "Standard Selling"

ROLE_BASE = "Base Price"

#: Directions whose data may leave this site. "From Medusa" and
#: "Don't Sync" both mean nothing goes out; they differ only in whether
#: anything is allowed to come back the other way.
OUTBOUND_DIRECTIONS = ("To Medusa", "Two-way")
INBOUND_DIRECTIONS = ("From Medusa", "Two-way")


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
				fields=["parent", "price_list", "direction", "enabled"],
			)
		]
	except Exception:
		# Table missing (pre-migrate) — behave as "nothing configured".
		return []


def legacy_selling_price_list() -> str:
	try:
		configured = (config.settings().get("pricing_selling_price_list") or "").strip()
	except Exception:
		configured = ""
	return configured or DEFAULT_SELLING_PRICE_LIST


def _table() -> dict:
	"""price list -> [rule, ...], cached per request."""
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
		# Rows at all — enabled or not — mean this store has a map, and a
		# store that switched its only rule off meant to send nothing.
		configured.add(site_id)
		if not row.get("enabled") or not row.get("price_list"):
			continue
		table.setdefault(row["price_list"], []).append(
			{
				"site_id": site_id,
				"price_list": row["price_list"],
				"direction": row.get("direction") or "To Medusa",
				"role": ROLE_BASE,
			}
		)

	unmapped = sorted(set(stores.values()) - configured)
	if unmapped:
		base = legacy_selling_price_list()
		for site_id in unmapped:
			if base:
				table.setdefault(base, []).append(
					{
						"site_id": site_id,
						"price_list": base,
						"direction": "To Medusa",
						"role": ROLE_BASE,
					}
				)

	for rules in table.values():
		rules.sort(key=lambda r: (r["site_id"], r["role"]))
	frappe.local.cache[_CACHE_KEY] = table
	return table


def all_rules_for(price_list: str) -> list[dict]:
	"""Every rule touching this list, whichever way it points."""
	if not price_list:
		return []
	return [dict(rule) for rule in _table().get(price_list, [])]


def rules_for(price_list: str) -> list[dict]:
	"""Rules that let this list leave ERPNext.

	This is what the outbound handler asks. A list nobody mapped, one
	marked Don't Sync, and one Medusa owns all answer the same way here:
	an empty list, so nothing is sent.
	"""
	return [rule for rule in all_rules_for(price_list) if rule["direction"] in OUTBOUND_DIRECTIONS]


def accepts_inbound(price_list: str, site_id: str) -> bool:
	"""May this store write this list's prices back into ERPNext?

	Nothing calls this yet — the inbound price path is not built — but the
	direction it answers is already stored, and answering it in one place
	keeps the two sides of "Two-way" from drifting apart.
	"""
	for rule in all_rules_for(price_list):
		if rule["site_id"] == site_id:
			return rule["direction"] in INBOUND_DIRECTIONS
	return False


def watched() -> set:
	"""Price lists any store cares about, in either direction."""
	return set(_table().keys())
