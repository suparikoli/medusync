# Prompt: store payments and payment-gateway settlement

**This is one prompt, not two.** The identical file exists in both repos
(medusync app and the Medusa plugin) so whichever you open first has it.
Run it **once**, in a single session — the work spans both repos and
splitting it across two sessions would have them editing the same wire
format from opposite ends.

## Where to run it

Start the session with the **bench root as the working directory**, because
every `bench` command must run from there:

    /Volumes/Extreme-SSD/Coding/frappe16

## Folders to grant access to

Three, all required:

| Folder | Why |
|---|---|
| `/Volumes/Extreme-SSD/Coding/frappe16` | bench root; the medusync app is at `apps/medusync`. All `bench` commands and the test suite run here. |
| `/Volumes/Extreme-SSD/Coding/00-medusa` | the plugin, at `Plugins&Extensions/medusa-erpnextsync`. Note the `&` in that path — quote it in shell commands. |
| `/Volumes/Extreme-SSD/Coding/Splendx` | the store that exercises the sync, at `deploy/backend`. Needed to run the plugin against a real Medusa and to read `apps/backend/.env` for `DATABASE_URL`. |

In Claude Code: open the session in `frappe16` and add the other two with
`--add-dir`, or use `/add-dir` once inside.

## Environment notes that will cost you an hour otherwise

- The bench uses **port-based multi-tenancy** (`dns_multitenant` off,
  `serve_default_site: false`): one dev server per site, and the **port
  selects the site** — the `Host` header is ignored. `fixerp.localhost` is
  on **8002** (polemarch 8000, frappe16 8001, medusync-vanilla 8003).
  Unauthenticated `/api/method/ping` answers on every port, so it proves
  nothing; authenticate to confirm you are on the right site.
- `bench start` is managed (tmux/launchd) and restarts itself. Do not start
  it by hand.
- Postgres for Splendx: no `psql` installed — use node `pg` from
  `deploy/backend/node_modules/.pnpm/pg@*/node_modules/pg`.
- The Medusa backend is on **:9009**, storefront :3000, crew :3010.
- Run the medusync suite with
  `bench --site medusync-vanilla.localhost run-tests --app medusync`
  (the vanilla sandbox), never against `fixerp.localhost`, which holds real
  restored accounting data.

---

Work on payment handling for the Frappe/ERPNext ↔ Medusa v2 sync. Two
repositories, both with uncommitted work already in them:

- Frappe app: `/Volumes/Extreme-SSD/Coding/frappe16/apps/medusync` (bench
  root `/Volumes/Extreme-SSD/Coding/frappe16`, site `fixerp.localhost`,
  test site `medusync-vanilla.localhost`)
- Medusa plugin: `/Volumes/Extreme-SSD/Coding/00-medusa/Plugins&Extensions/medusa-erpnextsync`
  (package `@mithtech-medusa/plugin-erpnext`), installed into the Splendx
  store at `/Volumes/Extreme-SSD/Coding/Splendx/deploy/backend`

Read `pending_work/2026-09-14-store-payments-and-gateway-settlement.md` in
either repo first — it has the full background, the constraint and a
suggested accounting shape. Read each repo's README and any AGENTS.md /
CLAUDE.md before changing anything.

## The task

A Splendx order is paid through one of several methods: a payment gateway
(card, UPI, net banking, wallet), NEFT/IMPS/RTGS, or cash on delivery.
Today medusync books one Payment Entry per captured payment, using a single
Mode of Payment field on Medusync Site. Make that work properly for real
money:

1. **Several payment methods per store.** The store's payment provider must
   decide which Mode of Payment and which account the Payment Entry uses.
   This is configuration, not code — nothing gateway-specific in either
   codebase.

2. **Gateway settlement.** The gateway keeps a commission and charges GST on
   it, then transfers the net days later. Handle the difference between the
   gross the customer paid, the charge, the GST on the charge, and the net
   that reaches the bank, so that the invoice reads fully paid, the bank
   matches the statement, and commission and input GST land in the right
   accounts. Settlement is a separate event from capture and arrives later.

3. **Idempotency.** Captures and settlements both arrive more than once —
   subscriber, retry job and reconciliation sweep all push. Nothing may
   double-post. `medusync/links.py` (Medusync Link) is how records are
   already keyed to Medusa ids; use it rather than inventing a second
   mechanism.

## Hard constraints — these are non-negotiable product rules

- **No Custom Fields on ERPNext doctypes, ever.** Required values are
  generated or pulled from the other system. The user's note that "medusync
  has to create fields for this" conflicts with this rule — resolve it
  explicitly with the user before writing code; do not quietly add fields.
  `Medusync Link.details` (JSON), a child table on Medusync Site, or a
  Medusync-owned doctype are the ways to hold data without touching an
  ERPNext doctype.
- **No hardcoded deployment values**, and no company, account or gateway
  names in code.
- Neither app invents fields, columns or mapping pairs on its own.
- Generic behaviour belongs in the plugin repo, never in Splendx's code.

## Before you write anything

Ask the user:

- Which accounts: the gateway clearing account, the commission expense
  account, and the input-GST account, per company.
- Whether settlement arrives by webhook, by a settlement report the plugin
  polls, or by manual import.
- Whether a partially settled batch should post at all, or wait.

## Definition of done

- `bench --site medusync-vanilla.localhost run-tests --app medusync` passes,
  with new tests covering split payment methods, the settlement split, and
  repeated delivery of the same capture and settlement.
- In the plugin: `npx tsc --noEmit` clean, `npx vitest run` green, and
  `npx medusa plugin:build` succeeding.
- `record_payments` can be turned back on for Medusync Site `splendx` and a
  real order books correctly end to end.
- No Custom Field is added to any ERPNext doctype.

## State to be aware of

Payment recording is currently **off** on Medusync Site `splendx`
(`record_payments = 0`, `mode_of_payment` cleared). Orders and invoices sync
without it. `UPI Payment`'s default account for `SPLENDAX GLOBAL PRIVATE
LIMITED` is `Cash - SBPL`, a Cash account, which is wrong for online
receipts and needs correcting as part of this work.

`medusync/payment_modes.py` already reports which Modes of Payment are
absent or unusable for a company and can create them on request; extend it
rather than starting again.
