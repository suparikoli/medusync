# Prices flowing Medusa → ERPNext

**Deferred from:** Phase 3 (entity breadth)
**Belongs to:** a later phase, once the mapping studio (Phase 4) can dry-run it
**Side:** both systems. This file is the ERPNext half; the plugin repo's
`pending_work/` holds the Medusa half.

## What exists

Direction is stored per price list, per store, and is already respected — in one
direction. `Medusync Site → Price Lists` holds the rules and
`medusync/price_lists.py` reads them:

| Direction | Today |
|---|---|
| `To Medusa` | ERPNext sends. Works. |
| `Two-way` | ERPNext sends. The other half is this file. |
| `From Medusa` | ERPNext sends nothing. Nothing arrives either. |
| `Don't Sync` | Nothing moves. Correct and complete. |

`From Medusa` therefore means "ERPNext keeps out of it", not "Medusa drives it".
The README says so plainly; this file is the work that would make the label true.

## The seam that is already cut

`price_lists.accepts_inbound(price_list, site_id)` exists, is tested by nothing
that matters yet, and answers correctly. Nothing calls it. An inbound price
handler would ask it first and refuse anything it declines.

## What is missing here

- **A handler that writes an `Item Price`.** Key: (item, price list, currency,
  packing unit). Create or update; never touch a list the rules do not permit.
- **Refuse as a skip, not a failure.** The same rule the catalogue guard follows.
  A store that keeps sending a price ERPNext will not take must get a 200 and
  stop, or it builds a retry queue that can never drain. This project has
  already had to unpick one of those (`customer.synced`, 77 queued rows).
- **A tier price needs its bracket back.** A Medusa customer-tier price maps to
  an Item Price on the list whose rule carries that tier code, at the
  `packing_unit` matching its quantity bracket. Medusa has no direct equivalent
  of the bracket, so the return trip has to reconstruct it or refuse.
- **Loop prevention across a rounding boundary.** ERPNext pushes 799.00, Medusa
  stores 79900 minor units, sends back 799.0 — the echo breadcrumb should catch
  it, but a tax-inclusive or currency-converted return may not compare equal to
  what was sent. Decide before writing code whether the guard is the breadcrumb,
  a tolerance comparison, or both.

## Why it was left

Phase 3's job was to stop one global selling-price-list setting standing in for
many lists with different meanings at different stores. The direction field is
the configuration that makes an inbound path *possible*; the path itself wants
the Phase 4 test studio first, because a wrong price landing in ERPNext is
expensive in a way a wrong stock level is not.

## Questions this is waiting on

See `00-QUESTIONS-ANSWER-THESE-FIRST.md`.

- **Q10** — which ERPNext Price List a price coming back from Medusa belongs
  to. There is no obvious answer: Medusa has no concept of the list it came
  from once the price is stored.
- **Q11** — what happens to a Medusa tier price, which has no quantity
  bracket, when ERPNext tiers are defined by one.
- **Q12** — how much rounding tolerance the echo guard allows, since a
  price crossing to minor units and back may not compare equal.
- **Q13** — whether an inbound price may create an Item Price or only
  update one that exists.

All four have to be answered together: they are one design. A wrong price
landing in ERPNext is expensive in a way a wrong stock level is not, which
is why none of it was guessed at.
