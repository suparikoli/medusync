# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Medusa → Polemarch wallet deposit/withdrawal.

This module owns the wallet side-effects the Medusa erpnext-plugin
triggers. It used to live in `apps/polemarch/polemarch/api/wallet_sync.py`
as a `@frappe.whitelist()` API; after the v3 split it lives here in
`medusync.handlers.polemarch.wallet` and is invoked only by the
`handle_deposit` / `handle_withdrawal` event handlers.

The sync-gate (`is_medusa_sync_enabled`) used to live inside
`record_deposit` / `record_withdrawal`. It has moved upstream to
`medusync.api.receive` — every event already passes that gate, so
double-checking it here would be redundant and would also block
operators who legitimately want to call these functions from a
Frappe form action in the future.

The Cashfree gateway-fee side JE (`_post_gateway_fee_je`) reads
`Polemarch Settings.wallet_gateway_fee_*` via cross-app
`frappe.get_single("Polemarch Settings")`. That field stays in
Polemarch because it's domain accounting (which Input Tax account
to debit for the fee), not Medusa protocol.
"""

from typing import Optional

import frappe
from frappe import _
from frappe.utils import flt


_VALID_DEPOSIT_SOURCES = {"cashfree", "manual", "bank_transfer", "razorpay", "stripe"}


# ── event handlers (called by medusync.api.receive) ─────────────────


def handle_deposit(data: dict, event_id: Optional[str] = None) -> dict:
	"""Medusa `wallet.deposit.captured` → create a Polemarch Wallet Deposit.

	Expected payload:
	  {
	    customer: <frappe customer name or email>,
	    amount:   <float>,
	    gateway_ref?: <str>,
	    posting_date?: <YYYY-MM-DD>,
	    source?: 'cashfree' (default) | 'manual' | 'bank_transfer' | 'razorpay' | 'stripe',
	    remarks?: <str>,
	  }
	"""
	return record_deposit(
		customer=data["customer"],
		amount=float(data["amount"]),
		posting_date=data.get("posting_date"),
		gateway_ref=data.get("gateway_ref") or event_id,
		source=data.get("source", "cashfree"),
		remarks=data.get("remarks"),
	)


def handle_withdrawal(data: dict, event_id: Optional[str] = None) -> dict:
	"""Medusa `wallet.withdrawal.posted` → create a Polemarch Wallet Withdrawal.

	Expected payload:
	  {
	    customer: <frappe customer name or email>,
	    amount:   <float>,
	    gateway_ref?: <str>,
	    posting_date?: <YYYY-MM-DD>,
	    remarks?: <str>,
	  }
	"""
	return record_withdrawal(
		customer=data["customer"],
		amount=float(data["amount"]),
		posting_date=data.get("posting_date"),
		gateway_ref=data.get("gateway_ref") or event_id,
		remarks=data.get("remarks"),
	)


# ── domain operations ──────────────────────────────────────────────


def record_deposit(
	customer: str,
	amount: float,
	posting_date: Optional[str] = None,
	gateway_ref: Optional[str] = None,
	source: str = "cashfree",
	remarks: Optional[str] = None,
) -> dict:
	"""Record a wallet deposit. For source='cashfree' (the default),
	also posts a side JE booking the gateway fee as a company
	expense — the customer's wallet credit is NOT reduced by the fee.
	"""
	if source not in _VALID_DEPOSIT_SOURCES:
		raise ValueError(
			_("Unknown deposit source {0}. Valid: {1}").format(
				source, ", ".join(sorted(_VALID_DEPOSIT_SOURCES))
			)
		)
	if not frappe.db.exists("Customer", customer):
		raise ValueError(_("Customer {0} does not exist.").format(customer))
	amt = flt(amount)
	if amt <= 0:
		raise ValueError(_("Amount must be greater than zero."))

	# Idempotency: if a Wallet Deposit with this gateway_ref already
	# exists (reference_no), return it instead of creating another.
	if gateway_ref:
		existing = frappe.db.get_value(
			"Wallet Deposit",
			{"reference_no": gateway_ref, "customer": customer, "docstatus": 1},
			["name", "amount"],
			as_dict=True,
		)
		if existing:
			return {
				"ok": True,
				"deposit": existing.name,
				"amount": flt(existing.amount),
				"fee_je": _find_fee_je_for_deposit(existing.name),
				"idempotent_skip": True,
			}

	posting_date = posting_date or frappe.utils.today()
	company = _company_for_customer_wallet(customer)

	deposit_fields: dict = {
		"doctype": "Wallet Deposit",
		"customer": customer,
		"company": company,
		"posting_date": posting_date,
		"amount": amt,
		"bank_or_cash_account": _bank_account_for_company(company),
		"mode": "Bank Transfer" if source != "manual" else "Other",
		"reference_no": gateway_ref,
		"remarks": remarks
		or f"Wallet deposit via {source} (gateway_ref={gateway_ref or 'none'})",
	}
	# Tag every Medusa-created doc so the pull cron knows to skip it
	# (otherwise Medusa would re-pull the deposit and double-credit).
	if frappe.db.has_column("Wallet Deposit", "medusa_originated"):
		deposit_fields["medusa_originated"] = 1
	deposit = frappe.get_doc(deposit_fields)
	deposit.flags.ignore_permissions = True
	deposit.insert(ignore_permissions=True)
	deposit.submit()

	# Post the gateway-fee side JE for cashfree-sourced deposits.
	fee_je_name = None
	if source == "cashfree":
		fee_je_name = _post_gateway_fee_je(
			company=company,
			posting_date=posting_date,
			deposit_name=deposit.name,
			gateway_ref=gateway_ref,
		)

	frappe.db.commit()
	return {
		"ok": True,
		"deposit": deposit.name,
		"amount": amt,
		"fee_je": fee_je_name,
		"idempotent_skip": False,
	}


def record_withdrawal(
	customer: str,
	amount: float,
	posting_date: Optional[str] = None,
	gateway_ref: Optional[str] = None,
	remarks: Optional[str] = None,
) -> dict:
	"""Record a wallet withdrawal (customer pulls funds back out). No
	gateway-fee booking on the withdrawal side — fees apply only on
	pay-ins.
	"""
	if not frappe.db.exists("Customer", customer):
		raise ValueError(_("Customer {0} does not exist.").format(customer))
	amt = flt(amount)
	if amt <= 0:
		raise ValueError(_("Amount must be greater than zero."))

	if gateway_ref:
		existing = frappe.db.get_value(
			"Wallet Withdrawal",
			{"reference_no": gateway_ref, "customer": customer, "docstatus": 1},
			["name", "amount"],
			as_dict=True,
		)
		if existing:
			return {
				"ok": True,
				"withdrawal": existing.name,
				"amount": flt(existing.amount),
				"idempotent_skip": True,
			}

	posting_date = posting_date or frappe.utils.today()
	company = _company_for_customer_wallet(customer)

	withdrawal_fields: dict = {
		"doctype": "Wallet Withdrawal",
		"customer": customer,
		"company": company,
		"posting_date": posting_date,
		"amount": amt,
		"bank_or_cash_account": _bank_account_for_company(company),
		"reference_no": gateway_ref,
		"remarks": remarks
		or f"Wallet withdrawal (gateway_ref={gateway_ref or 'none'})",
	}
	if frappe.db.has_column("Wallet Withdrawal", "medusa_originated"):
		withdrawal_fields["medusa_originated"] = 1
	wd = frappe.get_doc(withdrawal_fields)
	wd.flags.ignore_permissions = True
	wd.insert(ignore_permissions=True)
	wd.submit()
	frappe.db.commit()
	return {
		"ok": True,
		"withdrawal": wd.name,
		"amount": amt,
		"idempotent_skip": False,
	}


# ── helpers ─────────────────────────────────────────────────────────


def _post_gateway_fee_je(
	company: str,
	posting_date: str,
	deposit_name: str,
	gateway_ref: Optional[str],
) -> Optional[str]:
	"""Post a separate JE booking the gateway fee + GST as a company
	expense. Skips silently (returns None) if the settings don't have
	all three accounts configured — the operator can post the fee
	manually until they configure it.

	JE shape (per deposit):
		DR Payment Gateway Fee Expense    <fixed_fee>
		DR GST Input                       <gst_amount>
		CR Bank                            <fixed_fee + gst_amount>

	Tagged with `custom_source_doctype=Wallet Deposit` +
	`custom_source_name=<deposit_name>` so cancel-cascade and audit
	queries can find it.
	"""
	# Cross-app doctype access — read Polemarch's gateway-fee config
	# from Polemarch Settings (Polemarch owns the GST routing; medusync
	# just consumes it here).
	cfg = _get_gateway_fee_config()
	if not cfg["accounts_configured"]:
		frappe.log_error(
			f"Wallet Deposit {deposit_name}: gateway-fee JE skipped — "
			f"Polemarch Settings.wallet_gateway_fee_* accounts not all set. "
			f"Configure them in Desk → Polemarch Settings to enable auto-fee posting.",
			"Medusync Polemarch Gateway Fee Skip",
		)
		return None
	if cfg["total_fee_with_gst"] <= 0:
		return None

	cost_center = frappe.db.get_value("Company", company, "cost_center")

	# Build the GST-leg account lines per the configured strategy.
	# intra_state → 50/50 CGST + SGST split (typical when the gateway's
	#   GSTIN state == this Company's state, e.g. Cashfree Karnataka ↔
	#   MISPL Karnataka → 9% CGST + 9% SGST).
	# inter_state → single IGST line.
	# legacy_single → fall back to the deprecated single GST Input
	#   account so old tenants don't break before they migrate.
	gst_amount = cfg["gst_amount"]
	gst_lines: list[dict] = []
	strategy = cfg.get("gst_strategy")
	accts = cfg.get("gst_accounts", {}) or {}
	if strategy == "intra_state":
		half = round(gst_amount / 2.0, 2)
		# Avoid a rounding penny drift on odd amounts — anchor the
		# second leg as (gst - half) so the two sum exactly to
		# gst_amount even for ₹3.61 / ₹0.01 edge cases.
		gst_lines = [
			{
				"account": accts["cgst"],
				"debit_in_account_currency": half,
				"cost_center": cost_center,
			},
			{
				"account": accts["sgst"],
				"debit_in_account_currency": round(gst_amount - half, 2),
				"cost_center": cost_center,
			},
		]
	elif strategy == "inter_state":
		gst_lines = [
			{
				"account": accts["igst"],
				"debit_in_account_currency": gst_amount,
				"cost_center": cost_center,
			},
		]
	elif strategy == "legacy_single":
		gst_lines = [
			{
				"account": accts["legacy"],
				"debit_in_account_currency": gst_amount,
				"cost_center": cost_center,
			},
		]
	else:
		# Defensive: get_gateway_fee_config().accounts_configured should
		# have already returned False, but belt-and-suspenders.
		return None

	# India Compliance: any JE touching a GST account requires
	# `company_gstin` set on the doc. Lift from the Company master.
	company_gstin = frappe.db.get_value("Company", company, "gstin")

	je = frappe.get_doc(
		{
			"doctype": "Journal Entry",
			"voucher_type": "Bank Entry",
			"posting_date": posting_date,
			"company": company,
			"company_gstin": company_gstin,
			"user_remark": (
				f"Payment gateway fee for Wallet Deposit {deposit_name}"
				+ (f" (gateway_ref={gateway_ref})" if gateway_ref else "")
				+ f" [{strategy}]"
			),
			"cheque_no": gateway_ref or f"FEE-{deposit_name}",
			"cheque_date": posting_date,
			"custom_source_doctype": "Wallet Deposit",
			"custom_source_name": deposit_name,
			"accounts": [
				{
					"account": cfg["expense_account"],
					"debit_in_account_currency": cfg["fixed_fee"],
					"cost_center": cost_center,
				},
				*gst_lines,
				{
					"account": cfg["bank_account"],
					"credit_in_account_currency": cfg["total_fee_with_gst"],
					"cost_center": cost_center,
				},
			],
		}
	)
	je.flags.ignore_permissions = True
	je.insert(ignore_permissions=True)
	je.submit()
	return je.name


def _find_fee_je_for_deposit(deposit_name: str) -> Optional[str]:
	"""Locate the gateway-fee JE for a given Wallet Deposit, if any.

	Returns None when:
	  - The fee accounts aren't configured (no fees can ever be booked
	    on this tenant — common during early setup).
	  - The deposit exists but no fee JE was posted (source=manual or
	    cashfree fees disabled).
	"""
	cfg = _get_gateway_fee_config()
	if not cfg["accounts_configured"]:
		return None
	if not cfg["expense_account"]:
		return None
	rows = frappe.db.sql(
		"""
		SELECT DISTINCT je.name
		FROM `tabJournal Entry` je
		JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
		WHERE je.custom_source_doctype = 'Wallet Deposit'
		  AND je.custom_source_name = %s
		  AND je.docstatus = 1
		  AND jea.account = %s
		  AND jea.debit > 0
		LIMIT 1
		""",
		(deposit_name, cfg["expense_account"]),
	)
	return rows[0][0] if rows else None


def _company_for_customer_wallet(customer: str) -> str:
	"""Resolve the company hosting the customer's wallet. Each wallet is
	keyed `WAL-<customer>` and has a `company` field; if no wallet
	exists yet, fall back to the global default.
	"""
	wallet_name = f"WAL-{customer}"
	if frappe.db.exists("Wallet", wallet_name):
		return frappe.db.get_value("Wallet", wallet_name, "company")
	company = frappe.defaults.get_global_default("company")
	if not company:
		raise ValueError(_("No default Company configured."))
	return company


def _bank_account_for_company(company: str) -> str:
	"""Find the default Bank or Cash account for the company. Same
	resolution the Wallet Deposit controller does.
	"""
	direct = frappe.db.get_value("Company", company, "default_bank_account")
	if direct:
		return direct
	return frappe.db.get_value(
		"Account",
		{
			"company": company,
			"account_type": ["in", ["Bank", "Cash"]],
			"disabled": 0,
			"is_group": 0,
		},
		"name",
		order_by="account_type ASC, creation ASC",
	)


def _get_gateway_fee_config() -> dict:
	"""Best-effort accessor for the gateway-fee config.

	Polemarch owns this config (it's polemarch domain accounting —
	which P&L and Input Tax GL accounts the Cashfree fee should hit).
	The medusync wallet handler reads it to post the side JE.

	To honour the "medusync is operationally independent of polemarch"
	principle: try the polemarch import, and if polemarch is not
	installed, return a default config that signals "no side JE
	posted". The Wallet Deposit itself is still created and submitted
	— the operator gets a clear log message that the fee config is
	missing, and can post the side JE manually.
	"""
	try:
		from polemarch.polemarch_trading.doctype.polemarch_settings.polemarch_settings import (
			get_gateway_fee_config,
		)
		return get_gateway_fee_config()
	except ImportError:
		return {
			"accounts_configured": False,
			"fixed_fee": 0,
			"gst_amount": 0,
			"total_fee_with_gst": 0,
			"expense_account": None,
			"bank_account": None,
			"gst_strategy": None,
			"gst_accounts": {},
			"gst_input_account": None,
		}
