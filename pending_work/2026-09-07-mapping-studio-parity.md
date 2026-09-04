# A real two-panel mapper, and parity between the two sides

**Raised:** 2026-09-07, from looking at both mapping pages side by side
**Belongs to:** the next block of operator-facing work
**Side:** both, and they are in very different places.

## Where each side actually is

**Medusa** has a purpose-built page — `/app/erpnext` → **Mappings**. Entity
picker, DocType search, a field-pair grid, autofill suggestions with
confidence scores, transforms, templates, a trigger builder, and since
Phase 7: Sample, Test push, Test pull, Pull now. It is a real editor.

**ERPNext** has a DocType form. `Medusync Mapping` with a child-table grid
where you type `frappe_field` and `medusa_path` into two columns. It has
the studio bolted on as toolbar buttons — Rehearse, Rehearse and Enable,
Send a Test Event, Show a Sample Record, Push Everything Now, Re-send What
Gave Up — and a headline that says where the mapping stands. Useful, but it
is still a form where you type field names.

## What neither side has

Neither is n8n's mapper, and the difference is not cosmetic:

- **No two-panel drag.** n8n puts source fields on the left, target on the
  right, and you drag one onto the other. Both sides here are a grid of
  text pairs. The Sample button shows you the source record, but in a
  separate dialog you then read from and type back into the grid.
- **No live preview per row.** You can rehearse the whole mapping and see
  the resulting payload; you cannot see, on the row you are editing, what
  that one field currently resolves to.
- **No item-by-item walk.** There is no "show me record 1 of 40, map it,
  next" flow. Rehearsal is one sample at a time and the sample is chosen
  for you.
- **No drag to reorder or split.** Reordering is deliberately not a
  behaviour change (the signature ignores it), but there is no way to do it
  by dragging either.

## What would close it

In the order that gets the most from the least:

1. **A per-row resolved value.** The Sample already returns the record and
   the mapping engine already walks a dotted path. Showing the current
   value beside each row is a small change and removes most of the
   typing-blind problem.
2. **A field picker on each side of the row.** ERPNext's fields come from
   live meta already (`/admin/erpnext/doctypes/:name`, `studio.fields_of`).
   Medusa's come from the registry — see
   `2026-09-07-medusa-field-discovery.md`, which is the blocker for making
   the Medusa half a picker rather than a text box.
3. **The two-panel layout**, once both sides have a real field list to
   render.
4. **ERPNext parity**: a Frappe Page rather than a DocType form. A DocType
   form cannot host a two-panel mapper; it can host the buttons, which is
   what it does today.

## What is already true and worth not rebuilding

- **The mapping list does synchronise both ways.** Saving on either side
  sends the whole mapping to the other, paired by `mapping_uid`, ordered by
  `version`, ERPNext winning a tie. It fires on save, not as you type, and
  first contact always arrives switched off.
- **Both sides log it.** A mapping-config message is an ordinary logged
  message: `Medusync Log` on one side, `erpnext_sync_event` on the other,
  with the same retry, the same idempotency and the same rehearsal marking.
- **What is not logged** is the editing itself. Frappe keeps document
  version history for `Medusync Mapping` by default; Medusa keeps only
  `version` and `updated_by_user_id`. Nobody can currently answer "who
  changed this field, and to what, on the Medusa side".

## Questions

See `00-QUESTIONS-ANSWER-THESE-FIRST.md` — **Q27**, **Q28**.
