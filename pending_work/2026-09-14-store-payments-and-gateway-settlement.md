# Store payments and gateway settlement

Raised 14 September 2026. **Not started.** Payment recording is switched
off on the store's Medusync Site (`record_payments = 0`, `mode_of_payment`
cleared) so orders and invoices can sync without it. Turning it back on
before this is designed would post wrong numbers to real books.

## What the user asked for

> We'll be using payment gateway and cash, NEFT IMPS etc on site. This has
> to be generic for now. We'll be using the mapping tool for these things
> I'm assuming. The medusync has to create fields for this. And when
> payment is transferred by the payment gateway to the bank they deduct
> their charges, GST and then send. So, we'll have to handle that as well.

## Why the current code is not enough

`medusync/invoicing.py` books one Payment Entry per captured store payment
(`record_payments`), and takes the Mode of Payment from a single field on
Medusync Site. Three things break on contact with a real gateway:

1. **One mode per store.** A storefront collects through several methods at
   once — gateway card, gateway UPI, NEFT/IMPS, cash on delivery. They post
   to different accounts, so one field cannot describe a store.
2. **Gross is not what arrives.** The gateway deducts its commission and
   GST on that commission, then transfers the net. A Payment Entry for the
   gross amount never reconciles against the bank; one for the net leaves
   the invoice underpaid. The charge and its GST are an expense and an
   input-tax claim, not a discount.
3. **Capture and settlement are different events, days apart.** Money sits
   with the gateway before it reaches the bank. Posting straight to a bank
   account on capture misstates the bank balance until settlement.

## The constraint that makes this hard

The product rule is **no Custom Fields on ERPNext doctypes, ever** — see
the medusync README and the mapping validator. The user's note that
"medusync has to create fields for this" runs against it, so the design
has to resolve that explicitly rather than quietly adding fields. Options
worth weighing:

- Carry the settlement facts on **Medusync Link** `details` (already a JSON
  column, already per-record, already the answer for `medusa_*` keys).
- A **child table on Medusync Site** mapping store payment provider →
  Mode of Payment → account, which is configuration rather than a field on
  an ERPNext business doctype.
- A **Medusync-owned doctype** for a settlement batch, holding gross,
  commission, GST and net, which posts a Journal Entry — no ERPNext
  doctype is modified.

Whichever is chosen, it must hold for any gateway: nothing Razorpay- or
Cashfree-specific in either codebase, and no deployment values in code.

## Suggested accounting shape (to confirm with the user)

The usual ERPNext treatment is a clearing account per gateway:

    capture     Debit  Payment Gateway Clearing   gross
                Credit Debtors                    gross      (Payment Entry)

    settlement  Debit  Bank                       net
                Debit  Commission expense         charge
                Debit  Input GST                  gst on charge
                Credit Payment Gateway Clearing   gross      (Journal Entry)

This keeps the invoice fully paid, the bank matching the statement, and the
commission and its GST where the accountant expects them.

## Prompt to start the work

A ready-to-paste session prompt lives outside both repos, with this
deployment's paths, ports and site names in it — those are one machine's
details and have no place in an app other stores install. On this machine:
`frappe16/medusync-runbooks/2026-09-14-gateway-settlement.prompt.md`.

## Related

- `medusync/invoicing.py` — `record_payments`, `_payment_entry`
- `medusync/payment_modes.py` — reports missing modes and creates them on
  request; `missing()` also flags modes with no default account for a
  company. That check is what catches a mode pointing at a Cash account —
  wrong for an online receipt, and only discovered as a misposted Payment
  Entry otherwise.
- Plugin: `src/modules/erpnext/registry.ts` fetches captured payments and
  `pushViaMapping` sends them as `medusa_payments`.
