# Product images ERPNext → Medusa

**Deferred from:** Phase 3 (entity breadth)
**Belongs to:** a later phase, alongside the versioned default mappings
**Side:** both systems. This file is the ERPNext half; the plugin repo's
`pending_work/` holds the Medusa half.

## What the brief asks for

"Products, variants and images flow ERPNext → Medusa by default." Phase 3
delivered products and variants through the mapping engine, and defended them
with the catalogue guard. Images were not touched.

## Why images are not just another field

Every other mapped field is a value that fits in a JSON payload. An image is a
file that has to exist in two places:

- ERPNext keeps it as a `File` document attached to the Item, public
  (`/files/x.jpg`) or private (`/private/files/x.jpg`) — and a private one is
  not fetchable without a session.
- Medusa wants a URL its storefront can serve, or an upload through its file
  module into whatever provider the store runs.

Three questions decide the shape:

1. **Who moves the bytes?** ERPNext pushing to a Medusa upload endpoint, or
   Medusa pulling a URL. Pulling is simpler right up until the file is private.
2. **What counts as a change?** Re-sending every image on every Item save would
   be ruinous. A content hash carried in the payload is the cheap answer.
3. **Which image is which?** ERPNext has one `image` field plus arbitrary
   attachments; Medusa has an ordered gallery with a thumbnail. The mapping has
   to say what the primary is and whether attachments follow it.

## Where it attaches here

- A handler-pack hook on `File` (`after_insert`, `on_trash`), filtered to
  attachments of the catalogue DocType.
- It must go through `selection.is_allowed` and the per-store rules like every
  other outbound path, or an Item somebody excluded would still leak its
  photographs.
- `outbound.emit` already takes a per-store body, so one image event can carry a
  different URL per store if a provider ever needs it.

## Why it was left

Images are the one entity in the default mapping table whose delivery mechanism
differs in kind from everything else. Adding a file transfer to Phase 3 would
have meant either re-uploading on every save or a half-built change check, and
multi-warehouse stock and the catalogue guard were the things that were actively
wrong today.
