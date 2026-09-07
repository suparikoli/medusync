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

**Keeping it clean.** This file holds only questions still waiting on an
answer. The moment one is answered it moves to the `pending_work` topic
doc it governs, under a `## Decided` heading — so the decision sits with
the work rather than in a growing list nobody reads, and what is left here
is exactly what is still owed. New questions are welcome and belong here.

Where I have a view it is marked ★ — that is what I would build if told to
use my own judgement, not a default that happens without you.

The same file is in `medusync/pending_work/` and the two are kept
identical. Answering in either answers both.

---

## A. Questions that block a specific piece of work

### Prices coming back from Medusa — `2026-09-04-inbound-price-path.md`

**Q1. A Medusa customer-tier price has no quantity bracket, and ERPNext
tiers are defined by one (`packing_unit`). What happens on the way back?**
(a) ★ Refuse tier prices inbound; base prices only. Tiers stay ERPNext's
(b) Accept at bracket 1, overwriting the single-unit price
(c) Carry the bracket on the payload

> **Answer:**

**Q2. Rounding.** ERPNext sends 799.00, Medusa stores 79900 minor units
and sends back 799.0. The echo guard compares payloads.
(a) ★ Compare to two decimal places, treating equal-within-tolerance as an
echo
(b) Exact comparison, accepting the occasional redundant round trip

> **Answer:**

**Q3. May an inbound price create an Item Price that does not exist, or
only update one that does?**
(a) ★ Update only — a price appearing in ERPNext from a storefront is a
surprise nobody asked for
(b) Create and update

> **Answer:**

### Product images — `2026-09-04-product-images.md`

**Q4. Who moves the bytes?**
(a) ★ Medusa pulls a URL ERPNext gives it — fewer moving parts, and Medusa
already owns file storage
(b) ERPNext pushes to a Medusa upload endpoint

> **Answer:**

**Q5. ERPNext private files are not fetchable without a session. What
should happen to them?**
(a) ★ Skip them and say so in the log — somebody who wants an image on the
storefront can make it public
(b) Give Medusa a scoped read token
(c) ERPNext copies them to public on sync

> **Answer:**

**Q6. What counts as a change worth re-sending?**
(a) ★ A content hash computed on the ERPNext side, carried in the payload
(b) File size and modified timestamp
(c) Always re-send — simple and expensive

> **Answer:**

**Q7. Which image is the primary, and do the others sync at all?**
(a) ★ `Item.image` is the primary; other attachments do not sync until
somebody asks
(b) `Item.image` first, then every image attachment in order

> **Answer:**

### An order's payment status — `2026-09-05-order-payment-status.md`

**Q8. Which document is the payment authority when a Sales Order and a
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

**Q9. Nobody can currently answer "who changed this mapping field, and to
what" on the Medusa side.** ERPNext has Frappe's document version history;
Medusa keeps only `version` and `updated_by_user_id`. Worth an edit log?
(a) ★ Yes — a small revisions table on `erpnext_mapping`, written on save
(b) No — the mapping syncs to ERPNext, which does keep history

> **Answer:**

### How the two systems pair — `2026-09-06-guided-presets-target-readonly-fields.md`

**Q10. "This should work for multiple Medusa & ERPNext sites" — one half of
that is not built. Which way should it go?**
ERPNext already talks to many stores: one `Medusync Site` row per connected
Medusa, and inbound requests are attributed by signature. Medusa does not
have the mirror of that. `src/modules/erpnext/models/setting.ts` is a
deliberate singleton — one row pinned by `singleton_key = "default"` behind a
unique index, one `erpnext_url`, one `site_id` — so a single Medusa store can
be paired with exactly one ERPNext. Every discovery cache, dictionary entry
and mapping the next three pieces of work introduce has to be keyed by
*something*, and that key is only meaningful if this is settled first.
(a) ★ Many Medusa stores per ERPNext is the supported topology, and one
Medusa answers to one ERPNext. The new work keys by connection anyway, so
nothing has to be rebuilt if (b) happens later
(b) Medusa should also hold many ERPNext connections — replace the singleton
with a real table, add a connection picker to every admin screen, and route
inbound by which secret verified. A sizeable change with its own migration,
and it touches the forwarder, the breaker and the settings UI
(c) Something else — say what pairs with what

> **Answer:**

**Q11. A Medusa customer is three ERPNext records. Which one does the
guided sync own?**
ERPNext keeps a customer's email and phone on a linked **Contact**, not on
the Customer: `email_id`, `mobile_no`, `first_name` and `last_name` are
fieldtype Read Only with `fetch_from = customer_primary_contact.*`, and
Frappe refills them from the link. The old preset wrote them anyway and
keyed the whole mapping on `email_id` — it appeared to work only because
`customer_primary_contact` was empty. The preset now syncs the name, a
fixed Customer Type and the link id, and says so. That is honest but
partial, and a flat mapping addresses one DocType, so the rest needs a
decision. Address is the same shape again.
(a) ★ A `customer` handler pack that writes Contact and Address alongside
the Customer. It is what handler packs are for, it gets the ordering right,
and it is not configurable from the mapper
(b) A second mapping, `customer` → `Contact`, keyed on the Medusa id.
Configurable, but the mapper cannot express "only after the Customer
exists", so first sync of a new customer would race
(c) Leave it — name and link id are enough, and whoever needs contact
details adds their own mapping
See `2026-09-06-guided-presets-target-readonly-fields.md`.

> **Answer:**

---

---

## B. Decisions about what is already built

**Q13. Turn off the legacy Single secret on this installation now?**
It works, and it is the one place where "which store is this?" is answered
by something other than the signature. Every connected store here has
delivered with its own secret.
(a) ★ Turn it off on this site; leave the default on for a client mid-upgrade
(b) Leave it on everywhere

> **Answer:**

**Q14. A Medusa hard reset switches off *every* mapping, including ones
ERPNext never touched. Scope it to the connection being reset?**
Harmless with one connection, wrong the moment a second store connects.
(a) ★ Scope it by `site_id` before a second connection exists
(b) Leave it — one Medusa talks to one ERPNext

> **Answer:**

**Q16. The shipped default set is three mappings: Customers, Catalogue,
Orders. Enough to start a client on?**
(a) ★ Yes — everything past those three is genuinely per-project
(b) Add more — say which

> **Answer:**

**Q21. A `mapped` push with no handler pack behind it is refused with a
500. Should it fall through to the generic apply?**
Raised while settling Q12. A `mapped` body is already in ERPNext
fieldnames, so the generic `apply_inbound` could take it with no
translation step; today `_apply_mapped` answers "no configured handler
pack provides a mapped upsert" instead. Only matters for a site that
runs no pack at all.
(a) ★ Yes — the generic mapper is what a site without a pack has
(b) No — a site with no pack has opted out of Medusa-originated writes

> **Answer:**

**Q22. "Link this product to an existing Item" half-succeeds when Medusa
catalogue updates are off.** Seen 2026-09-07 while verifying Q12 live:
the link route stores the item code on the Medusa product and reports
`ok: true`, then sends `product.linked` as a `mapped` push to stamp
`medusa_product_id` on the Item — and ERPNext's catalogue guard refuses
it as `catalogue-protected`, because `allow_medusa_catalogue_updates` is
off. The two sides then disagree about whether they are linked, and
reconciliation keeps reporting the pair.
(a) ★ A link stamp is not a catalogue edit — let `product.linked` through
the guard (it writes only the link id field)
(b) Refuse the whole link on the Medusa side when ERPNext will not take
the stamp, so `ok: false` says why
(c) Leave it — an operator who links must also allow updates

> **Answer:**

---

## C. Getting it in front of a client

**Q18. Which deployment goes first — an existing one, or a fresh site?**
★ A fresh site: everything built since Phase 6 is written for a second
deployment and none of it has met one.

> **Answer:**

**Q19. Is there a deadline or a client date to build towards?**
Nothing in the brief mentioned one, and it changes what is worth doing next.

> **Answer:**

**Q20. Development may continue on another machine. Is that machine going
to have a Frappe bench and a Medusa sandbox, or only the two repos?**
`docs/RESUMING-ON-ANOTHER-MACHINE.md` covers both cases, but the answer
decides what is worth preparing.
(a) Both stacks, same as here
(b) ★ Repos only at first — unit tests and typecheck run without either
stack; the bench and sandbox come later
(c) Something else

> **Answer:**
