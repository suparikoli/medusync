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
