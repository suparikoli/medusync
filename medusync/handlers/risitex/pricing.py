# Pricing + B2B: ERPNext -> Medusa. ERPNext price always wins.
#   Item Price -> variant.price.set        (lists a store maps as a base price)
#   Item Price -> variant.tier_price.set   (lists a store maps as a B2B tier)
#   Item       -> variant.meta.set         (MOQ = min_order_qty)
#   Customer   -> customer.group.set       (B2B customer group)
#
# Which price list is which is per store, not per site: the same list can be
# the shelf price at one store and a wholesale tier at another, and a cost
# list can be marked Don't Sync so it never leaves. See medusync.price_lists.

import frappe

from medusync import config, price_lists
from medusync.outbound import emit


def _guard():
    return not frappe.flags.get("medusync_inbound") and config.is_enabled()


def _deliver(event, payload, ref, doctype, docname, per_site=None):
    """Log + hand off through the shared channel (queued by default, with
    the same retry/backoff as mapped events). Runs inside a doc event, so
    the outbound HTTP call must never happen inline here."""
    emit(event, payload, ref=ref, doctype=doctype, docname=docname, per_site=per_site)


def on_item_price(doc, method=None):
    """One Item Price can mean different things to different stores, so the
    rules are resolved first and the stores are grouped by what they asked
    for. A store that mapped nothing hears nothing."""
    try:
        if not _guard():
            return
        rules = price_lists.rules_for(doc.price_list)
        if not rules:
            return
        deleted = method == "on_trash"
        ref = "%s-%s" % (doc.name, method)

        base_stores = {r["site_id"] for r in rules if r["role"] == price_lists.ROLE_BASE}
        if base_stores:
            payload = {
                "sku": doc.item_code,
                "price_list": doc.price_list,
                "amount": float(doc.price_list_rate or 0),
                "currency": doc.currency,
                "valid_from": str(doc.get("valid_from") or ""),
                "valid_upto": str(doc.get("valid_upto") or ""),
                "deleted": bool(deleted),
            }
            _deliver(
                "variant.price.set",
                payload,
                ref,
                "Item Price",
                doc.name,
                per_site=lambda site_id, body: body if site_id in base_stores else None,
            )

        tier_codes = {
            r["site_id"]: r["tier_code"]
            for r in rules
            if r["role"] == price_lists.ROLE_TIER and r["tier_code"]
        }
        if tier_codes:
            payload = {
                "sku": doc.item_code,
                "price_list": doc.price_list,
                "amount": float(doc.price_list_rate or 0),
                "currency": doc.currency,
                # packing_unit (units per pack) is the volume bracket: several
                # Item Prices per (item, list) at different packing_units form a
                # quantity ladder. 0/blank -> the single (min_quantity 1) price.
                "min_quantity": int(doc.get("packing_unit") or 1),
                "deleted": bool(deleted),
            }
            _deliver(
                "variant.tier_price.set",
                payload,
                ref,
                "Item Price",
                doc.name,
                per_site=lambda site_id, body: (
                    {**body, "tier_code": tier_codes[site_id]} if site_id in tier_codes else None
                ),
            )
    except Exception:
        frappe.log_error(title="medusync pricing on_item_price failed", message=frappe.get_traceback())


def on_item(doc, method=None):
    try:
        if not _guard():
            return
        payload = {"sku": doc.item_code, "moq": float(doc.get("min_order_qty") or 0)}
        _deliver("variant.meta.set", payload, "%s-%s" % (doc.name, method or "u"), "Item", doc.name)
    except Exception:
        frappe.log_error(title="medusync pricing on_item failed", message=frappe.get_traceback())


def on_customer_group_link(doc, method=None):
    try:
        if not _guard():
            return
        grp = doc.get("customer_group")
        if not grp:
            return
        payload = {
            "medusa_customer_id": doc.get("medusa_customer_id"),
            "email": doc.get("email_id"),
            "group": grp,
        }
        _deliver("customer.group.set", payload, "%s-%s" % (doc.name, method or "u"), "Customer", doc.name)
    except Exception:
        frappe.log_error(title="medusync pricing on_customer_group failed", message=frappe.get_traceback())
