# Post-order reverse path: ERPNext -> Medusa order metadata.
# Delivery Note -> fulfilment (or RETURN receipt if is_return),
# Shipment -> tracking, Sales Invoice -> invoice (or REFUND/credit note if is_return).
import hashlib
import json

import frappe

from medusync import config
from medusync.outbound import _create_log, deliver


def _order_id_from_so(so_name):
    if not so_name:
        return None
    return frappe.db.get_value("Sales Order", so_name, "medusa_order_id")


def _deliver(event, medusa_order_id, payload, ref, doctype, docname):
    body = dict(payload)
    body["medusa_order_id"] = medusa_order_id
    event_id = "frappe:%s:%s" % (event, ref)
    ph = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    log = _create_log(
        direction="Outbound", status="Queued", event=event, event_id=event_id,
        document_type=doctype, document_name=docname, payload_hash=ph, request_body=body,
    )
    deliver(log.name, event, event_id, body, attempt=1)


def _guard():
    return not frappe.flags.get("medusync_inbound") and config.is_enabled()


def _so_from_dn(doc):
    for it in (doc.items or []):
        if it.get("against_sales_order"):
            return it.against_sales_order
    if doc.get("return_against"):
        rows = frappe.get_all(
            "Delivery Note Item",
            filters={"parent": doc.return_against, "against_sales_order": ["!=", ""]},
            fields=["against_sales_order"], limit=1,
        )
        if rows:
            return rows[0].against_sales_order
    return None


def on_delivery_note(doc, method=None):
    try:
        if not _guard():
            return
        oid = _order_id_from_so(_so_from_dn(doc))
        if not oid:
            return
        cancelled = method == "on_cancel" or getattr(doc, "docstatus", 0) == 2
        if doc.get("is_return"):
            # Return RECEIPT. Stock restore flows automatically via the inventory
            # SLE hook (the return DN increments Bin.actual_qty on submit).
            payload = {
                "status": "cancelled" if cancelled else "received",
                "items": [{"sku": it.item_code, "qty": abs(float(it.qty or 0))} for it in (doc.items or [])],
                "return_against": doc.get("return_against"),
                "received_at": str(doc.get("posting_date") or ""),
            }
            _deliver("order.returned", oid, payload, "%s-%s" % (doc.name, method), "Delivery Note", doc.name)
            return
        payload = {
            "status": "cancelled" if cancelled else "dispatched",
            "items": [{"sku": it.item_code, "qty": it.qty} for it in (doc.items or [])],
            "lr_no": doc.get("lr_no"),
            "transporter": doc.get("transporter_name") or doc.get("transporter"),
            "vehicle_no": doc.get("vehicle_no"),
            "dispatched_at": str(doc.get("posting_date") or ""),
        }
        _deliver("order.fulfilled", oid, payload, "%s-%s" % (doc.name, method), "Delivery Note", doc.name)
    except Exception:
        frappe.log_error(title="medusync reverse DN hook failed", message=frappe.get_traceback())


def on_shipment(doc, method=None):
    try:
        if not _guard():
            return
        so = None
        for dn in (doc.get("shipment_delivery_note") or []):
            dnname = dn.get("delivery_note")
            if not dnname:
                continue
            rows = frappe.get_all(
                "Delivery Note Item",
                filters={"parent": dnname, "against_sales_order": ["!=", ""]},
                fields=["against_sales_order"], limit=1,
            )
            if rows:
                so = rows[0].against_sales_order
                break
        oid = _order_id_from_so(so)
        if not oid:
            return
        ts = doc.get("tracking_status")
        payload = {
            "awb_number": doc.get("awb_number"),
            "carrier": doc.get("carrier"),
            "carrier_service": doc.get("carrier_service"),
            "tracking_url": doc.get("tracking_url"),
            "tracking_status": ts,
            "delivered": str(ts or "").lower() == "delivered",
        }
        _deliver("order.tracking", oid, payload, "%s-%s" % (doc.name, method), "Shipment", doc.name)
    except Exception:
        frappe.log_error(title="medusync reverse Shipment hook failed", message=frappe.get_traceback())


def on_sales_invoice(doc, method=None):
    try:
        if not _guard():
            return
        oid = None
        for it in (doc.items or []):
            if it.get("sales_order"):
                oid = _order_id_from_so(it.sales_order)
                if oid:
                    break
        if not oid and doc.get("medusa_order_id"):
            oid = doc.get("medusa_order_id")
        if not oid:
            return
        cancelled = method == "on_cancel" or getattr(doc, "docstatus", 0) == 2
        if doc.get("is_return"):
            # Credit Note -> refund record (no money moved).
            payload = {
                "credit_note": doc.name,
                "amount": abs(float(doc.get("grand_total") or 0)),
                "date": str(doc.get("posting_date") or ""),
                "currency": doc.get("currency"),
                "status": "Cancelled" if cancelled else "Credited",
                "reason": doc.get("remarks") or None,
            }
            _deliver("order.refunded", oid, payload, "%s-%s" % (doc.name, method), "Sales Invoice", doc.name)
            return
        status = "Cancelled" if cancelled else ("Paid" if float(doc.get("outstanding_amount") or 0) <= 0 else "Unpaid")
        payload = {
            "invoice_number": doc.name,
            "invoice_date": str(doc.get("posting_date") or ""),
            "grand_total": float(doc.get("grand_total") or 0),
            "currency": doc.get("currency"),
            "status": status,
        }
        _deliver("order.invoiced", oid, payload, "%s-%s" % (doc.name, method), "Sales Invoice", doc.name)
    except Exception:
        frappe.log_error(title="medusync reverse SI hook failed", message=frappe.get_traceback())


def create_pending_return(medusa_order_id, items):
    """Medusa customer return request -> a DRAFT return Delivery Note in ERPNext.
    Draft => zero stock impact; ops submits on physical receipt (which fires
    on_delivery_note -> order.returned + restores stock). The receipt gate.
    `items` = [{sku, qty, reason}]. Idempotent by medusa_order_id (skip if a
    draft return already pending)."""
    from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_return as dn_return
    so = frappe.db.get_value("Sales Order", {"medusa_order_id": medusa_order_id}, "name")
    if not so:
        return {"skipped": True, "reason": "no Sales Order for %s" % medusa_order_id}
    dn = frappe.db.get_value(
        "Delivery Note Item", {"against_sales_order": so, "docstatus": 1}, "parent"
    )
    if not dn:
        return {"skipped": True, "reason": "no submitted Delivery Note for SO %s" % so}
    existing = frappe.db.get_value(
        "Delivery Note", {"return_against": dn, "is_return": 1, "docstatus": 0}, "name"
    )
    if existing:
        return {"ok": True, "return_dn": existing, "status": "already pending"}
    want = {str(i.get("sku")): float(i.get("qty") or 0) for i in (items or [])}
    rdn = dn_return(dn)
    # Force every return line negative (qty AND stock_qty), then narrow to the
    # requested skus/qtys. ERPNext validates that return docs are negative.
    for row in rdn.items:
        cf = row.get("conversion_factor") or 1
        req = want.get(row.item_code)
        row.qty = -abs(req) if req else -abs(row.qty or 0)
        row.stock_qty = row.qty * cf
    if want:
        kept = [r for r in rdn.items if r.item_code in want]
        if kept:
            rdn.items = kept
    rdn.insert(ignore_permissions=True)  # DRAFT (not submitted) -> no stock impact
    frappe.db.commit()
    return {"ok": True, "return_dn": rdn.name, "status": "draft (pending receipt)"}


# ── Inbound: Medusa customer return REQUEST -> DRAFT return DN ──────────
# Registered into the handler registry (see handlers/risitex/__init__.py)
# so it arrives through medusync.api.receive with the full transport
# hardening (HMAC + replay window + idempotency + Medusync Log row).
# This is the last-mile trigger: the Medusa admin "request return" route
# POSTs event ; here we turn it into a DRAFT
# return Delivery Note (zero stock impact) awaiting warehouse receipt.
def handle_return_requested(payload, *, event_id=""):
	"""Envelope: {"medusa_order_id": "...", "items": [{"sku","qty","reason"}]}.
	Returns a dict the api._result_* closers understand (name -> the return
	DN so the audit row links it; status -> Success/Skipped)."""
	oid = payload.get("medusa_order_id") or payload.get("order_id")
	items = payload.get("items") or []
	if not oid:
		return {"status": "skipped", "reason": "no_medusa_order_id"}
	# The handler-driven receive() path dispatches as Guest (the endpoint is
	# allow_guest, authed by HMAC), and receive() -- unlike receive_mapped --
	# does not elevate. Reading Delivery Note and running make_sales_return
	# need real permissions, so elevate here (restored in finally).
	_prev_user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		res = create_pending_return(oid, items)
	except frappe.ValidationError as exc:
		# Permanent business rejection (e.g. StockOverReturnError -- nothing
		# left to return, or over-qty). Retrying will never succeed, so do
		# NOT let it become a 5xx (which tells Medusa to retry). Roll back and
		# report a clean skip carrying the ERPNext reason for the admin.
		frappe.db.rollback()
		return {"status": "skipped", "reason": str(exc), "order_id": oid}
	finally:
		frappe.set_user(_prev_user)
	# create_pending_return skips (200, not an error) when nothing has
	# shipped yet or no SO exists — surface that verbatim so the admin sees
	# WHY, rather than a silent success.
	# NB: deliberately avoid the keys api._result_name treats as a docname
	# (name / customer / sale / medusa_id / ...). The handler-driven receive()
	# path inserts the Medusync Log row with document_type=None, so setting
	# document_name (a Dynamic Link) would fail validation ("Document Type
	# must be set first"). We keep the return DN in  (non-magic),
	# which still travels back to Medusa in the response .
	if res.get("skipped"):
		return {"status": "skipped", "reason": res.get("reason"), "order_id": oid}
	return {
		"status": "created",
		"action": "created",
		"return_dn": res.get("return_dn"),
		"detail": res.get("status"),
		"order_id": oid,
	}
