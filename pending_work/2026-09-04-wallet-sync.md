# Wallet / store-credit sync

**Status:** waiting on an application to exist on both sides
**Belongs to:** whenever the wallet apps are built
**Side:** both. The other repo keeps the same file.

## What is happening

You are building the wallet applications yourself — one for ERPNext, one
for Medusa — and these will be linked to the connector afterwards. That
supersedes the earlier plan, which was for the connector to define a
generic `wallet_transaction` contract and have each project implement it.

Nothing here should be built before those applications exist. A contract
written against an imagined schema is a contract that gets rewritten, and
the earlier attempt at one is exactly why the demo module was removed.

## Where things stand today

**ERPNext.** No wallet DocType at all. `RISITEX Wallet Settlement` went
with the `risitex_erp` uninstall on 2026-09-04. `Customer.wallet_balance_paise`
survives with nothing writing to it — see Q3.

**Medusa.** A real `cashfree_wallet` module is installed on the sandbox and
holds data: 22 wallets, 11 transactions, 4 settlements. It belongs to the
Polemarch securities domain rather than to generic commerce, so it is a
reasonable thing to test transport against and the wrong thing to model a
contract on.

**The connector.** Cleaned of the debris on 2026-09-05: the registry no
longer offers a `wallet_settlement` entity, the dead mapping is switched
off, and the six `cashfree_wallet` handlers answer "not installed" rather
than throwing on a Medusa that has no such module.

## What the connector will need from your applications

Not requests — the things it cannot work around, worth knowing while the
schema is still soft.

- **A stable id on each side, and a field to record the other's.** The
  connector correlates by id pairs and everything in it already works this
  way. A wallet with no place to keep its counterpart's id can only be
  matched by heuristics.
- **One document per movement, not a balance somebody edits.** Two systems
  both writing a number cannot be ordered; two systems each appending
  transactions can. The balance should be derived.
- **Amounts in minor units with an explicit currency.**
- **An idempotency key per transaction.** The connector retries, and a
  retried credit that credits twice is the worst bug this project could
  ship.
- **A reversal that references what it reverses**, rather than a second
  entry with the opposite sign and no link.
- **Somewhere to carry which side originated a movement**, or loop
  prevention has nothing to hold on to.

## What is not decided

See `00-QUESTIONS-ANSWER-THESE-FIRST.md`.

- **Q1** — the shape of the ERPNext side, once it exists.
- **Q2** — which side owns the balance.
- **Q3** — whether `Customer.wallet_balance_paise` stays or goes.
- **Q3b** — whether wallet and credit line are one entity to the connector
  or two. See `2026-09-07-credit-line.md`.

Q3b is worth answering before either application is finished, because it
decides whether they share a contract.
