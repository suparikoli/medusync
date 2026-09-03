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
