# Picking this up on another machine

Written for the version of you that comes back to this in a fortnight, on a
different computer, having forgotten which half lives where.

## What is in git and what is not

| | Where | In git |
|---|---|---|
| This plugin | a Medusa 2.19 project's plugin folder | ✅ `mithtech-is/medusa-erpnextsync` |
| The Frappe app | `frappe-bench/apps/medusync` | ✅ `suparikoli/medusync` |
| The Medusa project you test against | wherever you put it | ❌ yours, local only |
| The Frappe bench and its site | wherever you put it | ❌ never was |

Both applications are entirely in git. **Neither stack is.** That is the
important sentence: a new machine has the code and none of the environment,
and rebuilding the environment is most of the work.

Both repos carry everything on their default branch — `master` for the
Frappe app, `main` for the plugin.

## What you can do with only the repos

More than you would expect, and this is the cheapest way back in:

```bash
git clone https://github.com/mithtech-is/medusa-erpnextsync.git
cd medusa-erpnextsync && npm install
npm run typecheck     # tsc, app and specs
npm test              # vitest — no database, no Medusa
```

The pure modules carry the rules worth being sure about — the envelope, the
mapping engine, echo suppression, the reset secrets, the circuit breaker,
the mapping signature, the order-payment merge — and none of them needs a
running anything.

The Frappe app's tests are the opposite: they are integration tests and need
a bench with a site.

## What a full environment needs

**Frappe side.** A bench on Frappe/ERPNext **v16** (the app declares
`required_apps = ["erpnext"]` and there are no v15 shims), a site, then:

```bash
bench get-app https://github.com/suparikoli/medusync.git
bench --site <site> install-app medusync
bench --site <site> migrate
bench --site <site> run-tests --app medusync    # ~300 tests
```

No `set-config` step: a fresh site gets the `commerce` handler pack, which
is what a site without an opinion wants. `after_migrate` installs the three
shipped mappings, switched off, and runs the drift check. Both report rather
than fail.

**Medusa side.** Any Medusa 2.19 project. The plugin is consumed through
Medusa's yalc flow, not npm:

```bash
# in the plugin
npx medusa plugin:build && npx medusa plugin:publish
# in the Medusa project
npx yalc update medusa-plugin-erpnext     # REQUIRED — publish alone does not move it
pnpm install
pnpm exec medusa db:migrate
```

`yalc update` is the step everyone forgets, and skipping it installs the
previous build so a newly imported module is simply missing at boot.

## Pairing the two

1. **ERPNext:** create a **Medusync Site**. The Site ID travels in every
   message and cannot change once records are correlated. Generate two long
   random strings for its Inbound and Outbound secrets.
2. **Medusa:** ERPNext page → settings. Cross them over — our Inbound Secret
   is the store's `frappe_to_medusa_secret`; our Outbound Secret is its
   `webhook_secret`.
3. Set `erpnext_url` on the Medusa side and `medusa_url` on the Site.
4. Test **both** directions: `POST /admin/erpnext/ping` and, on the Frappe
   desk, **Medusync Settings → Test connection to Medusa**. They use
   different secrets and prove different things; one green does not imply
   the other.

An inbound request is attributed to a store **by its signature**, not by any
header, so a store cannot claim to be another. The one exception is the
legacy Single secret — see `pending_work/` and question Q19.

## If the two halves are on different machines or in a VM

The addresses are the fiddly part and are machine-specific. `docs/LOCAL_DEV.md`
covers the WSL⇄Windows case in detail, including why the WSL IP changes on
every restart and the script that rewrites both sides. On a single Linux or
macOS machine none of that applies and `localhost` works.

## Where to start reading

- `docs/OPERATIONS.md` — running it, in both repos.
- This file is kept identical in both; it describes the pair, not one side.
- `pending_work/00-QUESTIONS-ANSWER-THESE-FIRST.md` — every open decision.
- `README.md` — the wire contract, the studio, the reset, the defaults.

## What a fresh install looks like

So that a difference from this is recognised as a difference, not assumed
to be a bug:

- Three shipped default mappings — customer, catalogue, orders — installed
  and **switched off**. They stay off until a mapping is rehearsed in the
  studio; that gate is deliberate and is the whole point of Phase 4.
- The `commerce` handler pack loaded, because the site config says nothing.
  It is stock levels, prices, delivery notes, invoices and order metadata.
- No Medusync Site, so nothing is delivered anywhere. Create one, pair it,
  then map its warehouses and price lists — neither has a default, because
  neither can be guessed.
- Empty `Medusync Log` and `erpnext_sync_event`. Both fill from first use
  and are pruned on the retention in the settings.
- The legacy Single secret **on**, which is the shipped default and matters
  only to a site upgrading from before there were Sites.
