# One sync per pair, and both lists read the same

**Raised:** 2026-09-07, looking at the two mapping lists side by side
**Side:** both, and the identity that travels between them
**Severity:** the two lists drifted apart and one sync showed up twice

## What happened

The Medusa list held one mapping; the ERPNext list held four, two of them
for Orders. Three separate causes, all of one shape.

- **A mapping travelled only when it was saved.** Nothing exchanged the
  whole list when the two connected, and nothing reconciled afterwards.
  Customers had been deleted on the Medusa side on 6 Sep, and ERPNext at
  the time answered a delete by switching its copy off; Orders had never
  been received by Medusa at all, because every save of the ERPNext
  defaults happened under the reset flag, which does not push.
- **The two sides shipped different default sets with different
  identities.** ERPNext seeded `default:customer` and friends; Medusa seeded
  "Customer ↔ Customer" by name with a random identity. They met as
  strangers.
- **The wizard minted a new identity every time.** "Orders ↔ ERPNext" was
  created on Medusa beside ERPNext's Orders, and synced across as a second
  mapping for the same pair.

## Decided

**2026-09-07 — a sync is its pair.** One Medusa entity and one DocType,
per store, is one mapping, whichever side created it and whatever it is
called. Its identity is derived from the pair —
`pair:<entity>:<doctype>[:<site>]`, e.g. `pair:order:sales_order` — so both
systems agree which mapping is which without asking. A sync direction of
to, from or both, and the per-field directions, all live inside that one
mapping.

What follows from that:

- **One per pair, enforced.** A second mapping for a pair is refused on
  both sides with a message naming the one that exists. The pair of an
  existing mapping cannot change; a different pair is a different sync.
- **Twins fold.** The higher version survives, then the one switched on,
  then the older row; the other's field pairs and trigger events are
  folded in and the other is removed. ERPNext patch
  `v1_6.pair_identity`, Medusa migration `Migration20260907120000`, and a
  unique index on Medusa's `mapping_uid`.
- **Whatever arrives lands on the pair.** An inbound mapping is resolved
  by its identity, then by its pair, and is stored under the pair's own
  identity — so a copy from before this rule converges on its next save.
- **The guided setup folds into an existing sync** rather than creating
  another: its field pairs are added, its events unioned, and a one-way
  sync meeting its opposite becomes two-way. The wizard says so.
- **Deleting propagates on both sides now.** Medusa used to disable its
  copy when ERPNext deleted; a disabled twin is how one sync came to look
  like two.
- **The whole list travels on demand and on connection.** "Send all
  mappings to the store" on the ERPNext Mappings page and "Send all to
  ERPNext" on the Medusa Mappings tab; automatically when a Medusync Site
  is saved and when Medusa's connection settings are saved. The receiver
  keeps its newer copies, so it is safe to press twice. This answers
  **Q15**: automatic, plus a button, rather than a differences view.
- **Defaults share identity.** ERPNext's shipped set now carries pair
  identities and finds its mapping by pair; Medusa's seed does the same,
  so reseeding on either side updates rather than duplicates.
- **A mapping runs only when both sides have it on (2026-09-07, later
  the same day).** Switched off anywhere, it is off everywhere: a local
  switch-off — the drift check on either side, the attention flag — goes
  out one version up, and the reason travels with it (`attention`,
  `attention_detail` on the canonical form, present only when off). A side
  that declines a remote enable, because it has not rehearsed the mapping
  itself, answers with off and the reason, so the side that asked turns it
  off too rather than believing it runs. After a reset ERPNext sends the
  whole list, so the versions line up again. What the operator saw before
  this — a switch that moved on one side only — was version drift: the two
  copies had been bumped apart by saves that never pushed, and every later
  edit from the lower side was refused as stale.
- **Each editor shows what the receiving side insists on.** A "Required in
  ERPNext" box when data flows to ERPNext and a "Required in Medusa" box
  when it flows to Medusa, each field marked covered or not by a pair that
  flows that way (a fixed value counts), with "Add the missing ones".
  Fields Frappe derives from a link or defaults are not listed; neither is
  `name`.
- **Rehearsals ask the receiving side's question.** Medusa's dry run
  rehearses the pull half of a pull or two-way mapping — a Frappe-shaped
  sample through the mapping, and the store's own required fields — instead
  of refusing a pull-only mapping for ERPNext fields it never writes.
  ERPNext's outbound rehearsal asks the store what it requires and fails on
  one nobody sends; a store that cannot be reached is a warning, not a pass.

Changed, ERPNext: `mapping_sync.py` (identity, folding, `push_all`,
`sync_now`, site hook), `medusync_mapping.py` (`validate_pair`,
`stamp_identity`), `defaults.py`, `hooks.py`, the Mappings page. Changed,
Medusa: `mapping-sync.ts` (identity and folding helpers), `index.ts`
(`saveMapping`, `findMappingByPair`, `applyMappingConfig`,
`removeMappingConfig`, `pushAllMappingConfigs`, seeding), the mappings
routes (409 on a duplicate pair, `POST mappings/sync-now`), the settings
route, the admin page. Tests: `test_pair_identity.py`,
`pair-identity.spec.ts`; fixtures that named a real entity moved to probe
entities, since the site's own mapping for that pair now refuses them.

## Not done here

- Site-pinned pairs on the Medusa side: Medusa holds one connection (Q10),
  so its pairs carry no site; ERPNext's may. A mapping pinned to a site on
  ERPNext arrives on Medusa with `site_id` and derives the same identity.
- The ERPNext "Add a sync" dialog still lets you pick a pair that exists
  and only tells you on Create. It could offer to open the existing one.
