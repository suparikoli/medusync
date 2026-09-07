# Variant options, barcode, brand — deferred 2026-09-04

**Requirement.** Variants sync ERPNext → Medusa with SKU, name, attributes,
options, barcode, UOM, weight; images ERPNext → Medusa.

**State.** Flat Item attributes (`item_group`, `hsn_code`, `fabric`, `gsm`)
sync through the mapping rows. ERPNext Item variant templates (Item
Attribute / Item Variant Attribute) ↔ Medusa product options, variant
barcode (`Item Barcode` child table), UOM conversions and brand are not
implemented; the demo site has no templated Items to test with.

**Dependencies.** Phase 3 product/variant entity work; seeded Item templates
with attributes on the demo site.

## Questions this is waiting on

See `00-QUESTIONS-ANSWER-THESE-FIRST.md`.

- **Q1** — whether ERPNext variant templates become Medusa product options
  or separate products. This is the shape of the whole feature: options give
  a storefront its size picker, separate products do not.
- **Q2** — which Item Attributes are the option axes, or whether to read
  them from each Item.
- **Q3** — where barcode, UOM and brand each land.

Q1 first. Q2 and Q3 are details inside whichever answer it gets.

---

## Decided

Moved here from `00-QUESTIONS-ANSWER-THESE-FIRST.md`, which carries only questions
still waiting on an answer. The decision stays with the work it governs.


### Variant templates become options, not separate products
> **Answer:** a

### Which Item Attributes are the option axes
> **Answer:** a

### Barcode, UOM and brand
> **Answer:** a

### Prices coming back from Medusa — `2026-09-04-inbound-price-path.md`
