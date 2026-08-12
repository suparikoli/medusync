# Medusync

Two-way sync between a Frappe/ERPNext site and a [Medusa v2](https://medusajs.com) backend.

Site-agnostic: no assumptions about which doctypes you sync, no
customer-specific fields, nothing to edit in Python to add a doctype.
Install it on any ERPNext site, point it at a Medusa backend, and
configure the mappings from the Desk UI.

## Do I actually need this?

Not necessarily — and that's worth being honest about.

Medusa can drive a stock Frappe site on its own: it writes through
`/api/resource/<Doctype>` with an API key, and reads either by polling
or via Frappe's built-in **Webhook** doctype. If that covers you, use it.

Medusync earns its place when you want:

| | Stock Frappe | With Medusync |
|---|---|---|
| Add a synced doctype | Hand-write a Jinja payload template per doctype, on the Medusa side | Pick it from a dropdown in Desk |
| Outbound field selection | Whatever the template hard-codes | A field map, or "send all" |
| Deletes | No inbound delete handling | `on_trash` → `<doctype>.deleted` |
| Retries | 3 fixed inline attempts | Configurable, backed off, on the queue |
| Idempotency | None — a retry re-applies | Deduped on `event_id` |
| Audit | Webhook Request Log (outbound only) | Both directions, with payloads |
| Backfill existing rows | Touch every record by hand | One `bench execute` |

## Install

```bash
bench get-app medusync <repo-url>
bench --site <site> install-app medusync
bench --site <site> migrate
```

Then open **Medusync Settings** and set:

- **Medusa URL** — e.g. `https://admin.example.com` (no trailing slash)
- **Inbound Secret** — must equal the Medusa plugin's `webhook_secret`
- **Outbound Secret** — must equal the Medusa plugin's `frappe_to_medusa_secret`

Leave **Enable Sync** off until the mappings are in place. Installing
the app never starts moving data on its own.

## Configure a mapping

**Medusync Mapping** → New:

| Field | Meaning |
|---|---|
| Document Type | The doctype to sync |
| Direction | `To Medusa`, `From Medusa`, or `Two-way` |
| Trigger On | Which Frappe events push outbound (one per line) |
| Condition | Optional Python expression over `doc` |
| Key Field | The field that identifies this record to Medusa |
| Field Map | Frappe field ↔ Medusa path, with a per-field direction |
| Send All Fields | Skip the field map and send everything |

Per-field direction is how you say "Frappe owns the address, Medusa
owns the email" without splitting the mapping in two.

## Endpoints

| Path | Auth | Purpose |
|---|---|---|
| `/api/method/medusync.api.receive` | HMAC | Apply one inbound event |
| `/api/method/medusync.api.health` | none | Liveness probe |

Both directions sign the **raw request body** with HMAC-SHA256 and send
it in `X-Medusa-Signature`. Hex is emitted; hex or base64 is accepted,
so a site part-way through migrating off Frappe's native Webhook rows
(which sign base64) keeps working.

Inbound envelope:

```json
{
  "event": "customer.updated",
  "event_id": "medusa:cus_01H...:1723459200",
  "doctype": "Customer",
  "key_field": "email_id",
  "key_value": "someone@example.com",
  "data": { "customer_name": "Alex Fern", "mobile_no": "+91..." }
}
```

`event_id` is the idempotency key: a repeat is answered `skipped`, not
applied twice.

## Backfill

Hooks only fire on change, so a new mapping syncs nothing until each
record is touched. To replay what already exists:

```bash
bench --site <site> execute medusync.backfill.run --kwargs "{'mapping': 'Customers to Medusa', 'limit': 50, 'dry_run': True}"
```

Drop `dry_run` once the counts look right.

## Loop prevention

An inbound write is an ordinary document save, so it would fire the
outbound hook and push the change straight back. Medusync sets
`frappe.flags.medusync_inbound` for the duration of an inbound apply
and the outbound hook checks it.

That flag is per-request, so it does not cover a human editing the same
document at the same moment as an inbound write. That case is handled
on the Medusa side by `event_id` dedup — the cost is one redundant
round trip, not a loop.

## Operational notes

- **Log retention.** `Medusync Log` stores whatever your mapping
  carries — on a Customer mapping, that is personal data. Retention
  defaults to 30 days; set **Log Payload Bodies** off on sites where a
  second copy of that data is not acceptable.
- **Delivery is queued** by default so a slow Medusa never blocks a
  user's save. Turn it off only while debugging.
- **Deletes are opt-in** per mapping, and off by default.
- The outbound hook is a wildcard across every doctype. It returns
  immediately when no mapping matches, from a request-local cache.

## Licence

MIT
