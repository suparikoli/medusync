# Credit line sync

**Status:** waiting on an application to exist on both sides
**Belongs to:** whenever the credit-line apps are built
**Side:** both. The Frappe app keeps the same file.

## What is happening

You are building credit-line applications yourself — one for ERPNext, one
for Medusa — and these will be linked to the connector afterwards. Nothing
here should be built before they exist: a contract written against an
imagined schema is a contract that gets rewritten.

This file exists so the seam is thought about before the applications are
finished, not after.

## What Medusa already has

The sandbox carries three tables from earlier work, which may or may not be
what the new application uses:

| Table | |
|---|---|
| `credit_line` | the facility itself |
| `credit_terms` | days, limit, and the rules attached to it |
| `order_credit_line` | an order drawing on one |

There is no ERPNext counterpart. `Customer.credit_limit` and the payment
terms on a Sales Order are the nearest standard things, and neither is a
ledger.

## What the connector will need from your applications

Not requests — the things it cannot work around, so they are worth knowing
while the schema is still soft.

- **A stable id on each side, and a place to record the other's.** The
  connector correlates by id pairs, and everything else in it works this way
  (`medusa_customer_id`, `medusa_order_id`). A credit line with no id field
  for its counterpart can only be matched by heuristics.
- **One document per *event*, not per state.** A limit that is a number on a
  customer syncs badly: two systems both editing a number cannot be ordered.
  A drawdown, a repayment and a limit change that are each a record can.
- **Amounts in minor units with an explicit currency**, matching the rest of
  the wire contract.
- **An idempotency key per transaction.** The connector retries, and a
  retried drawdown that draws twice is the worst bug this project could
  ship.
- **Something that says which side originated a movement**, or the loop
  prevention has nothing to hold on to. The existing echo mechanism can tag
  it, but only if there is a field to carry the tag.

## What is not decided

See `00-QUESTIONS-ANSWER-THESE-FIRST.md`.

- **Q1** — the shape of the ERPNext side, once it exists.
- **Q2** — which side owns the balance.
- **Q3b** — whether wallet and credit line are one entity to the connector
  or two. Medusa's existing tables suggest two, and they reconcile
  differently: a wallet is money held, a credit line is money owed.

Q3b is the one that decides how much of this is shared with the wallet
work, so it is worth answering even before the applications are finished.
