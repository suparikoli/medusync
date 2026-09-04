# Questions — answer these first

Everything else in this folder is blocked on one of these. Each is a real
fork in the design where guessing would produce work that has to be thrown
away, so none of them has been decided.

Answer by number: **"Q4: b, Q11: c, Q18: invoice"** is enough. Where I have
a recommendation it is marked ★ — that is what I would build if you told
me to use my judgement, not a default that happens without you.

The same file is in `medusync/pending_work/`. Answering here answers both.

---

## A. Questions that block a specific piece of work

### Wallet / store credit — `2026-09-04-wallet-sync.md`

**Q1. Which ERPNext DocType holds the wallet on the first client that needs
this?**
No wallet DocType ships with medusync and the demo one went with the
`risitex_erp` uninstall. Medusa has a real `cashfree_wallet` module with 22
wallets, but it belongs to the Polemarch securities domain rather than to
generic commerce.
(a) Name an existing DocType on a client site
(b) medusync ships one, and clients use it as-is
(c) ★ medusync ships nothing and the DocType is named per site in Settings,
like the catalogue DocType already is

**Q2. Which side owns the balance?**
(a) ERPNext — Medusa reports transactions and ERPNext computes
(b) ★ ERPNext — same, and Medusa's balance is a cached read-only figure
(c) Both, reconciled — this needs a tie-break rule and I would want to
know yours before writing it

**Q3. `Customer.wallet_balance_paise` still exists on the ERPNext side with
nothing writing to it. Keep it as the landing place for a balance, or drop
it?**
(a) ★ Keep — it is where a balance would land
(b) Drop it in the next patch

### Percentage Pricing Rules and MRP — `2026-09-04-pricing-rules-and-mrp.md`

**Q4. Where should an ERPNext Pricing Rule expressed as a percentage or a
discount land in Medusa?**
Flat-rate tier prices already sync. Percentages have no direct counterpart.
(a) A Medusa price-list rule, computed to a flat amount at sync time
(b) A Medusa promotion
(c) ★ Variant metadata only, with the storefront applying it — the least
wrong until somebody needs it enforced at checkout
(d) Not at all

**Q5. Where should MRP land?**
(a) Variant metadata, for display only
(b) ★ A second Medusa price list named `MRP`, so the storefront can strike
it through with real price machinery
(c) Not at all

**Q6. There is no ERPNext field for MRP on the demo site and no Pricing
Rules seeded. Can you seed a site, or should I add an `Item.mrp` custom
field in a patch and seed it myself?**
(a) You will seed one
(b) ★ I add the field and seed a couple of rules to test against

### Variant options, barcode, brand — `2026-09-04-variant-attributes.md`

**Q7. Do ERPNext Item variant templates become Medusa product options, or
does each ERPNext variant become its own Medusa product?**
(a) ★ Options — one Medusa product with a variant per combination, which is
what a storefront expects
(b) Separate products — simpler, and loses the size picker

**Q8. Which Item Attributes are the option axes?**
Colour and size are the usual two, but ERPNext allows any. Fixed list, or
read from the Item's own attribute table per product?
(a) ★ Read from the Item — no configuration, works for any client
(b) A configured list in Settings

**Q9. Barcode, UOM and brand — where does each land?**
`Item Barcode` is a child table, so there can be several.
(a) ★ Barcode: the first row, or the one whose type is `EAN`, into
`variant.barcode`. UOM: the stock UOM into variant metadata; conversions
not synced. Brand: `Item.brand` into variant metadata.
(b) Something else — say which for each

### Prices coming back from Medusa — `2026-09-04-inbound-price-path.md`

**Q10. When Medusa sends a price back, which ERPNext Price List does it
belong to?**
The direction is already stored per list per store; what is missing is the
target.
(a) ★ The list mapped as Base Price for that store, and refuse if that list
is not marked `Two-way`
(b) A dedicated list per store, created on first use
(c) Carried on the payload, and ERPNext trusts it

**Q11. A Medusa customer-tier price has no quantity bracket. ERPNext tiers
use `packing_unit` as the bracket. What happens on the way back?**
(a) ★ Refuse tier prices inbound; base prices only. Tiers stay ERPNext's to
own
(b) Accept at bracket 1, overwriting the single-unit price
(c) Carry the bracket on the payload

**Q12. Rounding.** ERPNext sends 799.00, Medusa stores 79900 minor units and
sends back 799.0. The echo guard compares payloads.
(a) ★ Compare to two decimal places and treat equal-within-tolerance as an
echo
(b) Exact comparison, and accept the occasional redundant round trip

**Q13. May an inbound price create an Item Price that does not exist, or
only update one that does?**
(a) ★ Update only — a price appearing in ERPNext from a storefront is a
surprise nobody asked for
(b) Create and update

### Product images — `2026-09-04-product-images.md`

**Q14. Who moves the bytes?**
(a) ★ Medusa pulls a URL ERPNext gives it — fewer moving parts, and Medusa
already owns file storage
(b) ERPNext pushes to a Medusa upload endpoint

**Q15. ERPNext private files are not fetchable without a session. What
should happen to them?**
(a) ★ Skip private files and say so in the log — an operator who wants an
image on the storefront can make it public
(b) Give Medusa a scoped read token
(c) ERPNext copies them to public on sync

**Q16. What counts as a change worth re-sending?**
(a) ★ A content hash computed on the ERPNext side and carried in the payload
(b) File size and modified timestamp
(c) Always re-send — simple and expensive

**Q17. Which image is the primary, and do the others sync at all?**
(a) ★ `Item.image` is the primary; other attachments do not sync until
somebody asks
(b) `Item.image` first, then every image attachment in order

### An order's payment status — `2026-09-05-order-payment-status.md`

**Q18. Which document is the payment authority when a Sales Order and a
Sales Invoice disagree?**
This one is live and mildly wrong today: an order paid the ordinary way
through an invoice reads `unpaid` in `erp_order.payment.status`, because
that figure is computed from the order's `advance_paid` alone.
(a) The invoice when there is one — matches how accounts think; the status
flips to `unpaid` the moment an invoice is raised and before money lands,
which reads as a regression to anyone watching the store
(b) ★ The sum of Payment Entry receipts — truest to the money, already
computed as `erp_payments_total`, and ignores credit terms entirely
(c) Report both separately, and let the storefront choose

---

## B. Decisions about what is already built

**Q19. Turn off the legacy Single secret now?**
It is on, it works, and it is the one place where "which store is this?" is
answered by something other than the signature. Every connected store on
this installation has delivered with its own secret.
(a) ★ Turn it off on this site now; leave the default on for a client
mid-upgrade
(b) Leave it on everywhere

**Q20. A Medusa hard reset switches off *every* mapping, including ones
ERPNext never touched. Should it be scoped to the connection being reset?**
Harmless with one connection, wrong the moment a second store connects.
(a) ★ Scope it by `site_id` before a second connection exists
(b) Leave it — one Medusa talks to one ERPNext

**Q21. Both sides now refuse to be switched on remotely. That means they
can disagree about `enabled` at the same version, with nothing reconciling
it. Do you want a reconcile action?**
(a) ★ Yes — a "differences with the other side" view and a per-mapping
"take theirs / keep mine"
(b) No — the Needs Attention flag is enough

**Q22. The shipped default set is three mappings: Customers, Catalogue,
Orders. Enough to start a client on?**
(a) ★ Yes — everything past those three is genuinely per-project
(b) Add more — say which

---

## C. Getting it in front of a client

**Q23. The branch is `feat/phase0-1-foundation` on both repos and now holds
six phases plus this follow-up. Rename before opening pull requests?**
A rename means force-pushing the new name and deleting the old one on both
remotes; the history is unchanged.
(a) ★ Rename to `feat/configurable-bidirectional-sync` and open one PR per
repo against `master` / `main`
(b) Open the PRs under the current name
(c) Neither yet

**Q24. Which client goes first — Polemarch, Splendax, or a fresh site?**
It decides what gets tested next: Polemarch has the securities domain and a
real wallet, Splendax presumably does not, and a fresh site is the only way
to find out what a first install is actually like.
★ A fresh site, because everything above is written for the second client
and none of it has met one.

**Q25. Is there a deadline or a client date I should be building towards?**
Nothing in the brief mentioned one, and it changes what is worth doing next.
