# Apply Medusa order financials (addresses, tax/shipping charges, discount) onto
# a Sales Order so its grand_total matches the Medusa order total. Called from
# mapped.py after the base doc is built (create path).
import frappe


def _country(code):
    if not code:
        return None
    return frappe.db.get_value("Country", {"code": str(code).lower()}, "name")


def _charge_account(company):
    existing = frappe.db.get_value(
        "Account", {"company": company, "account_name": "Medusa Charges"}, "name"
    )
    if existing:
        return existing
    parent = frappe.db.get_value(
        "Account", {"company": company, "account_name": "Duties and Taxes", "is_group": 1}, "name"
    ) or frappe.db.get_value(
        "Account", {"company": company, "is_group": 1, "root_type": "Liability"}, "name"
    )
    acc = frappe.get_doc({
        "doctype": "Account", "account_name": "Medusa Charges", "company": company,
        "parent_account": parent, "account_type": "Tax", "root_type": "Liability", "is_group": 0,
    })
    acc.insert(ignore_permissions=True)
    return acc.name


def _address(customer, a, kind):
    if not a or not (a.get("address_line1") or a.get("city")):
        return None
    title = "%s-%s-Medusa" % (customer, kind)
    existing = frappe.db.get_value("Address", {"address_title": title}, "name")
    if existing:
        return existing
    doc = frappe.get_doc({
        "doctype": "Address", "address_title": title, "address_type": kind,
        "address_line1": a.get("address_line1") or a.get("city") or "NA",
        "address_line2": a.get("address_line2"),
        "city": a.get("city") or "NA", "state": a.get("state"),
        "pincode": a.get("pincode"), "country": _country(a.get("country")),
        "phone": a.get("phone"),
        "links": [{"link_doctype": "Customer", "link_name": customer}],
    })
    doc.insert(ignore_permissions=True)
    return doc.name


def apply_financials(doc, customer, payload):
    try:
        ba = _address(customer, payload.get("medusa_billing_address"), "Billing")
        sa = _address(customer, payload.get("medusa_shipping_address"), "Shipping")
        if ba and doc.meta.get_field("customer_address"):
            doc.customer_address = ba
        if sa and doc.meta.get_field("shipping_address_name"):
            doc.shipping_address_name = sa
        disc = float(payload.get("medusa_discount_total") or 0)
        if disc:
            if doc.meta.get_field("apply_discount_on"):
                doc.apply_discount_on = "Net Total"
            if doc.meta.get_field("discount_amount"):
                doc.discount_amount = disc
        acc = None
        for label, amt in (("Tax", payload.get("medusa_tax_total")),
                           ("Shipping", payload.get("medusa_shipping_total"))):
            amt = float(amt or 0)
            if amt:
                if not acc:
                    acc = _charge_account(doc.company)
                doc.append("taxes", {
                    "charge_type": "Actual", "account_head": acc,
                    "description": "Medusa %s" % label, "tax_amount": amt,
                })
    except Exception:
        frappe.log_error(title="medusync apply_financials failed", message=frappe.get_traceback())
