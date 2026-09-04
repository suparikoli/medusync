# Questions — answer these first

Every topic in this folder is blocked on a decision below. None has been
guessed at, because guessing here produces work that gets thrown away.

## How to use this file

**Answering.** Write under the `Answer:` line of a question. A letter is
enough; a sentence is better where the option lists do not cover what you
want.

```
> **Answer:** b — but only for the retail list, not wholesale.
```

**Working from it.** This is the first file to open when picking up
anything in `pending_work/`. If the question a topic cites has no answer
yet, stop and ask for one — send the link to this file — rather than
implementing against an assumption.

**Keeping it clean.** When a question has been answered *and* the work it
was blocking is done, delete the question and its answer. The decision
survives in the commit that implemented it and in the code; leaving it
here turns this into a changelog nobody reads. A question with an answer
and no work yet stays.

Where I have a view it is marked ★ — that is what I would build if told to
use my own judgement, not a default that happens without you.

The same file is in `medusync/pending_work/` and the two are kept
identical. Answering in either answers both.

---

## A. Questions that block a specific piece of work

### Wallet and credit line — `2026-09-04-wallet-sync.md`, `2026-09-07-credit-line.md`

You are building the wallet and credit-line applications yourself, on both
ERPNext and Medusa, and these will be linked to them afterwards. So the
questions are no longer about what to build — they are about the seam.

**Q1. What will the ERPNext side be: a DocType in your new app, or several?**
The connector needs a name to map, and one transaction-shaped DocType is
far easier to sync than a balance plus a ledger.
(a) One transaction DocType; balance is derived
(b) A balance DocType and a transaction DocType
(c) ★ Tell me when it exists and I will read it rather than guess now

> **Answer:**

**Q2. Which side owns the balance once both exist?**
(a) ERPNext computes it, Medusa caches a read-only copy
(b) ★ ERPNext computes it and Medusa never stores one, only transactions
(c) Both, reconciled — this needs a tie-break rule and it has to be yours

> **Answer:**

**Q3. `Customer.wallet_balance_paise` still exists on the ERPNext side with
nothing writing to it, left over from `risitex_erp`. Keep it as the landing
place, or drop it now that you are building a proper app?**
(a) Keep
(b) ★ Drop it — a field the new app does not own is a trap

> **Answer:**

**Q3b. Should the connector treat wallet and credit line as one entity or
two?**
Medusa already has `credit_line`, `credit_terms` and `order_credit_line`
tables alongside `wallet`, which suggests two.
(a) ★ Two — a wallet is money held, a credit line is money owed, and they
reconcile differently
(b) One ledger with a sign

> **Answer:**

### Percentage Pricing Rules and MRP — `2026-09-04-pricing-rules-and-mrp.md`

**Q4. Where should an ERPNext Pricing Rule expressed as a percentage or a
discount land in Medusa?**
Flat-rate tier prices already sync. Percentages have no direct counterpart.
(a) A Medusa price-list rule, computed to a flat amount at sync time
(b) A Medusa promotion
(c) ★ Variant metadata only, with the storefront applying it — the least
wrong until somebody needs it enforced at checkout
(d) Not at all

> **Answer:**

**Q5. Where should MRP land?**
(a) Variant metadata, for display only
(b) ★ A second Medusa price list named `MRP`, so the storefront can strike
it through with real price machinery
(c) Not at all

> **Answer:**

**Q6. There is no ERPNext field for MRP on the demo site and no Pricing
Rules seeded. Will you seed a site, or should I add an `Item.mrp` custom
field in a patch and seed a couple myself?**
(a) You will seed one
(b) ★ I add the field and seed a couple of rules to test against

> **Answer:**

### Variant options, barcode, brand — `2026-09-04-variant-attributes.md`

**Q7. Do ERPNext Item variant templates become Medusa product options, or
does each ERPNext variant become its own Medusa product?**
(a) ★ Options — one Medusa product with a variant per combination, which is
what a storefront expects
(b) Separate products — simpler, and loses the size picker

> **Answer:**

**Q8. Which Item Attributes are the option axes?**
Colour and size are the usual two, but ERPNext allows any.
(a) ★ Read from each Item's own attribute table — no configuration, works
for any client
(b) A configured list in Settings

> **Answer:**

**Q9. Barcode, UOM and brand — where does each land?**
`Item Barcode` is a child table, so there can be several.
(a) ★ Barcode: the row whose type is `EAN`, else the first, into
`variant.barcode`. UOM: the stock UOM into variant metadata, conversions
not synced. Brand: `Item.brand` into variant metadata.
(b) Something else — say which for each

> **Answer:**

### Prices coming back from Medusa — `2026-09-04-inbound-price-path.md`

**Q10. When Medusa sends a price back, which ERPNext Price List does it
belong to?**
The direction is already stored per list per store; the target is missing.
(a) ★ The list mapped as Base Price for that store, refusing if that list
is not marked `Two-way`
(b) A dedicated list per store, created on first use
(c) Carried on the payload, and ERPNext trusts it

> **Answer:**

**Q11. A Medusa customer-tier price has no quantity bracket, and ERPNext
tiers are defined by one (`packing_unit`). What happens on the way back?**
(a) ★ Refuse tier prices inbound; base prices only. Tiers stay ERPNext's
(b) Accept at bracket 1, overwriting the single-unit price
(c) Carry the bracket on the payload

> **Answer:**

**Q12. Rounding.** ERPNext sends 799.00, Medusa stores 79900 minor units
and sends back 799.0. The echo guard compares payloads.
(a) ★ Compare to two decimal places, treating equal-within-tolerance as an
echo
(b) Exact comparison, accepting the occasional redundant round trip

> **Answer:**

**Q13. May an inbound price create an Item Price that does not exist, or
only update one that does?**
(a) ★ Update only — a price appearing in ERPNext from a storefront is a
surprise nobody asked for
(b) Create and update

> **Answer:**

### Product images — `2026-09-04-product-images.md`

**Q14. Who moves the bytes?**
(a) ★ Medusa pulls a URL ERPNext gives it — fewer moving parts, and Medusa
already owns file storage
(b) ERPNext pushes to a Medusa upload endpoint

> **Answer:**

**Q15. ERPNext private files are not fetchable without a session. What
should happen to them?**
(a) ★ Skip them and say so in the log — somebody who wants an image on the
storefront can make it public
(b) Give Medusa a scoped read token
(c) ERPNext copies them to public on sync

> **Answer:**

**Q16. What counts as a change worth re-sending?**
(a) ★ A content hash computed on the ERPNext side, carried in the payload
(b) File size and modified timestamp
(c) Always re-send — simple and expensive

> **Answer:**

**Q17. Which image is the primary, and do the others sync at all?**
(a) ★ `Item.image` is the primary; other attachments do not sync until
somebody asks
(b) `Item.image` first, then every image attachment in order

> **Answer:**

### An order's payment status — `2026-09-05-order-payment-status.md`

**Q18. Which document is the payment authority when a Sales Order and a
Sales Invoice disagree?**
This is the only one on the list that is live and mildly wrong today rather
than absent: an order paid the ordinary way through an invoice reads
`unpaid`, because the figure comes from the order's `advance_paid` alone.
(a) The invoice when there is one — matches how accounts think; the status
flips to `unpaid` the moment an invoice is raised and before money lands
(b) ★ The sum of Payment Entry receipts — truest to the money, already
computed as `erp_payments_total`, ignores credit terms entirely
(c) Report both separately and let the storefront choose

> **Answer:**

### The mapping editor — `2026-09-07-mapping-studio-parity.md`, `2026-09-07-medusa-field-discovery.md`

**Q27. How close to n8n do you want the mapper?**
Neither side is a two-panel drag-and-drop today: Medusa has a real editor
with a field-pair grid, ERPNext has a DocType form with studio buttons.
(a) ★ Show each row's currently-resolved value first — small, and removes
most of the typing-blind problem — then decide whether the two-panel
layout is still worth it
(b) Build the two-panel drag mapper properly, both sides
(c) Leave it; the grid plus Sample is enough

> **Answer:**

**Q28. ERPNext's mapper is a DocType form and cannot host a two-panel
layout. Replace it with a Frappe Page?**
It means giving up the free things a DocType form provides: permissions,
version history, list views, filters.
(a) ★ Only if Q27 lands on (b) — otherwise the form plus buttons is the
better trade
(b) Yes regardless; the editor deserves a purpose-built page
(c) No

> **Answer:**

**Q29. Where should Medusa's field list come from?**
It is a hand-curated `paths` array per entity today, so a field nobody
listed is invisible in the picker, and a Medusa upgrade that adds a column
adds nothing here. ERPNext's equivalent is read live from `get_meta`.
(a) Keep curating, and add a test that reports fields present in real data
but missing from the list
(b) ★ Derive from the model definition at runtime, falling back to walking
a real record when a model has no rows, keeping the curated list only for
labels and suggested transforms
(c) Derive from a real record only

> **Answer:**

**Q30. Nobody can currently answer "who changed this mapping field, and to
what" on the Medusa side.** ERPNext has Frappe's document version history;
Medusa keeps only `version` and `updated_by_user_id`. Worth an edit log?
(a) ★ Yes — a small revisions table on `erpnext_mapping`, written on save
(b) No — the mapping syncs to ERPNext, which does keep history

> **Answer:**

### The field dictionary — `2026-09-07-field-equivalence-dictionary.md`

Most of this exists as code: `canonical-mappings.ts` for whole (entity ×
DocType) pairs, and `autofill.ts` with 28 synonym groups and a
seven-rung confidence ladder. What is missing is that it cannot be edited,
does not learn, and does not reach ERPNext at all.

**Q31. Where should the dictionary live?**
(a) ★ A DocType on ERPNext and a model on Medusa, synced by the mechanism
mappings already use — uid, version, higher wins, ERPNext takes a tie
(b) ERPNext owns it; Medusa reads it over the wire
(c) Stay as code, and just widen the constants

> **Answer:**

**Q32. What scope should an entry have?**
(a) Global only — `email` ↔ `email_id` everywhere
(b) ★ Global, plus optional narrowing to an (entity, DocType) pair, plus
optional narrowing to one site. Most rows are global; the ones that matter
to a client are not
(c) Per (entity, DocType) only

> **Answer:**

**Q33. When an operator corrects a suggested pairing, should the dictionary
learn it?**
That correction is the most reliable row anyone could write, and writing it
back automatically is also how a dictionary fills with one client's habits.
(a) ★ Record it as `suggested` from `operator`, and let somebody promote it
to `confirmed` — learning that has to be agreed to
(b) Write it straight in as confirmed
(c) Never; the dictionary is edited by hand only

> **Answer:**

**Q34. Should selecting an entity and a DocType auto-fill the grid, or
offer the rows?**
Auto-filling is what was asked for and is faster. Offering makes the
operator look at a `weak` guess before it becomes a mapping.
(a) ★ Auto-fill everything at `synonym` or better; list the `weak` and
`none` rows separately for the operator to accept
(b) Auto-fill everything the matcher produces
(c) Offer everything, accept nothing automatically

> **Answer:**

---

## B. Decisions about what is already built

**Q19. Turn off the legacy Single secret on this installation now?**
It works, and it is the one place where "which store is this?" is answered
by something other than the signature. Every connected store here has
delivered with its own secret.
(a) ★ Turn it off on this site; leave the default on for a client mid-upgrade
(b) Leave it on everywhere

> **Answer:**

**Q20. A Medusa hard reset switches off *every* mapping, including ones
ERPNext never touched. Scope it to the connection being reset?**
Harmless with one connection, wrong the moment a second store connects.
(a) ★ Scope it by `site_id` before a second connection exists
(b) Leave it — one Medusa talks to one ERPNext

> **Answer:**

**Q21. Both sides now refuse to be switched on remotely, so they can
disagree about `enabled` at the same version with nothing reconciling it.
Do you want a reconcile action?**
(a) ★ Yes — a "differences with the other side" view and a per-mapping
"take theirs / keep mine"
(b) No — the Needs Attention flag is enough

> **Answer:**

**Q22. The shipped default set is three mappings: Customers, Catalogue,
Orders. Enough to start a client on?**
(a) ★ Yes — everything past those three is genuinely per-project
(b) Add more — say which

> **Answer:**

---

## C. Getting it in front of a client

**Q23. The branch is `feat/phase0-1-foundation` on both repos and now holds
six phases plus follow-ups. Rename before opening pull requests?**
A rename means force-pushing the new name and deleting the old one on both
remotes; the history is unchanged.
(a) ★ Rename to `feat/configurable-bidirectional-sync`, then one PR per
repo against `master` / `main`
(b) Open the PRs under the current name
(c) Neither yet

> **Answer:**

**Q24. Which client goes first — Polemarch, Splendax, or a fresh site?**
★ A fresh site: everything built since Phase 6 is written for the second
client and none of it has met one.

> **Answer:**

**Q25. Is there a deadline or a client date to build towards?**
Nothing in the brief mentioned one, and it changes what is worth doing next.

> **Answer:**

**Q26. Development may continue on another machine. Is that machine going
to have a Frappe bench and a Medusa sandbox, or only the two repos?**
`docs/RESUMING-ON-ANOTHER-MACHINE.md` covers both cases, but the answer
decides what is worth preparing.
(a) Both stacks, same as here
(b) ★ Repos only at first — unit tests and typecheck run without either
stack; the bench and sandbox come later
(c) Something else

> **Answer:**
