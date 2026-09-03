# Percentage / discount Pricing Rules and MRP — deferred 2026-09-04

**Requirement.** Price lists sync bidirectionally with per-list direction.
Rate-based tier prices and quantity ladders already sync from
`handlers/risitex/pricing.py` (`variant.price.set`, `variant.tier_price.set`
with `Item Price.packing_unit` → `min_quantity`). Still open:
- ERPNext **Pricing Rules** expressed as percentages or discounts are not
  pushed at all.
- **MRP** has no Item field on the demo site and no Medusa target.

**Dependencies.** Phase 3 price-list work (per-list direction + site
selection, warehouse / price-list mapping tables); Pricing Rules seeded on a
demo site; a per-project decision on the Medusa target.
