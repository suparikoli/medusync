# Item ↔ Product: what to map

Raised 14 September 2026. **For review — nothing below is built yet** beyond
the "Mapped now" column.

Names are the labels as they appear on screen, not the database fieldnames,
so this can be read next to the two forms:

- ERPNext: **Item** form at `/app/item/<code>`
- Medusa: **Products → Edit** in the admin at `/app/products`

## Required on each side

| Screen label (ERPNext Item) | Required? | What it is | Mapped now | Screen label (Medusa Product) |
|---|---|---|---|---|
| Item Code | **Yes** — also the record's name | The part number people search by | ✅ | Handle (URL slug) — slugified, see note 1 |
| Item Name | No — falls back to Item Code | The name a customer reads | ✅ | Title |
| Item Group | **Yes** | ERPNext's own category tree | ✅ → Metadata | Category *(not linked yet, note 2)* |
| Default Unit of Measure | **Yes** | Nos, Mtr, Kg … | ✅ → Metadata | — *(Medusa has no UOM)* |
| HSN/SAC | **Yes** when Is Sales Item and GST validation is on | Tax classification code | ✅ → Metadata | — |
| Description | No | Long copy for the product page | ✅ | Description |
| Brand | No | Manufacturer | ✅ | Subtitle *(note 3)* |
| Image | No | Main picture | ❌ | Thumbnail / Media |
| Standard Selling Rate | No | List price | ❌ | Variant → Price *(note 4)* |
| Weight Per Unit | No | For shipping rates | ❌ | Variant → Weight |
| Disabled | No | Hides the item | ❌ | Status (Published / Draft) |
| Is Sales Item | No | Whether it can be sold at all | ❌ | — *(use as a filter, note 5)* |
| — | — | Stock on hand | ❌ | Variant → Inventory quantity *(note 6)* |

## Required on the Medusa side that ERPNext has no equivalent for

| Screen label (Medusa) | Required? | What it is | How it is filled today |
|---|---|---|---|
| Title | **Yes** | Product name | From Item Name |
| Variant → Title | **Yes** | Name of the buyable version | Plugin writes "Default" |
| Variant → SKU | **Yes** | Stock code | Plugin writes the exact Item Code |
| Option → Title and Values | **Yes**, if variants exist | e.g. Size / Small, Large | Plugin writes one "Default" option |
| Status | No, but must be **Published** to sell | Draft hides it from the shop | Plugin forces Published |
| Shipping Profile | No, but needed to ship | Which rates apply | Not set — **decide this** |

## Notes / decisions needed

1. **Handle** — Medusa needs a URL-safe slug and an Item Code is not one
   (`ELE-CAB-ARM-COPPER-2.50 SQMM-3 CORE`). The plugin derives
   `ele-cab-arm-copper-2-50-sqmm-3-core` and keeps the exact code as the
   variant SKU. Nothing to decide unless you want a different slug rule.
2. **Category** — Item Group currently lands in Metadata as text. Mapping it
   to real Medusa Categories means creating the category tree in Medusa and
   keeping it in step. Worth doing, own piece of work.
3. **Brand → Subtitle** is a placeholder. Medusa has no Brand; the usual
   choices are a Product Type, a Collection, or a metadata key the storefront
   reads. Currently 62,334 of 62,961 Items carry a Brand.
4. **Price** — the site's selling price list is `Standard Selling`. The price
   lives on Item Price, not Item, so this needs its own pull. Medusa prices
   are per currency and per variant.
5. **Is Sales Item** — every one of the 62,961 Items has it set, so it does
   not narrow anything here. Kept as a filter for later.
6. **Stock** — needs a source warehouse, deferred by request. SPLENDAX has
   Stores / Finished Goods / Work In Progress / Goods In Transit.
7. **No images exist** — 0 of 62,961 Items have one. Mapping Image is
   pointless until pictures are loaded.

## Not started

- Price sync (Item Price → variant prices)
- Stock sync (Bin → inventory levels)
- Category tree (Item Group → Medusa Categories)
- Shipping profile assignment
- Variants proper: ERPNext Item Attributes → Medusa options/variants, instead
  of the single "Default" variant the plugin writes now
