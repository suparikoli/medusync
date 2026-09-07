# Where Medusa's field list comes from

**Raised:** 2026-09-07
**Belongs to:** before the two-panel mapper, which depends on it
**Side:** Medusa only. The ERPNext half already works the right way.

## The asymmetry

**ERPNext fields are derived live.** `studio.fields_of()` and
`GET /admin/erpnext/doctypes/:name` both read `frappe.get_meta(doctype)`,
so every field a DocType has — core, custom, or added by an app installed
this morning — appears with its label, type, options and a sample value.
Nothing is maintained by hand and nothing goes stale.

**Medusa fields are a curated list.** `src/modules/erpnext/registry.ts`
declares 17 entities, each with a hand-written `paths` array:

```ts
paths: [
    { path: "id",            label: "Medusa id",   type: "id" },
    { path: "display_id",    label: "Display id",  type: "number" },
    { path: "email",         label: "Customer email", type: "string",
      suggested_transform: "lowercase" },
    ...
]
```

That is the entity's public field list, and it is what the picker, the
autofill suggestions and the sample generator all read.

## Why it matters

- **A field nobody listed is invisible.** It can still be typed by hand —
  the mapping engine walks arbitrary dotted paths at run time, so an
  unlisted path works perfectly once written — but nothing offers it, and
  nobody discovers it.
- **It goes stale silently.** Medusa 2.20 adding a column to `order` does
  not add it here. Nothing fails; the field simply never appears.
- **It blocks the two-panel mapper.** A picker needs a field list. The
  ERPNext half of a two-panel view could be built today; the Medusa half
  would render seventeen curated lists of varying completeness.
- **A custom module has no list at all** unless somebody writes one, which
  is exactly the case a client project hits first.

## What "the DocType equivalent" is here

Worth stating plainly, because the two systems do not line up:

| ERPNext | Medusa |
|---|---|
| DocType | a **module model** — `Order`, `Product`, `Customer` |
| `frappe.get_meta(dt).fields` | the model's DML definition in `models/*.ts` |
| the DocType list | this plugin's `registry.ts`, a **subset** with adapters |
| a Link field | a module link, resolved through `query.graph` |
| a child table | a related model, fetched by expanding the graph |

The registry is not Medusa's list of models — it is our list of the ones
this connector knows how to fetch, upsert and disable. A model can exist in
Medusa and not be in the registry, and then it cannot be mapped at all.

## The three ways to fix it

1. **Keep curating, and make staleness visible.** A test that walks each
   entity's real record and reports paths present in the data but absent
   from `paths`. Cheap; still a hand-maintained list.
2. **Derive from the model definition.** Medusa's DML models are
   introspectable at runtime — the module's metadata knows its own columns.
   This gives every field of every registered model with no maintenance,
   and gives nothing for related models unless the graph is walked.
3. **Derive from a real record.** Fetch one, walk it, and offer every
   dotted path found. Requires a record to exist, describes the data rather
   than the schema, and would have found the `sales_channel.name` path that
   had to be added by hand in Phase 3.

★ 2 for the shape and 3 as a fallback when a model has no rows, with the
curated `paths` kept only for the labels and suggested transforms — those
are editorial and worth keeping.

## Questions

Answered and built — see **Built** below.

---

## Built — 2026-09-06

It was answered (b), and the Medusa half is done and verified against a
running 2.19 store.

- `src/modules/erpnext/discovery.ts` — pure. `describeModel` walks a
  module's mikro-orm metadata; `describeRecord` is the fallback walk;
  `mergeFieldSources` puts the curated labels and transforms on top.
  19 unit tests.
- `src/modules/erpnext/discovery-runtime.ts` — the impure half.
  `svc.baseRepository_.manager_.getMetadata().getAll()` is the access path,
  which works identically for core modules and a client's own. It fails
  soft: if a future Medusa moves those internals, discovery turns off and
  the curated list still works, with `fields_source: "curated"` and a
  `discovery_error` saying so.
- `GET /admin/erpnext/medusa-entities/:entity/fields` — the mirror of the
  Frappe side's `GET /admin/erpnext/doctypes/:name`.

Result across all 17 registry entities: **126 curated paths → 417 offered**,
every one deriving from `model`, none from the fallback.

### Two things the live check caught that the unit tests could not

Both were wrong assumptions in a fixture I had written from memory, and both
looked completely fine until the endpoint was called against a real store.
The fixture is now a verbatim dump, and each has a regression test.

1. **mikro-orm 6 renamed `reference` to `kind`.** Reading `reference`
   returned `undefined` for every relation, so each one fell through to the
   scalar branch — and since a relation's `type` holds its *target class
   name*, `addresses` and `groups` were offered as `string` fields.
2. **Medusa's DML emits both halves of a to-one relation as `m:1`.**
   `collection` (the object) and `collection_id` (the key) are both
   relations to `ProductCollection`; only `mapToPk: true` tells them apart.
   Descending the key half produced 18 paths like `collection_id.handle`,
   which resolve against a string and can never return a value. Product
   went from 63 fields to 48 once the key half was emitted as an `id`.

### Known limit, for the mapper step

Model discovery sees stored columns, not computed ones. `order.total` and
`order.subtotal` are BigNumber getters on the enriched object with no column
behind them, so they appear only because `registry.ts` curates them — which
is the reason the curated list is kept rather than replaced. A client's own
computed field would be invisible the same way. `describeRecord` would find
it; whether to union the two sources rather than treat record-walking purely
as a fallback is worth deciding when the two-panel mapper is built and it is
clear how much noise the picker can carry.

## What discovery cannot see: anything across a module link

Asked directly, 2026-09-06: "why am I not seeing price?"

Because price is not on the Product model. Medusa v2 keeps prices in the
**Pricing** module (`PriceSet` / `Price`), attached to `ProductVariant`
through a *module link* — not a mikro-orm relation. Discovery reads one
module's ORM metadata, and a module link is not in it, so the walk stops at
the module boundary. Confirmed against the running store: the product field
list has 48 entries and **no** price, inventory or sales-channel path.

The same boundary hides:

| Wanted | Lives in | Reached by |
|---|---|---|
| price, currency | Pricing module | `query.graph` through a link |
| stock, reserved | Inventory module | same |
| sales channels | Sales Channel module | same |

So the honest answer to "will new fields show up automatically?" is
**yes within a module, no across one**:

- A column added to a model in any *registered* module — including a
  client's own — appears with no maintenance. That is the whole point of
  deriving from the model, and it works for custom modules identically.
- Anything behind a module link appears only if the entity's `fetchById`
  adapter loads it and a curated path names it. `variants.0.sku` is in the
  list for exactly that reason, and it is curated, not derived.

### What it would take

Medusa's `query.graph` can traverse links, and the joiner config on each
module service names what it links to. Discovery could read those and offer
one more level — `variants.0.prices.0.amount` and so on. Two things make it
more than an afternoon: the path shape has to address a row inside a
collection (the flat mapper deliberately refuses `addresses.city` for this
reason), and every linked path costs a graph expansion on every push, so it
cannot be offered as freely as a column. Worth doing, worth doing
deliberately, and it belongs with the two-panel mapper rather than before it.

