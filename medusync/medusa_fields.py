# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""What the store on the other end actually has.

The mapping form could describe this side perfectly — `studio.fields_of`
reads `frappe.get_meta`, so every field of every doctype is there with its
label, its type and whether it is mandatory. The Medusa half was blank.
The only channel between the two systems was this one POSTing signed
events outward, so there was no way to ask a store what it holds, and
every Medusa path had to be typed from memory into a plain Data field.

This is that channel used the other way. Same shared secret, same HMAC,
same header — a store that is already paired needs no second credential,
and nothing answers without one.

Results are cached per site, not globally. Two Medusa stores with
different custom modules have different field lists, and a mapping pinned
to one store must not be offered the other's fields.
"""

import json

import frappe
import requests

from medusync import signing, sites

DESCRIBE_PATH = "/webhooks/erpnext-describe"

#: Long enough that opening the editor a few times costs one request,
#: short enough that a module added on the other side shows up without
#: anyone clearing a cache by hand.
CACHE_TTL_SECONDS = 15 * 60


def _describe_url(site) -> str | None:
	url = (site.get("medusa_url") or "").rstrip("/")
	return (url + DESCRIBE_PATH) if url else None


def _ask(site, body: dict) -> dict:
	"""One signed request. Returns the parsed body, or raises with a
	reason an operator can act on."""
	url = _describe_url(site)
	if not url:
		frappe.throw(frappe._("{0} has no Medusa URL.").format(site.get("site_id")))

	secret = sites.secret(site, "outbound_secret")
	if not secret:
		frappe.throw(
			frappe._(
				"{0} has no Outbound Secret, so this site cannot prove who it is. "
				"Set it on the Medusync Site, matching the store's Frappe to Medusa secret."
			).format(site.get("site_id"))
		)

	raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
	try:
		response = requests.post(
			url,
			data=raw,
			headers={
				"Content-Type": "application/json",
				signing.SIGNATURE_HEADER: signing.sign(raw, secret),
			},
			timeout=sites.timeout(site),
			verify=sites.verify_ssl(site),
		)
	except Exception as exc:
		frappe.throw(frappe._("Could not reach {0}: {1}").format(url, exc))

	if response.status_code == 401:
		frappe.throw(
			frappe._(
				"{0} rejected our signature. The Outbound Secret here and the "
				"Frappe to Medusa secret there are not the same value."
			).format(site.get("site_id"))
		)
	if not (200 <= response.status_code < 300):
		frappe.throw(
			frappe._("{0} answered {1}: {2}").format(
				url, response.status_code, (response.text or "")[:300]
			)
		)
	try:
		return response.json()
	except Exception:
		frappe.throw(frappe._("{0} did not answer with JSON.").format(url))


def _site_or_throw(site_id: str | None) -> dict:
	site = sites.get_site(site_id) if site_id else sites.default_site()
	if not site:
		frappe.throw(
			frappe._(
				"No Medusa store to ask. Add a Medusync Site, or pin this mapping to one."
			)
		)
	return site


def _cache_key(site_id: str, entity: str | None) -> str:
	return f"medusync:medusa-fields:{site_id}:{entity or '__entities__'}"


def _cached(site_id: str, entity: str | None, build):
	key = _cache_key(site_id, entity)
	hit = frappe.cache().get_value(key)
	if hit:
		return json.loads(hit)
	value = build()
	frappe.cache().set_value(key, json.dumps(value, default=str), expires_in_sec=CACHE_TTL_SECONDS)
	return value


@frappe.whitelist()
def entities(site_id: str | None = None, refresh: int | str = 0) -> dict:
	"""Which Medusa entities this store can map."""
	frappe.only_for("System Manager")
	site = _site_or_throw(site_id)
	sid = site["site_id"]
	if frappe.utils.cint(refresh):
		frappe.cache().delete_value(_cache_key(sid, None))
	body = _cached(sid, None, lambda: _ask(site, {}))
	return {"site_id": sid, "entities": body.get("entities") or []}


@frappe.whitelist()
def fields(entity: str, site_id: str | None = None, refresh: int | str = 0) -> dict:
	"""Every mappable path on one Medusa entity, with its label and type."""
	frappe.only_for("System Manager")
	if not entity:
		frappe.throw(frappe._("Pick a Medusa entity first."))
	site = _site_or_throw(site_id)
	sid = site["site_id"]
	if frappe.utils.cint(refresh):
		frappe.cache().delete_value(_cache_key(sid, entity))
	body = _cached(sid, entity, lambda: _ask(site, {"entity": entity}))
	return {
		"site_id": sid,
		"entity": body.get("entity") or entity,
		"fields": body.get("fields") or [],
		# "model" when the store derived them from its own model definition,
		# "curated" when discovery could not run there and the list may be
		# short. Worth showing rather than hiding.
		"fields_source": body.get("fields_source"),
	}


@frappe.whitelist()
def suggest(entity: str, doctype: str, site_id: str | None = None) -> dict:
	"""Suggested pairs for one (entity, doctype), from the store's matcher.

	The matching itself — the synonym groups, the composite templates, the
	confidence ladder — lives on the Medusa side and has for six phases.
	This side could not reach it, so its editor matched only names that were
	already identical, which is no help at all for `email_id` ↔ `email`.
	Asking the good matcher beats growing a worse one here.

	Not cached: it depends on the doctype's current fields, and an operator
	clicking Automap after adding a custom field should see it.
	"""
	frappe.only_for("System Manager")
	if not entity or not doctype:
		frappe.throw(frappe._("Pick both a Document Type and a Medusa entity first."))
	site = _site_or_throw(site_id)
	body = _ask(site, {"entity": entity, "doctype": doctype})
	return {
		"site_id": site["site_id"],
		"suggestions": body.get("suggestions") or [],
		"summary": body.get("suggestion_summary") or {},
	}


def clear_cache_for(site_id: str | None = None):
	"""Forget what a store told us. Called when a site's connection
	details change, since the answer belongs to a URL and a secret."""
	if site_id:
		frappe.cache().delete_keys(f"medusync:medusa-fields:{site_id}:")
	else:
		frappe.cache().delete_keys("medusync:medusa-fields:")
