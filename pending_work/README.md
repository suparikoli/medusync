# pending_work

Deferred requirements for the Frappe/ERPNext side of the ERPNext ↔ Medusa connector.
The Medusa plugin keeps its own `pending_work/` in `medusa-plugin-erpnext`; items that
touch both systems appear in both folders with the same file name, and the two
copies are kept in step.

## Start here

**`00-QUESTIONS-ANSWER-THESE-FIRST.md`** is not a topic. Every topic in this
folder is blocked on a decision in it, which is why it sorts to the top.

The working order is:

1. **Open the questions file first.** Every topic file cites the questions it
   is waiting on by number.
2. **If the question has no answer, stop and ask for one** — send the link to
   the questions file — rather than implementing against an assumption. A
   guessed decision here produces work that gets thrown away, which is the
   whole reason these are written down instead of decided.
3. **When the work is done, delete the question and its answer** from the
   questions file. The decision survives in the commit that implemented it and
   in the code. A question that has been answered but not yet built stays.

## Rules

- One file per topic: `YYYY-MM-DD-<topic>.md`.
- Each file states scope, what already exists, dependencies, the phase it
  belongs to, and the questions it is waiting on.
- This folder is tracked in git on purpose. Never add it to `.gitignore`.
- When an item ships, delete its file in the same PR (the PR description links
  the file's last revision).

## What is in here

| File | Waiting on |
|---|---|
| `2026-09-04-wallet-sync.md` | the wallet applications being built separately |
| `2026-09-07-credit-line.md` | the credit-line applications being built separately |
| `2026-09-04-pricing-rules-and-mrp.md` | decided — MRP is removed entirely |
| `2026-09-04-variant-attributes.md` | decided — options, attributes, barcode/UOM/brand |
| `2026-09-04-inbound-price-path.md` | Q1–Q3 (target price list decided) |
| `2026-09-04-product-images.md` | Q4–Q7 |
| `2026-09-05-order-payment-status.md` | Q8 |
| `2026-09-07-mapping-studio-parity.md` | Q9 — the rest is built |
| `2026-09-07-medusa-field-discovery.md` | decided and built |
| `2026-09-07-field-equivalence-dictionary.md` | decided — waiting to be built |
| `2026-09-06-guided-presets-target-readonly-fields.md` | Q10–Q11 |
| `2026-09-06-payload-key-convention-mismatch.md` | decided and built — Q21 is the follow-on |
| `2026-09-07-one-sync-per-pair.md` | decided and built — answers Q15 |
| `2026-09-07-state-of-play.md` | where everything stands; read it first |

Wallet and credit line are a different kind of pending: they are not waiting
on a decision so much as on two applications that do not exist yet. Their
files record what the connector will need from those applications, so the
seam can be got right while the schema is still soft.
