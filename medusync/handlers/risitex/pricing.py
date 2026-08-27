# Pricing + B2B: ERPNext -> Medusa. ERPNext price always wins.
#   Item Price -> variant.price.set   (selling price list only)
#   Item       -> variant.meta.set    (MOQ = min_order_qty)
#   Customer   -> customer.group.set  (B2B customer group)
import hashlib
import json

import frappe

from medusync import config
from medusync.outbound import _create_log, deliver

DEFAULT_SELLING_PL = "Standard Selling"


def _guard():
    return not frappe.flags.get("medusync_inbound") and config.is_enabled()


def _selling_pl():
    return getattr(config.settings(), "pricing_selling_price_list", None) or DEFAULT_SELLING_PL


def _deliver(event, payload, ref, doctype, docname):
    event_id = "frappe:%s:%s" % (event, ref)
    ph = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    log = _create_log(
        direction="Outbound", status="Queued", event=event, event_id=event_id,
        document_type=doctype, document_name=docname, payload_hash=ph, request_body=payload,
    )
    deliver(log.name, event, event_id, payload, attempt=1)


def on_item_price(doc, method=None):
    try:
        if not _guard():
            return
        deleted = method == "on_trash"
        # Selling price list -> the variant base price (unchanged).
        if doc.price_list == _selling_pl():
            payload = {
                "sku": doc.item_code,
                "amount": float(doc.price_list_rate or 0),
                "currency": doc.currency,
                "valid_from": str(doc.get("valid_from") or ""),
                "valid_upto": str(doc.get("valid_upto") or ""),
                "deleted": bool(deleted),
            }
            _deliver("variant.price.set", payload, "%s-%s" % (doc.name, method), "Item Price", doc.name)
            return
        # Any OTHER price list mapped to a Medusa customer tier (via the
        #  Custom Field holding the tier code) -> a B2B
        # tier price. Price lists without the mapping are ignored, as before.
        tier_code = frappe.db.get_value("Price List", doc.price_list, "medusa_customer_tier")
        if tier_code:
            payload = {
                "sku": doc.item_code,
                "tier_code": tier_code,
                "amount": float(doc.price_list_rate or 0),
                "currency": doc.currency,
                # packing_unit (units per pack) is the volume bracket: several
                # Item Prices per (item, list) at different packing_units form a
                # quantity ladder. 0/blank -> the single (min_quantity 1) price.
                "min_quantity": int(doc.get("packing_unit") or 1),
                "deleted": bool(deleted),
            }
            _deliver("variant.tier_price.set", payload, "%s-%s" % (doc.name, method), "Item Price", doc.name)
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
