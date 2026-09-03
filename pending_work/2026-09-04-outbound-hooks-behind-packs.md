# Move the RISITEX outbound doc_events behind the handler-pack switch — deferred 2026-09-04

**Gap.** `site_config.json` → `medusync_handler_packs` now gates *inbound*
dispatch (`handlers.dispatch`) and the mapped upsert (`MAPPED_UPSERT`). The
*outbound* handlers of the RISITEX pack are still wired unconditionally in
`medusync/hooks.py` (Stock Ledger Entry, Sales Order, Delivery Note, Shipment,
Sales Invoice, Item Price, Item, Customer → `handlers.risitex.*`). Their
`_guard()` only checks `enabled` and the inbound flag, so a site configured
with `["polemarch"]` or `[]` still runs them (no behaviour change versus
master, which added them the same way).

**Target (Phase 1).** Keep `hooks.py` domain-free: one generic dispatcher per
doc event that asks each *configured* pack for its outbound handlers
(`OUTBOUND_HOOKS = {doctype: {event: fn}}` on the pack module), so packs are
truly opt-in in both directions.
