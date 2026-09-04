# Wallet / store-credit sync (Frappe side) — deferred 2026-09-04

**Requirement.** Wallet transactions sync bidirectionally by default: ERPNext
wallet changes → Medusa, Medusa wallet changes → ERPNext. Fields: Customer,
Wallet ID, Currency, Balance, Transaction Type, Amount, Reference Type/ID,
Notes, Timestamp. Events: Deposit/Credit, Withdrawal/Debit, Payment, Refund,
Reversal.

**Why deferred.** The local demo site no longer has a wallet doctype: the
`risitex_erp` app (which owned `RISITEX Wallet Settlement`) was uninstalled
from `site1.local` on 2026-09-04, and the sandbox Medusa store dropped its
`wallet_settlement` demo module. No real wallet exists locally to test
against.

**What exists today (reference only).**
- `medusync/handlers/polemarch/wallet.py`: Polemarch's securities-wallet
  implementation (Wallet Deposit / Wallet Withdrawal doctypes, Cashfree
  gateway-fee journal entries). Domain-specific; stays inside the Polemarch
  pack.
- The generic mapping engine (`Medusync Mapping` + `Medusync Field Map`) can
  already mirror any custom wallet doctype two-way; the earlier
  `Wallet Settlement to Medusa` mapping proved that path.

**Target design (Phase 3 slot).**
- A `wallet_transaction` entity in the default mapping catalogue whose ERPNext
  doctype is chosen per site in `Medusync Settings` (no doctype ships with
  medusync).
- Per-site, per-field direction like every other entity; amounts stored in
  minor units with explicit currency; reversals reference the original
  transaction id; idempotent on the wallet transaction id.

**Dependencies.** Phase 1 (`Medusync Site`, envelope v2, mapping model v2,
opt-in handler packs). A demo wallet doctype or a client site with one.

## What was cleared away on 2026-09-05

Auditing this before Phase 6 found that the Phase 0 removal took the
modules and left everything pointing at them. The Medusa side had three
pieces of debris and they are fixed (see the plugin's copy of this file).

On this side there is one thing left, and it is left on purpose:
**`Customer.wallet_balance_paise` still exists** and nothing writes to it.
It came from the uninstalled `risitex_erp` app. Deleting it would be tidy
and would also throw away the obvious landing place for a balance when the
contract below is built, so it stays until that decision is made.

There is no wallet DocType on this site at all — `RISITEX Wallet
Settlement` went with the uninstall — so nothing on the ERPNext side can
be mapped to a wallet today. The Medusa side does have a real wallet with
real data (`cashfree_wallet`: 22 wallets, 11 transactions), but it belongs
to the Polemarch securities domain rather than to the generic commerce
contract this file is about. It is a reasonable thing to test the
transport against; it is not the thing to model the contract on.
