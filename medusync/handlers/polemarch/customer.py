# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Medusa → Polemarch Customer sync.

Three event handlers and three child-table syncers.

  handle_created         — storefront signup → create Frappe Customer
  handle_updated         — customer update + bank/demat child-row upsert
  handle_kyc_synced      — Medusa KYC decision echoes back to Frappe

  _sync_bank_accounts    — upsert into Customer.custom_bank_details
  _sync_demat_accounts   — upsert into Customer.custom_dp_details

The Mithtech-only filter is the Medusa side's job — Mithtech
customers never reach these handlers because operator policy creates
them in ERPNext only. Everything that arrives here is a Polemarch
customer.

Idempotency: keyed on Contact email. If a Customer with the same email
is already linked, the handler updates metadata rather than creating a
duplicate. New customers go through the after_insert hook chain
(Contact placeholder + customer_name back-sync).
"""

from typing import Optional

import frappe

from medusync.handlers.polemarch.common import (
	customer_by_email,
	compose_customer_name,
	default_customer_group,
	default_territory,
	populate_contact,
)


# ── event handlers ──────────────────────────────────────────────────


def handle_created(data: dict, event_id: Optional[str] = None) -> dict:
	"""Storefront signup → create a Frappe Customer.

	Expected payload (from the Medusa erpnext-forward subscriber's
	full-customer fetchById output):
	  id              <medusa customer id>
	  email           <required>
	  first_name, last_name, phone, company_name
	  addresses[0]    <primary address — optional>
	  metadata        <dict with optional kyc_pan, client_id>
	"""
	email = (data.get("email") or "").strip().lower()
	if not email:
		return {"status": "skipped", "reason": "no_email"}

	medusa_id = data.get("id") or event_id

	# Idempotency: find an existing Customer linked to this email via Contact
	existing_name = customer_by_email(email)
	if existing_name:
		# Update metadata only — don't reshape an existing operator-managed customer
		updates: dict = {}
		if data.get("metadata", {}).get("client_id") and not frappe.db.get_value(
			"Customer", existing_name, "custom_client_id"
		):
			updates["custom_client_id"] = data["metadata"]["client_id"]
		if updates:
			frappe.db.set_value("Customer", existing_name, updates, update_modified=False)
			frappe.db.commit()
		return {
			"status": "exists",
			"customer": existing_name,
			"medusa_id": medusa_id,
			"updates": list(updates.keys()),
		}

	# Create fresh Customer. The polemarch.overrides.customer.after_insert
	# hook will auto-create a placeholder Contact and link first_name/email/phone
	# via Dynamic Link. The validate hook will also auto-set
	# custom_is_polemarch_customer based on signals.
	customer_name = compose_customer_name(data)
	cust_doc = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": customer_name,
			"customer_type": "Individual",
			"customer_group": "Polemarch"
			if frappe.db.exists("Customer Group", "Polemarch")
			else default_customer_group(),
			"territory": default_territory(),
		}
	)
	cust_doc.flags.ignore_permissions = True
	cust_doc.flags.ignore_mandatory = True
	cust_doc.insert(ignore_permissions=True)

	# Populate the auto-created Contact with email + phone now (the
	# placeholder Contact only has first_name = customer_name).
	populate_contact(cust_doc.name, data)

	# Stamp the Medusa client id on the Frappe Customer for cross-system lookup
	metadata = data.get("metadata") or {}
	if metadata.get("client_id"):
		frappe.db.set_value(
			"Customer", cust_doc.name, "custom_client_id", metadata["client_id"],
			update_modified=False,
		)
	frappe.db.commit()
	return {
		"status": "created",
		"customer": cust_doc.name,
		"medusa_id": medusa_id,
	}


def handle_updated(data: dict, event_id: Optional[str] = None) -> dict:
	"""Customer update — top-level metadata sync PLUS bank/demat child
	table upsert when the payload includes those arrays.

	The bank/demat sync was wired in Phase 189 (Bank + BOID sync,
	Medusa→Frappe). The Medusa-side erpnext-forward subscriber listens
	to bank_account.verified / demat_account.verified events and
	rewrites them to customer.updated on the wire, with the enriched
	customer payload carrying:

	  bank_accounts: [
	    {bank_name, ifsc, account_number_last4, account_holder_name,
	     is_primary, verification_status, ...}
	  ]
	  demat_accounts: [
	    {depository, dp_id, client_id, boid, dp_name,
	     account_holder_name, is_primary, verification_status, ...}
	  ]

	Each Medusa row is upserted into the Customer's `custom_bank_
	details` / `custom_dp_details` child tables, keyed by:
	  - Bank: (ifsc, account_number_last4)
	  - Demat: (bo_id)

	Unverified Medusa rows are skipped (only `verification_status ==
	'verified'` rows land on Frappe — the operator workspace shouldn't
	show pending rows).
	"""
	email = (data.get("email") or "").strip().lower()
	if not email:
		return {"status": "skipped", "reason": "no_email"}
	customer = customer_by_email(email)
	if not customer:
		return {"status": "not_found", "email": email}

	metadata = data.get("metadata") or {}
	top_level_updates = {}
	if metadata.get("client_id") and not frappe.db.get_value(
		"Customer", customer, "custom_client_id"
	):
		top_level_updates["custom_client_id"] = metadata["client_id"]
	if top_level_updates:
		frappe.db.set_value(
			"Customer", customer, top_level_updates, update_modified=False
		)

	# ── Bank + Demat child-row upserts ──
	bank_synced = _sync_bank_accounts(
		customer, data.get("bank_accounts") or []
	)
	demat_synced = _sync_demat_accounts(
		customer, data.get("demat_accounts") or []
	)

	frappe.db.commit()
	return {
		"status": "synced",
		"customer": customer,
		"updates": list(top_level_updates.keys()),
		"bank_accounts": bank_synced,
		"demat_accounts": demat_synced,
	}


def handle_kyc_synced(data: dict, event_id: Optional[str] = None) -> dict:
	"""Medusa-side KYC verification echoes the verified PAN / Aadhaar
	flags back to Frappe. Only updates flags + verified_on — the
	canonical KYC decision (verify / reject) is operator-driven from
	the Frappe side via polemarch.api.kyc. This handler just records
	that Medusa has the latest state.
	"""
	email = (data.get("email") or "").strip().lower()
	if not email:
		return {"status": "skipped", "reason": "no_email"}
	customer = customer_by_email(email)
	if not customer:
		return {"status": "not_found", "email": email}
	updates = {}
	if data.get("pan_verified") is True:
		# Only stamp PAN onto custom_pan field, not the KYC status —
		# that requires the manual verify_kyc API call.
		pan = (data.get("pan") or "").upper().strip()
		if pan and not frappe.db.get_value("Customer", customer, "pan"):
			updates["pan"] = pan
	if updates:
		frappe.db.set_value("Customer", customer, updates, update_modified=False)
		frappe.db.commit()
	return {
		"status": "synced",
		"customer": customer,
		"updates": list(updates.keys()),
	}


# ── bank + demat child-row sync ────────────────────────────────────


def _sync_bank_accounts(customer_name: str, rows: list) -> dict:
	"""Upsert Medusa-verified banks into the Customer's
	custom_bank_details child table.

	Match key is (IFSC, last4). The Medusa side now decrypts the
	`account_number_encrypted` column server-side (via the wallet
	module's `listBankAccountsForSync` helper) and passes the FULL
	account number on `row.account_number`; we use that for
	`ac_number` so Frappe operators see the real number rather than
	just "1485". The `last4` field is kept around purely as the
	stable match key — full account numbers can be re-issued or
	masked differently between systems, but (IFSC, last4) is enough
	to identify a row without false positives.

	For backward compatibility (older payloads that only carry
	last4), we fall back to last4 in `ac_number` if `account_number`
	isn't present.
	"""
	if not rows:
		return {"upserted": 0, "skipped": 0, "removed_unverified": 0}
	customer_doc = frappe.get_doc("Customer", customer_name)
	existing_by_key = {
		(b.bank_code or "", (b.ac_number or "")[-4:]): b
		for b in (customer_doc.get("custom_bank_details") or [])
	}
	upserted = 0
	skipped = 0
	for row in rows:
		if (row.get("verification_status") or "") != "verified":
			skipped += 1
			continue
		ifsc = (row.get("ifsc") or "").upper()
		last4 = row.get("account_number_last4") or ""
		# Prefer the full decrypted account number — fall back to
		# last4 only if the wallet helper couldn't decrypt (key
		# rotation skew, etc.) or the payload predates the helper.
		full_number = row.get("account_number") or last4
		key = (ifsc, last4)
		existing = existing_by_key.get(key)
		payload = {
			"bank_name": row.get("bank_name") or "",
			"bank_code": ifsc,
			"ac_number": full_number,
			"account_holder": row.get("account_holder_name") or "",
			"is_primary": 1 if row.get("is_primary") else 0,
			"cheque_image": row.get("bank_proof_file_url") or "",
		}
		if existing:
			for k, v in payload.items():
				existing.set(k, v)
		else:
			customer_doc.append("custom_bank_details", payload)
		upserted += 1
	if upserted or skipped:
		customer_doc.flags.ignore_permissions = True
		customer_doc.save()
	return {"upserted": upserted, "skipped": skipped}


def _sync_demat_accounts(customer_name: str, rows: list) -> dict:
	"""Upsert Medusa-verified demats into the Customer's
	custom_dp_details child table. Key = bo_id (BOID is unique per
	depository — for CDSL it's the 16-digit number; for NSDL it's
	dp_id + client_id concatenated).
	"""
	if not rows:
		return {"upserted": 0, "skipped": 0}
	customer_doc = frappe.get_doc("Customer", customer_name)
	existing_by_key = {
		(b.bo_id or ""): b
		for b in (customer_doc.get("custom_dp_details") or [])
	}
	upserted = 0
	skipped = 0
	for row in rows:
		if (row.get("verification_status") or "") != "verified":
			skipped += 1
			continue
		depository = (row.get("depository") or "").upper()
		# CDSL: bo_id is the 16-digit BOID. The Medusa side typically
		# stores it monolithically in `boid` with `dp_id` + `client_id`
		# empty — but Frappe's DP Details child requires both fields.
		# Convention: first 8 digits = DP ID, last 8 digits = Client
		# ID. Split here so the child row passes mandatory validation.
		# NSDL: dp_id + client_id arrive populated; combine for bo_id.
		if depository == "CDSL":
			bo_id = row.get("boid") or ""
			dp_id = row.get("dp_id") or ""
			client_id = row.get("client_id") or ""
			if bo_id and len(bo_id) == 16 and not (dp_id and client_id):
				dp_id = bo_id[:8]
				client_id = bo_id[8:]
		else:
			dp_id = row.get("dp_id") or ""
			client_id = row.get("client_id") or ""
			bo_id = f"{dp_id}{client_id}"
		if not bo_id:
			skipped += 1
			continue
		existing = existing_by_key.get(bo_id)
		payload = {
			"dp_id": dp_id,
			"client_id": client_id,
			"bo_id": bo_id,
			"depository": depository,
			"dp_name": row.get("dp_name") or "",
			"primary_bo_name": row.get("account_holder_name") or "",
			"is_primary": 1 if row.get("is_primary") else 0,
			"cmr_copy": row.get("cmr_file_url") or "",
		}
		if existing:
			for k, v in payload.items():
				existing.set(k, v)
		else:
			customer_doc.append("custom_dp_details", payload)
		upserted += 1
	if upserted or skipped:
		customer_doc.flags.ignore_permissions = True
		customer_doc.save()
	return {"upserted": upserted, "skipped": skipped}
