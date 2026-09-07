# Percentage / discount Pricing Rules and MRP — deferred 2026-09-04

**Requirement.** Price lists sync bidirectionally with per-list direction.
Rate-based tier prices and quantity ladders already sync from
`handlers/commerce/pricing.py` (`variant.price.set`, `variant.tier_price.set`
with `Item Price.packing_unit` → `min_quantity`). Still open:
- ERPNext **Pricing Rules** expressed as percentages or discounts are not
  pushed at all.
- **MRP** has no Item field on the demo site and no Medusa target.

**Dependencies.** Phase 3 price-list work (per-list direction + site
selection, warehouse / price-list mapping tables); Pricing Rules seeded on a
demo site; a per-project decision on the Medusa target.

## Questions this is waiting on

See `00-QUESTIONS-ANSWER-THESE-FIRST.md`.

- **Q4** — where a percentage or discount Pricing Rule lands in Medusa.
  Metadata, a price-list rule, or a promotion are three different amounts of
  work and only one of them enforces the discount at checkout.
- **Q1** — where MRP lands. Metadata is display-only; a second price list
  lets the storefront strike it through with real price machinery.
- **Q2** — whether you seed a demo site with Pricing Rules, or I add the
  `Item.mrp` field and seed a couple myself.

Q2 is the practical blocker: none of this can be verified against a site
that has no Pricing Rules on it.

---

## Decided

Moved here from `00-QUESTIONS-ANSWER-THESE-FIRST.md`, which now
carries only questions still waiting on an answer. The decision and
the reasoning stay with the work they govern.

### Where a percentage Pricing Rule lands

> **Answer:** Let's use ERPNext default price list. Also, when we modify the product doctype in erpenext. Let's put all the custom fields related to Medusync in a separate tab where we'll have everything. We'll dive deeper into this later

### Where MRP lands — settled: nowhere

> **Answer (2026-09-07):** MRP has been removed completely from the Medusa
> instance. It is not synced, not stored and not mapped, on either side.
>
> This supersedes an earlier answer here that was cut off mid-sentence
> ("I don't want to use MRP I want to use ") and had been open since the
> project started. Nothing needs an `Item.mrp` custom field, no Pricing
> Rules need seeding for it, and any MRP path still referenced elsewhere in
> this folder is dead — treat a mention of it as something to delete rather
> than something to build.

