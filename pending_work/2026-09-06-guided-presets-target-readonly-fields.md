# The guided presets map fields ERPNext will not keep

**Raised:** 2026-09-06, while checking why autofill suggests so little for
Customer
**Side:** both — the preset lives in the Medusa admin, the constraint is
ERPNext's

## What was found

On a vanilla ERPNext 16, `Customer` does not hold a customer's email or
phone. Both are fieldtype **Read Only** with a `fetch_from`:

| Field | Fieldtype | fetch_from |
|---|---|---|
| `email_id` | Read Only | `customer_primary_contact.email_id` |
| `mobile_no` | Read Only | `customer_primary_contact.mobile_no` |
| `first_name` | Read Only | `customer_primary_contact.first_name` |
| `last_name` | Read Only | `customer_primary_contact.last_name` |
| `customer_name` | Data, **mandatory** | — |
| `customer_type` | Select, **mandatory** | — |

The real values live on a **Contact**, linked through
`customer_primary_contact`. Frappe repopulates the Read Only columns from
that link.

The `customers` preset in `SYNC_PRESETS` (admin `page.tsx`) maps:

- `email` → `email_id` — a fetched column, **and it is the mapping key**
- `phone` → `mobile_no` — a fetched column
- `first_name` → `customer_name` — correct, and the mandatory field
- `id` → `medusa_customer_id` — correct, a custom field medusync adds

So two of the four pairs write columns ERPNext treats as derived. It appears
to work while `customer_primary_contact` is empty, which is why an
end-to-end push passed earlier — the column is written and nothing
overwrites it. As soon as a Contact is linked to that Customer, Frappe
refills those columns from the Contact and the synced values are gone. Using
`email_id` as the key field has the same exposure: the key is a column the
ERP considers derived.

`autofill` is already right about this — it skips all four with the reason
"fetched from customer_primary_contact.email_id". That is why Customer
autofills 1 pair out of 52 fields and looks broken. It is not; the DocType
genuinely has almost nothing directly writable that a Medusa customer maps
onto.

## Why this is not just a preset bug

A Medusa customer is one record. Its ERPNext counterpart is three —
Customer, Contact, Address — and the flat dot-path mapper can only address
one DocType per mapping. Options, none of them free:

1. **A `customer` handler pack** that writes the Contact and Address
   alongside the Customer. Correct, and it is what the handler-pack
   mechanism exists for. Not configurable from the mapper.
2. **A second mapping, `customer` → `Contact`**, keyed on the Medusa id,
   with the link set by a constant or by the pack. Configurable, but the
   ordering (Customer must exist before the Contact links to it) is not
   something the mapper expresses.
3. **Leave Customer to `customer_name` + `medusa_customer_id` only**, and be
   honest in the preset that email and phone need one of the above.

(3) is the smallest honest change and should happen regardless, because the
current preset promises something it does not durably deliver.

## Done — 2026-09-06

(3) was taken, and fixed-value pairs were built to go with it.

- **Fixed-value pairs exist now, on both sides.** A pair may carry a
  `constant` instead of a `medusa_path`: push-only, written every time,
  never pulled back, and part of the rehearsal signature so changing one
  re-arms the enable gate. Medusa: `mapping-engine.ts`, `signature.ts`,
  `validateFieldMappings`, the canonical wire form. ERPNext: a
  `constant_value` column on `Medusync Field Map`, applied in
  `api._translate`, skipped in `outbound.build_payload`, carried by
  `mapping_sync`, and folded into `test_signature`. Covered by
  `test_fixed_value_fields.py` (8) and the Medusa unit tests (11).
- **The Customers preset** no longer claims email or phone, keys on
  `medusa_customer_id` rather than the fetched `email_id`, sends
  `{first_name} {last_name}` into the mandatory `customer_name`, and
  carries a fixed `customer_type`. Its note says where email and phone
  actually live.
- **The Products preset** now includes `item_group` and `stock_uom` as
  fixed values, which is what it was missing — both are mandatory Links
  with no default, so a product push could not have created an Item at all.

**No value is shipped.** Every preset's `constant` starts empty and the
wizard will not continue while one is blank. The choices come from the
connected site through
`GET /admin/erpnext/doctypes/:name/options?field=x` — a Select answers with
its own options, a Link with the records that exist there now, anything
else with nothing and the UI asks for free text. Hard-coding "Products" or
"Nos" would be one client's setup shipped to every client, and it fails
Link validation anywhere it does not hold — as it would here: this sandbox
had zero Item Groups and no UOMs until its setup wizard was completed.

## Still open

- **Q11** — email, phone and address still need Contact and Address. A flat
  mapping addresses one DocType, so this is a handler pack or a second
  mapping, and it is a decision rather than a defect.
- **Orders → Sales Order has not been checked** the way Customer and Item
  were. It has twelve mandatory fields including `company`, `currency`,
  `selling_price_list` and the `items` Table, and its preset maps two
  fields, relying on the handler pack for the rest. Whether that holds on a
  set-up ERPNext is untested. The sandbox can now run it — its setup
  wizard was completed on 2026-09-06.

---

## Decided

Moved here from `00-QUESTIONS-ANSWER-THESE-FIRST.md`, which now
carries only questions still waiting on an answer. The decision and
the reasoning stay with the work they govern.

### Completing ERPNext's setup wizard

> **Answer:** a — done on 2026-09-06. Company "Medusync Sandbox" (MS),
> India - Chart of Accounts, FY 2026-2027, Accounting and Stock, no demo
> data. That gives 75 accounts, six Item Groups, 239 UOMs, four warehouses
> and the two standard price lists, so the fixed-value pickers now have
> real choices and Products/Orders can be exercised. Still to do: actually
> run a product and an order through, which is what this was blocking.
>
> The wizard could not complete on its own, and it is worth knowing why
> before rebuilding this sandbox. This ERPNext build registers only its own
> two slides (persona, organization) and no country/region slide, so the
> payload carried `country: None`; `install_fixtures.get_preset_records`
> then calls `country.replace(...)` and dies with a bare AttributeError.
> Setting the *default* country fixes only the Chart of Accounts dropdown
> (its JS reads `frappe.defaults`), not the payload — and passing a country
> in the payload does not help either, because
> `setup_wizard.set_missing_values` overwrites country, time zone and
> currency from System Settings unconditionally, despite its name. The fix
> is to set **System Settings** country / time_zone / currency first, then
> run the wizard. No app code was changed: Frappe and ERPNext are vendored.

