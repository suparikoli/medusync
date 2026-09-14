# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Which Medusa record an ERPNext document is.

Held in Medusync Link rather than on the documents themselves. A client's
ERPNext is not ours to reshape: a row of Medusa ids on the Item form is a
schema change nobody asked for, and it follows the site into every backup,
export and upgrade long after the connector is gone.

Mappings and the shipped defaults still speak of `medusa_order_id` and its
relatives. Those names are *link keys* now, not columns: a payload value
under one is recorded here, and a read of one is answered from here. The
code that moves data does not need to know which kind of field it has.
"""

import frappe

LINK_DOCTYPE = "Medusync Link"

#: Link key -> the Medusa entity it identifies.
LINK_KEYS = {
	"medusa_customer_id": "customer",
	"medusa_product_id": "product",
	"medusa_variant_id": "variant",
	"medusa_order_id": "order",
	"medusa_address_id": "address",
	"medusa_payment_id": "payment",
	"medusa_invoice_id": "invoice",
}

#: Facts the store holds about an order, kept on its link as details.
DETAIL_KEYS = {
	"medusa_display_id": "display_id",
	"medusa_order_source": "source",
	"medusa_payment_method": "payment_method",
	"medusa_payment_reference": "payment_reference",
}

#: Which link a detail belongs to.
_DETAIL_ENTITY = "order"


def is_link_key(fieldname: str | None) -> bool:
	return bool(fieldname) and (fieldname in LINK_KEYS or fieldname in DETAIL_KEYS)


def current_site() -> str | None:
	"""The store the request being handled came from.

	Set by the inbound receiver. Outside a request — a document event, a
	backfill — the only store there is answers, and with several there is
	no honest default, so callers pass the site they mean.
	"""
	site = frappe.flags.get("medusync_site_id")
	if site:
		return site
	from medusync import sites

	default = sites.default_site()
	return default["site_id"] if default else None


def _site(site: str | None) -> str | None:
	return site or current_site()


def name_for(doctype: str, medusa_id: str | None, site: str | None = None, entity: str | None = None):
	"""The ERPNext document this store knows as `medusa_id`."""
	medusa_id = (medusa_id or "").strip() if isinstance(medusa_id, str) else medusa_id
	if not medusa_id:
		return None
	filters = {"document_type": doctype, "medusa_id": str(medusa_id)}
	site = _site(site)
	if site:
		filters["site"] = site
	if entity:
		filters["medusa_entity"] = entity
	return frappe.db.get_value(LINK_DOCTYPE, filters, "document_name")


def medusa_id_for(doctype: str, name: str | None, site: str | None = None, entity: str | None = None):
	"""What this store calls the ERPNext document `name`."""
	if not name:
		return None
	filters = {"document_type": doctype, "document_name": name}
	site = _site(site)
	if site:
		filters["site"] = site
	if entity:
		filters["medusa_entity"] = entity
	return frappe.db.get_value(LINK_DOCTYPE, filters, "medusa_id")


def details_for(doctype: str, name: str | None, site: str | None = None) -> dict:
	if not name:
		return {}
	filters = {"document_type": doctype, "document_name": name, "medusa_entity": _DETAIL_ENTITY}
	site = _site(site)
	if site:
		filters["site"] = site
	raw = frappe.db.get_value(LINK_DOCTYPE, filters, "details")
	if not raw:
		return {}
	return frappe.parse_json(raw) if isinstance(raw, str) else dict(raw)


def value_for(doc, fieldname: str, site: str | None = None):
	"""Read a field the way a mapping names it.

	A real field on the doctype wins, so a site that already carries
	`medusa_order_id` as its own column keeps working. Otherwise a link
	key is answered from the link.
	"""
	if doc.get(fieldname) not in (None, ""):
		return doc.get(fieldname)
	if fieldname in LINK_KEYS:
		return medusa_id_for(doc.doctype, doc.get("name"), site=site, entity=LINK_KEYS[fieldname])
	if fieldname in DETAIL_KEYS:
		return details_for(doc.doctype, doc.get("name"), site=site).get(DETAIL_KEYS[fieldname])
	return doc.get(fieldname)


def remember(
	doctype: str,
	name: str,
	medusa_id,
	*,
	entity: str,
	site: str | None = None,
	details: dict | None = None,
):
	"""Record that this store knows `name` as `medusa_id`. Idempotent.

	A document relinked to a different Medusa record keeps one link, not
	two: the store has changed its mind about what this is, and two
	answers to "which order is this" is worse than a stale one.
	"""
	if not name or medusa_id in (None, ""):
		return None
	site = _site(site)
	if not site:
		return None
	existing = frappe.db.get_value(
		LINK_DOCTYPE,
		{"site": site, "document_type": doctype, "document_name": name, "medusa_entity": entity},
		["name", "medusa_id", "details"],
		as_dict=True,
	)
	merged = {}
	if existing and existing.details:
		merged = frappe.parse_json(existing.details) if isinstance(existing.details, str) else dict(existing.details)
	if details:
		merged.update({k: v for k, v in details.items() if v not in (None, "")})
	if existing:
		link = frappe.get_doc(LINK_DOCTYPE, existing.name)
		link.medusa_id = str(medusa_id)
		link.details = frappe.as_json(merged) if merged else None
		if link.has_value_changed("medusa_id") or link.has_value_changed("details"):
			link.save(ignore_permissions=True)
		return link.name
	link = frappe.get_doc(
		{
			"doctype": LINK_DOCTYPE,
			"document_type": doctype,
			"document_name": name,
			"site": site,
			"medusa_entity": entity,
			"medusa_id": str(medusa_id),
			"details": frappe.as_json(merged) if merged else None,
		}
	)
	link.insert(ignore_permissions=True)
	return link.name


def forget(doctype: str, name: str, *, entity: str | None = None, site: str | None = None) -> int:
	"""Drop what the store knows about a document — it said the record is gone."""
	filters = {"document_type": doctype, "document_name": name}
	site = _site(site)
	if site:
		filters["site"] = site
	if entity:
		filters["medusa_entity"] = entity
	rows = frappe.get_all(LINK_DOCTYPE, filters=filters, pluck="name")
	for row in rows:
		frappe.delete_doc(LINK_DOCTYPE, row, ignore_permissions=True, force=True)
	return len(rows)


def take_link_keys(payload: dict, doctype: str) -> dict:
	"""Remove link keys from a payload that is about to be written to a
	document, and return them.

	A key the doctype really has stays in the payload, so it is written
	to the column as before.
	"""
	meta = frappe.get_meta(doctype)
	taken = {}
	for key in list(payload.keys()):
		if is_link_key(key) and not meta.get_field(key):
			taken[key] = payload.pop(key)
	return taken


def remember_all(doctype: str, name: str, taken: dict, site: str | None = None) -> None:
	"""Record every link key `take_link_keys` lifted off a payload."""
	if not taken:
		return
	details = {DETAIL_KEYS[k]: v for k, v in taken.items() if k in DETAIL_KEYS}
	placed_details = False
	for key, entity in LINK_KEYS.items():
		value = taken.get(key)
		if value in (None, ""):
			continue
		own = details if entity == _DETAIL_ENTITY else None
		remember(doctype, name, value, entity=entity, site=site, details=own)
		placed_details = placed_details or own is not None
	if details and not placed_details:
		# Details with no order id this time: the order is already linked.
		existing = medusa_id_for(doctype, name, site=site, entity=_DETAIL_ENTITY)
		if existing:
			remember(doctype, name, existing, entity=_DETAIL_ENTITY, site=site, details=details)


def find_by_key(doctype: str, key_field: str, key_value, site: str | None = None):
	"""The document a mapping key points at, whether the key is a column or a link."""
	if key_value in (None, ""):
		return None
	if key_field in LINK_KEYS and not frappe.get_meta(doctype).get_field(key_field):
		return name_for(doctype, key_value, site=site, entity=LINK_KEYS[key_field])
	return frappe.db.get_value(doctype, {key_field: key_value}, "name")
