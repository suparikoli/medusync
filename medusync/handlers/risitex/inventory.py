# ERPNext -> Medusa stock-level sync. One-way: ERPNext owns stock and is the
# SINGLE reservation authority. We push SELLABLE = actual - reserved - safety
# (not raw actual_qty), so Medusa never oversells.
#
# Two triggers:
#   - Stock Ledger Entry.after_insert  -> actual_qty changed (receipts/issues)
#   - Sales Order submit/cancel/update -> reserved_qty changed (no SLE fires)
# Both defer to an after-commit job `push_level` that reads the settled Bin
# (at after_insert neither Bin nor qty_after_transaction is final).
import hashlib
import json

import frappe

from medusync import config
from medusync.outbound import _create_log, deliver

DEFAULT_SOURCE_WAREHOUSE = "Finished Goods - R"


def _source_warehouse():
    cfg = config.settings()
    return getattr(cfg, "inventory_source_warehouse", None) or DEFAULT_SOURCE_WAREHOUSE


def on_sle(doc, method=None):
    """Stock movement -> re-push sellable. Cheap, never raises."""
    try:
        if frappe.flags.get("medusync_inbound"):
            return
        if not config.is_enabled():
            return
        source = _source_warehouse()
        if doc.warehouse != source:
            return
        frappe.enqueue(
            "medusync.handlers.risitex.inventory.push_level",
            queue="short",
            item_code=doc.item_code,
            warehouse=source,
            ref="sle-%s" % doc.name,
            enqueue_after_commit=True,
        )
    except Exception:
        frappe.log_error(title="medusync inventory SLE hook failed", message=frappe.get_traceback())


def on_sales_order(doc, method=None):
    """SO submit/cancel/amend changes reserved_qty with NO SLE -> re-push each
    line item's sellable at the source warehouse."""
    try:
        if frappe.flags.get("medusync_inbound"):
            return
        if not config.is_enabled():
            return
        source = _source_warehouse()
        seen = set()
        for it in (doc.items or []):
            if not it.item_code or it.item_code in seen:
                continue
            seen.add(it.item_code)
            frappe.enqueue(
                "medusync.handlers.risitex.inventory.push_level",
                queue="short",
                item_code=it.item_code,
                warehouse=source,
                ref="so-%s-%s" % (doc.name, method or "update"),
                enqueue_after_commit=True,
            )
    except Exception:
        frappe.log_error(title="medusync inventory SO hook failed", message=frappe.get_traceback())


def push_level(item_code, warehouse, ref):
    """After-commit: sellable = max(0, actual - reserved - safety), then deliver."""
    b = frappe.db.get_value(
        "Bin", {"item_code": item_code, "warehouse": warehouse},
        ["actual_qty", "reserved_qty"], as_dict=True,
    ) or {}
    actual = float(b.get("actual_qty") or 0)
    reserved = float(b.get("reserved_qty") or 0)
    safety = float(frappe.db.get_value("Item", item_code, "safety_stock") or 0)
    sellable = actual - reserved - safety
    if sellable < 0:
        sellable = 0.0
    payload = {"sku": item_code, "quantity": sellable}
    event = "inventory.level.set"
    event_id = "frappe:inventory:%s:%s" % (item_code, ref)
    payload_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    log = _create_log(
        direction="Outbound", status="Queued", event=event, event_id=event_id,
        document_type="Item", document_name=item_code,
        payload_hash=payload_hash, request_body=payload,
    )
    deliver(log.name, event, event_id, payload, attempt=1)
