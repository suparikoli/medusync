# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""ERPNext owns the catalogue, and a mapping must not be able to sign
that away by accident.

Two rules, both from the brief:

  - a Medusa update must not overwrite ERPNext product fields unless an
    operator has explicitly allowed it;
  - a Medusa product deletion must never delete the ERPNext Item.

Neither is something the mapping engine can decide. Carrying `title` in
both directions is a perfectly reasonable mapping to configure, and the
person who configured it was thinking about new products, not about the
description someone in purchasing spent an afternoon writing. So the
guard sits *outside* the mapping: whatever the field map says, an update
to a catalogue record that already exists here is held back until the
setting is on.

Deletion is stricter and has no setting at all. An Item carries stock,
purchase history and ledger entries; a storefront cannot be allowed to
destroy that, and disabling it is nearly as bad because ERPNext would
stop letting anyone transact against it. So a delete unlinks: the Item
keeps everything it had and simply stops claiming a Medusa product. The
next link (by hand, or by a create) reattaches it.

A refusal is not an error. It comes back as a skipped result so the
sender records "nothing to do" and stops; treating it as a failure would
put the event into a retry loop that can never succeed.
"""

from dataclasses import dataclass

import frappe

DEFAULT_CATALOGUE_DOCTYPE = "Item"
ALLOW_FIELD = "allow_medusa_catalogue_updates"

#: Cleared when Medusa says the product is gone.
LINK_FIELDS = ("medusa_product_id", "medusa_variant_id")

#: Event suffixes that mean "this no longer exists over there".
DELETE_SUFFIXES = (".deleted", ".removed", ".destroyed")

REASON_PROTECTED = "catalogue-protected"
REASON_UNLINKED = "catalogue-unlinked"


@dataclass(frozen=True)
class Verdict:
	"""`blocked` is the only thing a caller must act on. `reason` is for
	the log and for the result the far side reads."""

	blocked: bool
	reason: str | None = None
	document: str | None = None

	def as_result(self) -> dict:
		out = {"status": "skipped", "reason": self.reason}
		if self.document:
			out["name"] = self.document
		return out


def catalogue_doctype() -> str:
	"""What this site calls its products. Configurable, because not every
	installation sells Items."""
	from medusync import config

	try:
		return (config.settings().get("products_doctype") or "").strip() or DEFAULT_CATALOGUE_DOCTYPE
	except Exception:
		return DEFAULT_CATALOGUE_DOCTYPE


def updates_allowed() -> bool:
	from medusync import config

	try:
		return bool(config.settings().get(ALLOW_FIELD))
	except Exception:
		return False


def is_delete(event: str | None) -> bool:
	name = (event or "").lower()
	return any(name.endswith(suffix) for suffix in DELETE_SUFFIXES)


def find(doctype: str, key_field: str, key_value) -> str | None:
	"""The existing record this inbound event is about, if there is one."""
	if key_value in (None, ""):
		return None
	try:
		if not key_field or key_field == "name":
			return key_value if frappe.db.exists(doctype, key_value) else None
		return frappe.db.get_value(doctype, {key_field: key_value}, "name")
	except Exception:
		# An unknown key field is not a reason to let a write through.
		return None


def guard(doctype: str, key_field: str, key_value, event: str | None) -> Verdict:
	"""May this inbound event touch the catalogue?

	Answers `blocked=False` for everything that is not the catalogue, and
	for creating something that is not here yet — whether a Medusa-origin
	product may become a record at all is the plugin's product policy to
	decide, not this guard's.
	"""
	if not doctype or doctype != catalogue_doctype():
		return Verdict(False)

	existing = find(doctype, key_field, key_value)

	if is_delete(event):
		if existing:
			unlink(doctype, existing)
		return Verdict(True, REASON_UNLINKED, existing)

	if not existing:
		return Verdict(False)
	if updates_allowed():
		return Verdict(False)
	return Verdict(True, REASON_PROTECTED, existing)


def unlink(doctype: str, name: str) -> None:
	"""Forget the Medusa ids, keep the record.

	Deliberately not `disabled = 1`: a disabled Item cannot be received,
	invoiced or counted, so a storefront delete would quietly stop the
	warehouse working.
	"""
	meta = frappe.get_meta(doctype)
	updates = {field: None for field in LINK_FIELDS if meta.has_field(field)}
	if not updates:
		return
	frappe.db.set_value(doctype, name, updates, update_modified=False)
