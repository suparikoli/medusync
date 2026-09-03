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
sudo supervisorctl restart all      # or: bench restart
```

**That last line is not optional on a running site.** Adding an app to
`sites/apps.txt` makes every request import it, but gunicorn workers
started before the install cannot see a newly pip-installed package.
Skip the restart and the entire site returns HTTP 500 —
`ModuleNotFoundError: No module named 'medusync'` — until you do it.
That is the whole site, not just the sync.

If you installed by pointing bench at a local directory rather than
`get-app`, install the package into the bench env first:

```bash
./env/bin/pip install -e apps/medusync
```

If `install-app` fails partway with *"Module import failed for Medusync
Settings"*, the Module Def existed but the doctype loader had a stale
module map. Recover with:

```bash
bench --site <site> clear-cache && bench --site <site> migrate
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

## Handler packs

medusync's core is domain-neutral. Site-specific behaviour ships as
*handler packs* under `medusync/handlers/<pack>/` (today: `polemarch`,
`risitex`). A site chooses which packs load in its `site_config.json`:

```json
"medusync_handler_packs": ["risitex"]
```

When the key is absent the `polemarch` pack loads, which is what existing
installations expect. An empty list loads nothing: inbound events then go
through Medusync Mapping rows only, and `receive_mapped` answers 500 until
a pack that provides a mapped upsert is configured.

Today the switch gates inbound dispatch and the mapped upsert; the RISITEX
pack's *outbound* doc-event hooks in `hooks.py` still run on every site
(see `pending_work/2026-09-04-outbound-hooks-behind-packs.md`).

## Retries

A delivery that fails is parked with a **Next Attempt At** on its Medusync
Log row (30 s, then 120 s, 270 s …) and picked up by a once-a-minute
scheduler sweep (`medusync.tasks.retry_due`), so the scheduler must be
running. **Max Attempts** in the settings bounds the total.

## Sites

One ERPNext can serve several Medusa stores. Each is a **Medusync Site**
record holding that store's URL and its own pair of shared secrets, so a
leak or an outage at one store cannot touch another. A mapping with no
site applies to every enabled site; one pinned to a site applies only
there.

An inbound request is attributed to a site **by its signature**, not by
anything the caller claims, so a site cannot impersonate another by
setting a header.

Upgrading from the single-connection setup needs no work: the migration
copies the Single's URL and secrets onto a site called `default`.

## Wire contract (v2)

Every signed body carries a version, a timestamp and an origin:

```json
{
  "v": 2,
  "kind": "event",
  "event": "customer.updated",
  "event_id": "frappe:Customer:CUST-0001:2026-09-04 10:00:00:default",
  "ts": 1788474641,
  "origin": {
    "system": "erpnext",
    "site_id": "default",
    "correlation_id": "1722607afc9a4e6b95ae85bf1c0746ce"
  },
  "data": { "email_id": "someone@example.com" }
}
```

`kind` says what the body holds: `event` (a business event), `mapped` (an
already-mapped upsert with doctype, key and payload) or `mapping` (the
mapping configuration itself). A body with no `v` is read as v1 and still
applies, so the two apps can be upgraded one at a time.
`medusync/envelope.py` and the plugin's `envelope.ts` are mirrors — change
them together.

## Mapping synchronisation

A mapping is one configuration living in two systems. Both copies share a
`mapping_uid` and carry a `version`; saving on either side sends
`mapping.upserted` to the other. The higher version wins, and on a tie
**ERPNext wins**, because ERPNext owns which documents may sync at all and
the two decisions must not disagree. A deleted mapping disables the far
copy rather than destroying it, so records already correlated by it stay
traceable.

A mapping arriving under a **uid this site has never seen** is created
switched **off**, whatever the sender says: first contact between two
systems that each already had mappings must not enable a rule nobody
reviewed here. Updates to a uid already held apply as sent.

The canonical form carries what both sides understand: identity, version,
direction, the key pair and the field pairs. Options that exist on one
side only — **Send All Fields** here, the Medusa event list there — do not
travel, so a mapping that relies on them needs that part set again on the
far side.

## Loop prevention

An inbound write is an ordinary document save, so it would fire the
outbound hook and push the change straight back. Medusync sets
`frappe.flags.medusync_inbound` for the duration of an inbound apply
and the outbound hook checks it.

The flag is per-request, so on its own it cannot stop the echo a
background worker sends a moment later, after the request is gone. An
inbound write therefore also leaves a short-lived breadcrumb on each
document it touched: "this was last changed by correlation C, which came
from medusa:site-a". Anything sent about that document within the window
is stamped `echo_of`, and the far side drops what it recognises as its
own. The breadcrumb expires, so a person editing the same document a
minute later is never mistaken for an echo.

## Operational notes

- **Log retention.** `Medusync Log` stores whatever your mapping
  carries — on a Customer mapping, that is personal data. Retention
  defaults to 180 days; set **Log Payload Bodies** off on sites where a
  second copy of that data is not acceptable.
- **Delivery is queued** by default so a slow Medusa never blocks a
  user's save. Turn it off only while debugging.
- **Deletes are opt-in** per mapping, and off by default.
- The outbound hook is a wildcard across every doctype. It returns
  immediately when no mapping matches, from a request-local cache.

## Licence

MIT
