# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""The Modes of Payment a store's captured payments need.

A Payment Entry needs a Mode of Payment, and a Mode of Payment needs a
default account per company or the entry cannot say where the money
landed. A store pays through providers ERPNext has never heard of, so the
modes usually have to be made — and the account is a real accounting
decision, so nothing here guesses one. `missing` reports what is absent
and why it would not work yet; `create` makes only what it is asked for.
"""

import frappe

# What a storefront typically collects. Not a rule: `missing` reports on
# these, and `create` will make any name it is given.
SUGGESTED = (
	("UPI Payment", "Bank"),
	("Credit Card", "Bank"),
	("Debit Card", "Bank"),
	("Net Banking", "Bank"),
	("Wallet", "Bank"),
	("Cash on Delivery", "Cash"),
)


def _account_for(mode: str, company: str) -> str | None:
	for row in frappe.get_all(
		"Mode of Payment Account",
		filters={"parent": mode, "company": company},
		fields=["default_account"],
		limit=1,
	):
		return row.get("default_account")
	return None


@frappe.whitelist()
def missing(company: str | None = None) -> dict:
	"""Which suggested modes do not exist, and which exist but cannot be
	used for this company yet.

	`unusable` is the more common surprise: the mode is there, so it can be
	picked on a Medusync Site, but with no default account for the company
	every Payment Entry against it fails at submit.
	"""
	company = company or frappe.defaults.get_global_default("company")
	existing = set(frappe.get_all("Mode of Payment", pluck="name"))
	absent, unusable = [], []
	for name, mode_type in SUGGESTED:
		if name not in existing:
			absent.append({"mode": name, "type": mode_type})
		elif company and not _account_for(name, company):
			unusable.append({"mode": name, "reason": "no default account for %s" % company})
	return {
		"company": company,
		"existing": sorted(existing),
		"absent": absent,
		"unusable": unusable,
	}


@frappe.whitelist()
def create(mode: str, mode_type: str = "Bank", company: str | None = None, account: str | None = None) -> dict:
	"""Make one Mode of Payment, and give it a default account when one is
	named.

	The account is left to the caller on purpose. Picking a company's bank
	or cash account is a bookkeeping decision, and a wrong guess here is
	only discovered as a misposted Payment Entry later.
	"""
	company = company or frappe.defaults.get_global_default("company")
	created = False
	if frappe.db.exists("Mode of Payment", mode):
		doc = frappe.get_doc("Mode of Payment", mode)
	else:
		doc = frappe.get_doc({"doctype": "Mode of Payment", "mode_of_payment": mode, "type": mode_type})
		doc.insert(ignore_permissions=True)
		created = True

	linked = False
	if account and company and not _account_for(mode, company):
		if not frappe.db.exists("Account", account):
			frappe.throw(frappe._("Account {0} does not exist.").format(account))
		doc.append("accounts", {"company": company, "default_account": account})
		doc.save(ignore_permissions=True)
		linked = True

	return {
		"mode": doc.name,
		"created": created,
		"account_linked": linked,
		"usable": bool(_account_for(doc.name, company)) if company else False,
	}


@frappe.whitelist()
def create_missing(company: str | None = None, accounts: dict | str | None = None) -> list:
	"""Make every suggested mode that does not exist yet.

	`accounts` optionally maps a mode name to the account its payments land
	in for this company. A mode created without one exists but is not usable
	until somebody sets the account; the result says which.
	"""
	if isinstance(accounts, str):
		accounts = frappe.parse_json(accounts) or {}
	accounts = accounts or {}
	company = company or frappe.defaults.get_global_default("company")
	out = []
	for row in missing(company)["absent"]:
		out.append(create(row["mode"], row["type"], company, accounts.get(row["mode"])))
	return out
