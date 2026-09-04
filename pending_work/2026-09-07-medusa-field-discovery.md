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

See `00-QUESTIONS-ANSWER-THESE-FIRST.md` — **Q29**.
