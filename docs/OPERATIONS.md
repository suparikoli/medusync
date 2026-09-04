# Running medusync

For the person who has to keep this working, rather than the person who
wrote it. The README explains what the app does; this explains what to do
when it is doing it wrong.

Everything here is in the Desk. You do not need the console for any of it.

---

## Connecting a store

1. **Medusync Site → New.** The Site ID travels in every message and names
   the store in every log row, so it cannot change once records are
   correlated. Lower case, no spaces.
2. Put the store's URL in. Leave the inbound path alone unless the plugin
   was configured otherwise.
3. Generate two secrets — any long random strings — and put the *same* two
   into the Medusa admin's ERPNext settings, crossed over: our **Inbound
   Secret** is the store's `frappe_to_medusa_secret`, our **Outbound
   Secret** is its `webhook_secret`.
4. Save. **Medusync Settings → Test connection to Medusa** should go green.

The signature is how a message is attributed to a store, so a store cannot
claim to be another by setting a header. That is also why each store gets
its own pair: a leak at one cannot be used against another.

## Turning a mapping on

Nothing syncs until a mapping is enabled, and a mapping cannot be enabled
until it has been rehearsed. Open it and use **Test**:

- **Show a Sample Record** — what a record of this DocType actually looks
  like. Click through it while filling in the field map.
- **Rehearse** — what would be sent, and what an arriving payload would
  do, including anything ERPNext itself would refuse. Writes nothing.
- **Rehearse and Enable** — the same, and switches it on if it held up.
- **Send a Test Event** — a real signed request to every store. The store
  checks the signature and answers what it *would* do, without writing.
  This is the only check that proves the secret and the network.

A green rehearsal proves the mapping translates, that ERPNext would accept
the result, and — if you used Send a Test Event — that the store is
reachable and agrees. It does not prove the mapping is the one you meant.

**After enabling, the store still knows nothing about what already
exists.** Use **Run → Push Everything Now**. Set a limit of 5 first and
watch those land before doing all two thousand.

## Reading the log

**Medusync Log**, one row per message per store.

| Status | Means |
|---|---|
| Queued | waiting, or waiting for its next attempt |
| Success | the store took it. `Action` says what it did |
| Skipped | deliberately not sent or not applied. `Error` says why |
| Failed | one attempt failed; it will be tried again |
| Poison | attempts exhausted. It will not be retried on its own |

**Test Run** marks a rehearsal. Those are never retried, never suppress a
real message, and are deleted within a day.

When something is wrong, filter by Status = Poison and read the `Error`
column. Fix the cause, then **Run → Re-send What Gave Up** on any mapping;
it puts every row that gave up back in the queue with a fresh set of
attempts.

## When a store stops answering

After ten consecutive failures a store is left alone: **Stopped Trying At**
is set on the Medusync Site, deliveries to it are skipped rather than
attempted, and one message per minute is let through to find out whether
it has come back. One success clears it.

Other stores are unaffected. That is the point — without it, one store
that is down holds workers on timeouts and starves the queue for the
stores that are up.

To clear it by hand once you know the store is back, open the site and
save it after setting Consecutive Failures to 0, or just wait: the probe
does it for you within the minute.

## "Needs Attention" on a mapping

Two things set it, and they mean different things.

**Mapping Required** — the shipped default set changed and this mapping
was edited since it was installed, so the upgrade left it alone. Nothing
is broken; you have a decision. Compare yours with the shipped one and
either keep yours (clear the flag by saving) or press **Run → Apply New
Defaults**, which discards your changes to that one mapping.

**Field Missing** — the mapping names a field the DocType no longer has,
so it cannot do what it claims, and **it has been switched off**. Only it;
every other mapping keeps running. Fix the field map, then rehearse and
enable it again.

The drift check runs nightly and after every `bench migrate`.

## Stopping one document from syncing

On the document itself: **Sync with Medusa**, or, with several stores
connected, **Medusa Sites**. Whatever you untick is also written to
**Medusync Exclusion**, the central list, and an entry added there by hand
updates the document. They are two views of one decision and never
disagree.

A document nobody has touched syncs. Turning selection on does not stop a
store that was working.

## Starting over

**Medusync Site → Danger Zone → Hard Reset.** Read the dialog; it lists
exactly what is kept and what goes.

Both systems have to agree. Each generates a secret and shows it once, and
each has to be handed the other's. A secret lives three minutes and works
once. If you lose one, generate another — the old one is retired.

Afterwards **nothing is running**: the shipped mappings are back, switched
off, and anything you had written is switched off but kept. Rehearse and
enable what you want. Every cross-system id survives, so the two systems
still recognise each other's records.

## Upgrading

`bench migrate` does two things beyond the schema: it brings the shipped
mapping set up to date without overwriting anything you have edited, and
it looks for mappings that name fields which no longer exist. Both report
rather than fail — a migrate never stops because a mapping went stale.

Read the output. It names how many defaults were added, updated, and how
many need a decision.

## Where the unfinished work is written down

`pending_work/` in this repo, one file per topic, each saying what exists
today and what has to be decided before it can be built. It is tracked in
git on purpose. The Medusa plugin keeps its own; items touching both
appear in both under the same filename.
