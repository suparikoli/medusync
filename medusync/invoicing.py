# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""What a store order becomes in ERPNext, and who numbers its invoice.

Each store decides on its Medusync Site:

    A Store Order Becomes   Sales Order | Sales Invoice | both
    Invoice Numbering       ERPNext, company series |
                            ERPNext, a separate series for this store |
                            the store numbers it
    Record Store Payments   book what the store captured as Payment Entries

ERPNext numbering, either flavour, means the invoice number is born here
and the invoice travels back to the store for the customer to download.
The two differ only in which series it comes from: the company's own, or
one kept for this store so its invoices are countable on their own.

When the store numbers it, the store owns that series outright: ERPNext
books what arrives under the number it was given and sends nothing back,
because a second copy under a second number is two invoices for one sale.
Nobody can raise an invoice in that series from inside ERPNext either —
see `guard_store_series`, without which the two ends would eventually
both reach for the same number.
"""

import frappe

from medusync import links, sites

ORDER_DOCUMENT_SO = "Sales Order"
ORDER_DOCUMENT_SI = "Sales Invoice"
ORDER_DOCUMENT_BOTH = "Sales Order and Sales Invoice"

NUMBERING_ERPNEXT = "ERPNext numbers the invoice (company series)"
NUMBERING_ERPNEXT_SERIES = "ERPNext numbers the invoice (separate store series)"
NUMBERING_STORE = "The store numbers the invoice"

# What the option was called before it split in two. Sites saved under the
# old label keep meaning the company series.
NUMBERING_ERPNEXT_LEGACY = "ERPNext numbers the invoice"

DEFAULT_STORE_PREFIX = "STR-"

_PREFIX_CACHE_KEY = "medusync_store_invoice_prefixes"

SETTINGS_EVENT = "medusync.settings.changed"


def options(site_id: str | None = None) -> frappe._dict:
	"""The store's order and invoice choices, with the defaults a site
	created before these existed would have had."""
	site_id = site_id or links.current_site()
	row = {}
	if site_id and frappe.db.exists(sites.SITE_DOCTYPE, site_id):
		row = frappe.db.get_value(
			sites.SITE_DOCTYPE,
			site_id,
			[
				"order_document",
				"submit_documents",
				"record_payments",
				"mode_of_payment",
				"invoice_numbering",
				"store_invoice_prefix",
				"erpnext_invoice_series",
				"send_invoice_to_store",
				"invoice_print_format",
			],
			as_dict=True,
		) or {}
	numbering = row.get("invoice_numbering") or NUMBERING_ERPNEXT
	if numbering == NUMBERING_ERPNEXT_LEGACY:
		numbering = NUMBERING_ERPNEXT
	return frappe._dict(
		site_id=site_id,
		order_document=row.get("order_document") or ORDER_DOCUMENT_SO,
		submit_documents=bool(row.get("submit_documents")),
		record_payments=bool(row.get("record_payments")),
		mode_of_payment=row.get("mode_of_payment"),
		invoice_numbering=numbering,
		erpnext_invoice_series=(row.get("erpnext_invoice_series") or "").strip() or None,
		store_invoice_prefix=(row.get("store_invoice_prefix") or "").strip() or DEFAULT_STORE_PREFIX,
		send_invoice_to_store=bool(row.get("send_invoice_to_store")),
		invoice_print_format=row.get("invoice_print_format"),
	)


def target_doctype(mapped_doctype: str, opts) -> str:
	"""An order mapped to Sales Order becomes whatever this store asked for."""
	if mapped_doctype == ORDER_DOCUMENT_SO and opts.order_document == ORDER_DOCUMENT_SI:
		return ORDER_DOCUMENT_SI
	return mapped_doctype


def store_numbered(opts) -> bool:
	return opts.invoice_numbering == NUMBERING_STORE


def separate_series(opts) -> str | None:
	"""The series this store's invoices are numbered from when it has one of
	its own. None means the company's ordinary series."""
	if opts.invoice_numbering == NUMBERING_ERPNEXT_SERIES:
		return opts.erpnext_invoice_series
	return None


def insert_invoice(doc, payload: dict, opts) -> None:
	"""Insert a Sales Invoice under the store's number when the store
	numbers its invoices, and under ERPNext's series otherwise."""
	number = (payload.get("medusa_invoice_number") or "").strip() if store_numbered(opts) else ""
	if store_numbered(opts) and not number:
		frappe.throw(
			frappe._(
				"Store {0} numbers its own invoices but sent no invoice number. Check the plugin's invoice series."
			).format(opts.site_id)
		)
	if number:
		existing = frappe.db.exists("Sales Invoice", number)
		if existing:
			frappe.throw(frappe._("Sales Invoice {0} already exists.").format(number))
		# Tell the before_insert guard this number came from the store
		# that owns the series, not from someone raising one here.
		frappe.flags.medusync_store_series = True
		try:
			doc.insert(ignore_permissions=True, set_name=number)
		finally:
			frappe.flags.medusync_store_series = False
		return

	series = separate_series(opts)
	if series:
		_ensure_series(series)
		doc.naming_series = series
	doc.insert(ignore_permissions=True)


def _ensure_series(series: str) -> None:
	"""Offer the store's own series on Sales Invoice.

	A series ERPNext has never seen is not an error at insert time — it
	simply numbers from one — but it would be missing from the field's
	options, so nobody could pick it by hand afterwards.
	"""
	prop = frappe.db.get_value(
		"Property Setter",
		{"doc_type": "Sales Invoice", "field_name": "naming_series", "property": "options"},
		"value",
	)
	if prop is None:
		prop = frappe.get_meta("Sales Invoice").get_field("naming_series").options or ""
	lines = [ln for ln in str(prop).split("\n") if ln.strip()]
	if series in lines:
		return
	frappe.make_property_setter(
		{
			"doctype": "Sales Invoice",
			"doctype_or_field": "DocField",
			"fieldname": "naming_series",
			"property": "options",
			"value": "\n".join(lines + [series]),
			"property_type": "Text",
		},
		validate_fields_for_doctype=False,
	)


def guard_store_series(doc, method=None) -> None:
	"""Sales Invoice before_insert: the store's series belongs to the store.

	Whoever numbers a series has to be the only one counting, or both ends
	eventually hand out the same number. When a store numbers its own
	invoices, ERPNext books what that store sends (`insert_invoice` sets
	the flag) and refuses to raise one under the same prefix itself,
	whether by hand or from another automation.
	"""
	if doc.doctype != ORDER_DOCUMENT_SI or frappe.flags.get("medusync_store_series"):
		return
	candidate = str(doc.name or doc.get("naming_series") or "")
	if not candidate:
		return
	for site_id, prefix in _store_prefixes().items():
		if candidate.upper().startswith(prefix.upper()):
			frappe.throw(
				frappe._(
					"{0} is the invoice series of store {1}, which numbers its own invoices. "
					"Raise it in the store, or change Invoice Numbering on Medusync Site {1}."
				).format(prefix, site_id),
				title=frappe._("That series belongs to a store"),
			)


def _store_prefixes() -> dict:
	"""Prefix per enabled site that numbers its own invoices."""

	def build():
		rows = frappe.get_all(
			sites.SITE_DOCTYPE,
			filters={"enabled": 1, "invoice_numbering": NUMBERING_STORE},
			fields=["name", "store_invoice_prefix"],
		)
		out = {}
		for r in rows:
			prefix = (r.get("store_invoice_prefix") or DEFAULT_STORE_PREFIX).strip()
			if prefix:
				out[r["name"]] = prefix
		return out

	cached = frappe.cache().get_value(_PREFIX_CACHE_KEY)
	if cached is not None:
		return frappe.parse_json(cached)
	prefixes = build()
	frappe.cache().set_value(_PREFIX_CACHE_KEY, frappe.as_json(prefixes), expires_in_sec=300)
	return prefixes


def after_create(doc, payload: dict, opts, order_id) -> dict:
	"""Finish what the store asked for once the mapped document exists.

	Returns what was made, so the log row can say it.
	"""
	made = {"doctype": doc.doctype, "name": doc.name}
	invoice = doc if doc.doctype == "Sales Invoice" else None

	if doc.doctype == "Sales Order" and opts.order_document == ORDER_DOCUMENT_BOTH:
		# An invoice can only be made from a submitted order.
		if doc.docstatus == 0:
			doc.submit()
		invoice = _invoice_from_order(doc, payload, opts)
		links.remember("Sales Invoice", invoice.name, order_id, entity="order", site=opts.site_id)
		made["invoice"] = invoice.name
	elif opts.submit_documents and doc.docstatus == 0:
		doc.submit()

	if invoice is not None and store_numbered(opts):
		links.remember("Sales Invoice", invoice.name, invoice.name, entity="invoice", site=opts.site_id)

	if opts.record_payments:
		made["payments"] = record_payments(doc, invoice, payload, opts)
	return made


def _invoice_from_order(order, payload: dict, opts):
	from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

	invoice = make_sales_invoice(order.name)
	insert_invoice(invoice, payload, opts)
	if opts.submit_documents:
		invoice.submit()
	return invoice


def record_payments(order, invoice, payload: dict, opts) -> list:
	"""One Payment Entry per payment the store captured. Idempotent by the
	store's payment id.

	Booked against the invoice when there is a submitted one, otherwise as
	an advance on the submitted order. A draft cannot be paid, so its
	payments wait; the result says so rather than failing the order.
	"""
	out = []
	payments = [p for p in (payload.get("medusa_payments") or []) if _captured(p)]
	if not payments:
		return out
	against = invoice if invoice is not None and invoice.docstatus == 1 else None
	if against is None and order.doctype == "Sales Order" and order.docstatus == 1:
		against = order
	if against is None:
		return [{"id": p.get("id"), "status": "waiting", "reason": "the document is a draft"} for p in payments]
	if not opts.mode_of_payment:
		return [{"id": p.get("id"), "status": "skipped", "reason": "no Mode of Payment on the store"} for p in payments]

	for payment in payments:
		pid = str(payment.get("id") or "").strip()
		if not pid:
			continue
		if links.name_for("Payment Entry", pid, site=opts.site_id, entity="payment"):
			out.append({"id": pid, "status": "exists"})
			continue
		try:
			entry = _payment_entry(against, payment, opts)
			links.remember(
				"Payment Entry",
				entry.name,
				pid,
				entity="payment",
				site=opts.site_id,
				details={"provider": payment.get("provider_id")},
			)
			out.append({"id": pid, "status": "created", "payment_entry": entry.name})
		except Exception as exc:
			frappe.log_error(title="Medusync could not record a store payment", message=frappe.get_traceback())
			out.append({"id": pid, "status": "failed", "reason": str(exc)[:200]})
	return out


def _captured(payment: dict) -> bool:
	return float(payment.get("amount") or 0) > 0 and bool(payment.get("captured_at"))


def _payment_entry(against, payment: dict, opts):
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account

	amount = float(payment.get("amount") or 0)
	entry = get_payment_entry(against.doctype, against.name, party_amount=amount, bank_amount=amount)
	entry.mode_of_payment = opts.mode_of_payment
	account = get_bank_cash_account(opts.mode_of_payment, against.company).get("account")
	if account:
		entry.paid_to = account
	entry.paid_amount = amount
	entry.received_amount = amount
	entry.reference_no = payment.get("reference") or payment.get("id")
	entry.reference_date = str(payment.get("captured_at"))[:10]
	for row in entry.references or []:
		row.allocated_amount = min(float(row.outstanding_amount or amount), amount)
	entry.insert(ignore_permissions=True)
	if opts.submit_documents or against.docstatus == 1:
		entry.submit()
	return entry


# ── Telling the store ────────────────────────────────────────────────


def announce(doc, method=None) -> None:
	"""Medusync Site on_update: send this store the choices it acts on.

	The store numbers invoices and serves them to customers, so it has to
	know the series and whether an invoice is coming. Sent to that store
	alone.
	"""
	from medusync import config
	from medusync.outbound import emit

	if not doc.get("enabled") or not config.is_enabled():
		return
	try:
		opts = options(doc.site_id)
		body = {
			"order_document": opts.order_document,
			"invoice_numbering": "store" if store_numbered(opts) else "erpnext",
			"erpnext_invoice_series": separate_series(opts),
			"store_invoice_prefix": opts.store_invoice_prefix if store_numbered(opts) else None,
			"send_invoice_to_store": opts.send_invoice_to_store and not store_numbered(opts),
			"record_payments": opts.record_payments,
		}
		emit(
			SETTINGS_EVENT,
			body,
			ref="%s-%s" % (doc.site_id, frappe.utils.now_datetime().strftime("%Y%m%d%H%M%S%f")),
			per_site=lambda site_id, payload: payload if site_id == doc.site_id else None,
		)
	except Exception:
		frappe.log_error(title="Medusync could not send invoice options to a store", message=frappe.get_traceback())


def clear_cache(doc=None, method=None) -> None:
	"""Medusync Site on_update/on_trash: forget which prefixes are spoken for."""
	frappe.cache().delete_value(_PREFIX_CACHE_KEY)
