# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
"""Customer address sync (Medusa -> ERPNext Address).

A flat field mapping cannot create ERPNext Address docs (a separate doctype
linked to Customer via Dynamic Link), so the Customer branch of
 hands the normalized  list here
after the Customer is saved. Idempotent by the  Custom Field
(re-push updates the same Address); stale addresses (previously synced, now
absent from the incoming list) are DISABLED, never destroyed (safe-delete).
Never raises: a bad address must not fail the whole customer sync.
"""

import frappe


def _resolve_country(iso2):
    """ERPNext Country name from an ISO-2 code (Medusa sends e.g. \"in\")."""
    if not iso2:
        return None
    return frappe.db.get_value("Country", {"code": str(iso2).lower()}, "name")


def _addr_gstin_supported():
    try:
        return bool(frappe.get_meta("Address").get_field("gstin"))
    except Exception:
        return False


def sync_customer_addresses(customer_name, addresses):
    """Create/update Address docs for  from .
    Returns a summary dict {synced, skipped, disabled}."""
    if not addresses:
        return {"synced": [], "skipped": [], "disabled": []}

    gstin_ok = _addr_gstin_supported()
    seen, synced, skipped = set(), [], []

    for a in addresses:
        try:
            mid = a.get("medusa_address_id")
            if not mid:
                skipped.append({"reason": "no medusa_address_id"})
                continue
            country = _resolve_country(a.get("country_code"))
            if not country:
                skipped.append({"medusa_address_id": mid, "reason": "country %r not in ERPNext" % a.get("country_code")})
                continue
            seen.add(mid)
            existing = frappe.db.get_value("Address", {"medusa_address_id": mid}, "name")
            doc = frappe.get_doc("Address", existing) if existing else frappe.new_doc("Address")
            doc.medusa_address_id = mid
            doc.address_title = a.get("address_title") or customer_name
            doc.address_type = a.get("address_type") or "Billing"
            doc.address_line1 = a.get("address_line1") or "-"
            doc.address_line2 = a.get("address_line2")
            doc.city = a.get("city")
            doc.state = a.get("state")
            doc.pincode = a.get("pincode")
            doc.country = country
            doc.phone = a.get("phone")
            if gstin_ok and a.get("gstin"):
                doc.gstin = a.get("gstin")
            doc.disabled = 0
            has_link = any(
                l.link_doctype == "Customer" and l.link_name == customer_name
                for l in (doc.get("links") or [])
            )
            if not has_link:
                doc.append("links", {"link_doctype": "Customer", "link_name": customer_name})
            doc.flags.ignore_mandatory = True
            doc.save(ignore_permissions=True)
            synced.append(doc.name)
        except Exception as exc:
            frappe.db.rollback()
            skipped.append({"medusa_address_id": a.get("medusa_address_id"), "reason": str(exc)[:150]})

    # Safe-delete: disable this customer\047s previously-synced addresses that
    # are no longer in the incoming set.
    disabled = []
    prior = frappe.get_all(
        "Address",
        filters={"medusa_address_id": ["is", "set"], "disabled": 0},
        fields=["name", "medusa_address_id"],
    )
    for p in prior:
        if p.medusa_address_id in seen:
            continue
        linked = frappe.db.exists(
            "Dynamic Link",
            {"parenttype": "Address", "parent": p.name, "link_doctype": "Customer", "link_name": customer_name},
        )
        if linked:
            frappe.db.set_value("Address", p.name, "disabled", 1)
            disabled.append(p.name)

    frappe.db.commit()
    return {"synced": synced, "skipped": skipped, "disabled": disabled}
