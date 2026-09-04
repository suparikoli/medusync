# The Single still holds a connection, and still authenticates

**Deferred from:** Phase 1 (multi-site)
**Belongs to:** Phase 6, with the backward-compatibility work
**Side:** ERPNext only.

## What happened in Phase 1

Connection details moved from `Medusync Settings` onto `Medusync Site`, one
record per connected store, each with its own pair of secrets. Delivery
reads the Site record. Inbound requests are attributed to a site **by their
signature**, so a store cannot claim to be another by setting a header.

What did not happen is the removal of what came before. `Medusync Settings`
still carries `medusa_url`, `inbound_path`, `inbound_secret` and
`outbound_secret`, and `api._legacy_secret_matches` still accepts the
Single's inbound secret:

```python
def _legacy_secret_matches(raw: bytes, provided: str | None) -> bool:
    secret = config.get_secret("inbound_secret")
    return bool(secret and provided and verify(raw, secret, provided))
```

## Why that matters

It is a second key to every door. A request signed with the Single's secret
is accepted whichever site it claims to be from, and because no site
matched, `site_id` falls back to `default`. So a leaked Single secret is
not scoped to one store — it authenticates inbound traffic for the whole
installation, and the log row says `default` rather than saying "this could
not be attributed".

It is also the one place where "which site is this?" is answered by
something other than the signature, which is the property the rest of the
multi-site design rests on.

## Why it is still there

A site upgraded from before Phase 1 has its secrets in the Single. Patch
`v1_2.create_default_site` copies them onto a site called `default`, but
the fallback covers the window where the ERPNext side has migrated and the
Medusa side is still signing with what it had. Removing it during a rolling
deploy would drop real traffic.

## What removing it needs

- A migration that is confident every connected store has been repaired.
  The honest check is `Medusync Site.last_seen_at` being recent for every
  enabled site: it means each one has successfully delivered with its own
  secret, so nothing is relying on the fallback.
- Blanking the four Single fields, or marking them read-only with a
  description saying where the real ones live, so nobody edits a field that
  no longer does anything.
- A deprecation window where the fallback still works but writes a warning
  naming the site that used it, so an operator learns which store has not
  been repaired.

Until then, the fallback is what an operator would want on the day of an
upgrade, and a liability on every other day.
