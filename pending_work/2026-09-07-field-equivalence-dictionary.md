# A field dictionary that both sides share

**Raised:** 2026-09-07 — "a dictionary of equivalent fields that also syncs,
so when users select something it auto-maps the required fields"
**Belongs to:** with the mapper work; it is what makes a picker useful
**Side:** both, and the dictionary itself would sync like a mapping does.

## Most of this already exists — as code

Two constants do the job today and neither is editable without a release.

**`canonical-mappings.ts`** — hand-written (Medusa entity × Frappe DocType)
pairs with their whole field list. One entry today, `customer` ↔ `Customer`;
the docstring says six were intended.

**`autofill.ts`** — the matcher, 653 lines and pure. Given a DocType's live
field meta and an entity's declared paths it emits one ready-to-edit pair
per field that matters, with a confidence label:

| Rung | Means |
|---|---|
| `canonical` | a `canonical-mappings.ts` entry already pairs these two |
| `composite` | the Frappe field is a whole-name or whole-address style field and the entity has the parts, so it emits a template `{first_name} {last_name}` rather than dropping one |
| `exact` | normalised names identical — `email_id` ↔ `email` once the trailing `id` and a `custom_` prefix are stripped |
| `synonym` | both land in the same `SYNONYM_GROUPS` entry — 28 groups today |
| `strong` | token-set Jaccard ≥ 0.75 |
| `weak` | ≥ 0.40, shown but flagged |
| `none` | no guess, and the row is still emitted when the Frappe field is mandatory, because a blank mandatory field is what the operator needs to see |

So "select an entity and a DocType and get the fields filled in" already
works. What does not work is everything after that.

## What is actually missing

- **It cannot be edited.** A client whose Item calls the HSN code
  `custom_hsn` gets a `weak` guess forever, and the only fix is a plugin
  release. The people who know the right pairing are the ones who cannot
  record it.
- **It does not learn.** An operator fixes the same wrong guess on every
  site and the matcher never hears about it.
- **It does not sync.** It lives in the plugin, so ERPNext has no access to
  it at all — the Frappe side's mapping form gets no suggestions of any
  kind. That is the asymmetry the request is really about.
- **It is only suggestions.** Nothing distinguishes "this pairing is
  correct for every client" from "this was a 0.41 Jaccard guess".

## What a dictionary would be

An entry is small and obvious:

```
medusa_path:     "email"
erpnext_field:   "email_id"
scope:           global | (entity, doctype) | (entity, doctype, site)
confidence:      shipped | confirmed | suggested
direction:       the default direction for this pair
transform:       lowercase
source:          shipped | operator | learned
```

Three things follow from that shape:

1. **It seeds from what exists.** The 28 synonym groups and the canonical
   entries become rows, marked `shipped`. Nothing is lost and nothing has
   to be retyped.
2. **It syncs like a mapping.** Same mechanism, already built and tested:
   a uid per entry, a version, higher wins, ERPNext takes a tie, and every
   change logged as an ordinary message. The Frappe side would then have
   suggestions for the first time.
3. **It learns, if you want it to.** An operator who corrects a pairing has
   just produced the most reliable row in the table. Whether that gets
   written back automatically is Q33 — it is the difference between a
   dictionary and a habit.

## Where it plugs in

- **Medusa:** `buildAutofill` takes the dictionary as a fourth input, above
  `canonical` on the ladder. The matcher stays; the constants move.
- **ERPNext:** a new suggest endpoint the mapping form calls when
  `document_type` and `medusa_entity` are both set — the same shape
  `studio.fields_of` already returns, with a suggested `medusa_path` per
  row.
- **Both:** "auto-map required fields" becomes exactly what the ladder
  already does, except the top rung is now editable and shared.

## Why this is worth doing before the two-panel mapper

A picker with no suggestions is a longer way to type. The dictionary is
what makes selecting an entity and a DocType produce a working mapping
instead of an empty grid, and it is the part that carries a client's
knowledge from one site to the next.

It also depends on `2026-09-07-medusa-field-discovery.md`: a dictionary
whose Medusa half is a curated list can only pair the fields somebody
already listed.

## Questions

See `00-QUESTIONS-ANSWER-THESE-FIRST.md` — **Q31**–**Q34**.
