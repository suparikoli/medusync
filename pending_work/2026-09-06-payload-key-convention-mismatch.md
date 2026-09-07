# Both sides translate the payload, so mapped fields are dropped

**Raised:** 2026-09-06, running a product each way for the first time
**Side:** both, and it is the wire format between them
**Severity:** this silently drops every mapped field whose two names differ

## What happens

A field pair names a Frappe field and a Medusa path — `item_name ↔ title`.
Each side translates when it **sends**, and each side translates again when
it **receives**. The second translation looks for a key that is no longer
there, so the field is dropped. Nothing errors; the value simply never
arrives.

Fields survive only by accident — when the two names happen to be identical
(`description`), when a handler pack owns the event, or on the pull cron
path (below), which is the one place the convention is right.

### ERPNext → Medusa

`outbound.build_payload` sends keys named for the **Medusa** side:

```python
target = row.medusa_path or row.frappe_field
data[target] = source.get(row.frappe_field)
# {'handle': 'erp-probe2', 'title': 'ERP Probe Two', 'description': ...}
```

`applyMapping({direction: "pull"})` then reads `pair.erpnext_field` — it
looks for `item_name`, which is not in that payload. Observed end to end:
ERPNext logged `item.created` as **Success** (Medusa returned 200), Medusa
recorded the event as **failed — "Product title is required"**, and no
product was created.

### Medusa → ERPNext

The mirror image. `applyMapping({direction: "push"})` writes
`payload[pair.erpnext_field]`, so Medusa sends **ERPNext**-named keys.
`api._translate` then looks for `row.medusa_path` in that data:

```python
source = row.medusa_path or row.frappe_field
if source in data:            # 'handle' — not in a payload keyed 'item_code'
```

Verified directly against the Products mapping:

```
_translate({item_code, item_name, item_group, stock_uom})
  -> {'item_group': 'Products', 'stock_uom': 'Nos'}
```

`item_code` and `item_name` were dropped. Only the two **fixed values**
survived, because a constant is keyed by `frappe_field` on both sides and
never goes through the path lookup.

### Why it looked fine until now

- **The pull cron is correct.** It fetches rows from Frappe's REST API, so
  `source` is keyed by real Frappe fieldnames and `applyMapping` pull reads
  exactly the right keys. Anything tested through a pull worked.
- **Handler packs bypass the generic mapper.** The Item created by the
  Medusa → ERPNext push earlier that day was written by the `commerce`
  pack, not by `_translate` — which is why it landed correctly while the
  generic path would have dropped `item_code` and `item_name`.
- **The key field masks it.** Medusa falls back to a raw key when the
  mapped key is absent, so a record still *matches*; it just updates with
  nothing. An operator sees the two records linked and assumes the fields
  are in step.

### The two paths, same mapping, same data

The clearest evidence came by accident. Three Items were created in ERPNext
with the Catalogue mapping enabled on both sides. The **webhook** deliveries
recorded `item.created — failed, "Product title is required"`. Minutes
later the **pull cron** ran against the same three Items and created all
three products correctly, titles and all:

```
erp-probe-1788719157   title="ERP Probe Item"   published
erp-probe2-1788719270  title="ERP Probe Two"    published
```

One mapping, one set of records, two transports, opposite outcomes. The
difference is only which key convention the payload arrived in.

## The decision

Translate once, not twice. Which side stops is a real choice, because it
changes what travels on the wire between two already-deployed apps.

(a) **Senders stop translating** — both sides emit their own native
fieldnames and the receiver maps. This makes the webhook path match the
pull cron, which is already correct, and it means a payload always
describes the system it came from. It changes the outbound format on both
sides.

(b) **Receivers stop translating** — the sender's translation is
authoritative and the receiver applies the payload as-is. Smaller change to
`_translate` and to the inbound branch of `applyMapping`, but the pull cron
then needs its own translation step, and a payload no longer says which
system it describes.

(c) Something else — say what.

Whatever is chosen, both directions need a test that uses **different names
on the two sides**. Every existing test pairs same-named fields or goes
through a handler, which is exactly why this survived six phases.

## Decided

**2026-09-07 — (c): the envelope kind fixes the key convention.** A
message is translated once, and `kind` says by whom:

- An **`event`** body (`data`) is keyed by the *sender's* own fieldnames
  and the receiver applies the field map. This is what the pull cron
  already read, so on the Medusa side the webhook and the cron now share
  one `applyMapping` call.
- A **`mapped`** body (`payload`) is keyed by the *receiver's* fieldnames
  and is applied as it is.

Every receiver already behaved this way. The Medusa → ERPNext mapping push
was never on the broken path: it sends a `mapped` envelope that goes
straight to the commerce pack, and `_translate` is only reached by a
`kind: event` body or the studio's inbound rehearsal, for which the Medusa
path *is* the right key. The one sender that broke the rule was
`outbound.build_payload`, which is now keyed by `frappe_field`.

Why not strict (a): all the value work — dot paths, templates, transforms,
defaults, required — lives only in Medusa's engine. ERPNext's field map
has no transform column and its canonical sync drops `transform`, so
having ERPNext translate a Medusa push would mean porting that engine or
half-translating on the way out, to rewrite a path with no live defect.

Why not (b): Medusa's webhook receiver would stop translating while its
pull cron kept translating, giving one mapping two conventions on the same
side.

Changed: `medusync/outbound.py` (`build_payload`), `medusync/selftest.py`
(assertions), `medusync/envelope.py` and `src/modules/erpnext/envelope.ts`
(the rule, in the wire-contract header). Tests, every pair with a different
name on each side: `medusync/tests/test_wire_convention.py`,
`src/modules/erpnext/__tests__/wire-convention.spec.ts`;
`test_field_direction` flipped to the Frappe-keyed shape.

Not built, deliberately: a `mapped` push still returns 500 when no handler
pack provides a mapped upsert. Under the rule it could fall through to the
generic apply without translating — see **Q21**.
