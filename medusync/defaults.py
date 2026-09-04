# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""The mappings this app ships with, and what happens when they change.

A hard reset has to put *something* back, and "whatever was there before"
is not a definition. So the default set is data rather than a migration:
a fixed list, with identifiers derived from the name rather than
generated, which lets both systems agree on which mapping is which
without asking each other.

Three mappings, because three is what every store needs before anything
else is worth configuring: who the customers are, what is for sale, and
what was ordered. Everything past that is a per-project decision and
belongs in a mapping somebody writes.

Nothing ships switched on. A mapping goes live only after a rehearsal
that matches it (see medusync.studio), and that rule is what makes both
operations here safe: neither restoring nor upgrading changes what a site
*does* until a person has looked.

Two operations, and the difference between them is the whole point:

    restore_defaults()   put them back exactly as they ship. A reset.
    apply_defaults()     ship a NEWER set to a site that has been running.

The second is the hard one, because the brief is explicit that new
defaults must never auto-replace what somebody configured — which needs a
definition of "somebody configured it". There already is one: the
signature over what a mapping does, the same fingerprint the enable gate
and the studio use. `shipped_signature` records what a default looked like
when we wrote it. A default whose signature still matches has never been
touched and is ours to update. One that differs was edited, and the
upgrade says so instead of overwriting it.
"""

import frappe

from medusync import config
from medusync.attention import MAPPING_REQUIRED, clear, flag, notify_attention

#: Bump when the set below changes in a way an existing site should hear
#: about. Recorded on Medusync Settings so an upgrade can tell whether
#: this site has seen the current set.
DEFAULTS_VERSION = 1

#: Every default id starts with this. It is what tells a restore and an
#: upgrade which mappings they own and which belong to somebody else.
UID_PREFIX = "default:"

DEFAULT_CATALOGUE_DOCTYPE = "Item"


def _catalogue_doctype() -> str:
	"""What this site calls its products.

	Read from Settings rather than hard-coded, because a site that sells
	Assets or Services said so, and a default that lands on Item there is
	a default nobody can use.
	"""
	try:
		return (config.settings().get("products_doctype") or "").strip() or DEFAULT_CATALOGUE_DOCTYPE
	except Exception:
		return DEFAULT_CATALOGUE_DOCTYPE


def default_mappings() -> list[dict]:
	"""The set, as plain data.

	`fields` are (erpnext_field, medusa_path, direction) triples. A field
	direction of "From Medusa" on a cross-system id is deliberate: the id
	is assigned over there, so it can only ever travel one way.
	"""
	catalogue = _catalogue_doctype()
	catalogue_key = "item_code" if catalogue == DEFAULT_CATALOGUE_DOCTYPE else "name"
	return [
		{
			"uid": UID_PREFIX + "customer",
			"title": "Customers",
			"document_type": "Customer",
			"medusa_entity": "customer",
			"direction": "Two-way",
			"key_field": "email_id",
			"docevents": ["after_insert", "on_update"],
			"enabled": 0,
			"allow_insert": 1,
			"allow_update": 1,
			"allow_delete": 0,
			"fields": [
				("email_id", "email", "Two-way"),
				# One ERPNext name against two Medusa ones. Sending it is
				# useful; taking it back would have to guess where to split.
				("customer_name", "first_name", "To Medusa"),
				("medusa_customer_id", "id", "From Medusa"),
			],
		},
		{
			"uid": UID_PREFIX + "catalogue",
			"title": "Catalogue",
			"document_type": catalogue,
			"medusa_entity": "product",
			"direction": "To Medusa",
			"key_field": catalogue_key,
			"docevents": ["after_insert", "on_update"],
			"enabled": 0,
			"allow_insert": 0,
			"allow_update": 0,
			"allow_delete": 0,
			"fields": [
				(catalogue_key, "handle", "To Medusa"),
				("item_name", "title", "To Medusa"),
				("description", "description", "To Medusa"),
				("medusa_product_id", "id", "From Medusa"),
			],
		},
		{
			"uid": UID_PREFIX + "orders",
			"title": "Orders",
			"document_type": "Sales Order",
			"medusa_entity": "order",
			"direction": "From Medusa",
			"key_field": "medusa_order_id",
			# Nothing outbound: an order is Medusa's to place and ERPNext's
			# to fulfil, and what ERPNext has to say afterwards travels as
			# order metadata, not as a mapping.
			"docevents": [],
			"enabled": 0,
			"allow_insert": 1,
			"allow_update": 1,
			"allow_delete": 0,
			"fields": [
				("medusa_order_id", "id", "From Medusa"),
				("medusa_display_id", "display_id", "From Medusa"),
				# Which channel the order arrived through. Without this pair
				# the field exists and nothing ever writes to it, so every
				# web order reads as "medusa" instead of as itself.
				("medusa_order_source", "source", "From Medusa"),
				("medusa_payment_method", "payment_method", "From Medusa"),
				("medusa_payment_reference", "payment_reference", "From Medusa"),
			],
		},
	]


def _applicable(spec: dict) -> bool:
	"""A default for a DocType this site does not have is not a default."""
	return bool(spec.get("document_type")) and bool(
		frappe.db.exists("DocType", spec["document_type"])
	)


def _free_title(wanted: str, uid: str) -> str:
	"""A title nobody else is using.

	Mappings are named by their title, so a default whose name is already
	taken has to take another. Renaming the squatter would be worse: it is
	somebody's work, and a reset that renames it is a reset that lost it.
	"""
	existing = frappe.db.get_value(
		config.MAPPING_DOCTYPE, wanted, ["name", "mapping_uid"], as_dict=True
	)
	if not existing or existing.mapping_uid == uid:
		return wanted
	suffix = uid.replace(UID_PREFIX, "").replace("_", " ")
	candidate = f"{wanted} ({suffix})"
	index = 2
	while frappe.db.exists(config.MAPPING_DOCTYPE, candidate):
		candidate = f"{wanted} ({suffix} {index})"
		index += 1
	return candidate


def _write_spec(doc, spec: dict) -> None:
	"""Put the shipped shape onto a document, in memory."""
	doc.update(
		{
			"enabled": 0,
			"document_type": spec["document_type"],
			"medusa_entity": spec["medusa_entity"],
			"direction": spec["direction"],
			"key_field": spec["key_field"],
			"docevents": "\n".join(spec["docevents"]),
			"condition": None,
			"include_all_fields": 0,
			"allow_insert": spec.get("allow_insert", 0),
			"allow_update": spec.get("allow_update", 0),
			"allow_delete": spec.get("allow_delete", 0),
		}
	)
	doc.set("field_map", [])
	for erpnext_field, medusa_path, direction in spec["fields"]:
		doc.append(
			"field_map",
			{"frappe_field": erpnext_field, "medusa_path": medusa_path, "direction": direction},
		)
	# A default that has just been written has not been rehearsed as it now
	# stands, and the enable gate reads the signature rather than the flag,
	# so clearing it is what makes the gate tell the truth afterwards.
	doc.tested_signature = None
	doc.last_test_status = "Untested"
	doc.last_test_report = None


def spec_signature(spec: dict) -> str:
	"""What a mapping written from this spec would fingerprint as."""
	probe = frappe.new_doc(config.MAPPING_DOCTYPE)
	probe.title = "medusync-defaults-probe"
	_write_spec(probe, spec)
	return probe.test_signature()


def _stamp(doc) -> None:
	"""Record what we shipped, so a later upgrade can tell whether anybody
	has touched it since. Written straight to the column: it describes the
	document rather than changing it, and a save would bump the version."""
	frappe.db.set_value(
		config.MAPPING_DOCTYPE,
		doc.name,
		"shipped_signature",
		doc.test_signature(),
		update_modified=False,
	)


def _create(spec: dict):
	doc = frappe.new_doc(config.MAPPING_DOCTYPE)
	doc.title = _free_title(spec["title"], spec["uid"])
	doc.mapping_uid = spec["uid"]
	_write_spec(doc, spec)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	_stamp(doc)
	clear(doc.name)
	return doc


def restore_defaults(reason: str = "restore") -> dict:
	"""Put the defaults back exactly as they ship, switched off.

	Only mappings this set owns — the ones carrying a `default:` uid — are
	touched. A mapping somebody wrote is left alone, including one that
	happens to cover the same DocType: deciding it is redundant is not a
	restore's job.
	"""
	restored = []
	skipped = []
	for spec in default_mappings():
		if not _applicable(spec):
			skipped.append({"uid": spec["uid"], "reason": f"no DocType {spec['document_type']}"})
			continue

		name = frappe.db.get_value(config.MAPPING_DOCTYPE, {"mapping_uid": spec["uid"]}, "name")
		if not name:
			doc = _create(spec)
		else:
			doc = frappe.get_doc(config.MAPPING_DOCTYPE, name)
			_write_spec(doc, spec)
			doc.flags.ignore_permissions = True
			doc.save(ignore_permissions=True)
			_stamp(doc)
			clear(doc.name)
		restored.append({"uid": spec["uid"], "name": doc.name})

	frappe.db.set_single_value("Medusync Settings", "defaults_version", DEFAULTS_VERSION)
	frappe.clear_cache(doctype="Medusync Settings")
	return {"version": DEFAULTS_VERSION, "reason": reason, "mappings": restored, "skipped": skipped}


def apply_defaults(force: bool = False, reason: str = "upgrade") -> dict:
	"""Bring a running site up to the current default set.

	Never overwrites a default somebody has edited. `force` is the "Apply
	new defaults anyway" button: the operator has read the notification
	and decided.
	"""
	result = {
		"version": DEFAULTS_VERSION,
		"reason": reason,
		"created": [],
		"applied": [],
		"unchanged": [],
		"flagged": [],
		"skipped": [],
	}

	for spec in default_mappings():
		if not _applicable(spec):
			result["skipped"].append(
				{"uid": spec["uid"], "reason": f"no DocType {spec['document_type']}"}
			)
			continue

		name = frappe.db.get_value(config.MAPPING_DOCTYPE, {"mapping_uid": spec["uid"]}, "name")
		if not name:
			doc = _create(spec)
			result["created"].append({"uid": spec["uid"], "name": doc.name})
			continue

		doc = frappe.get_doc(config.MAPPING_DOCTYPE, name)
		current = doc.test_signature()
		shipped = doc.get("shipped_signature")
		if shipped:
			untouched = shipped == current
		else:
			# Installed by a version that did not record the fingerprint.
			# If it still matches what this set would produce then nobody
			# has touched it and we can adopt it. Otherwise we genuinely
			# cannot tell, and not overwriting is the safe answer.
			untouched = current == spec_signature(spec)

		if not (force or untouched):
			detail = frappe._(
				"This mapping was edited since it was installed, so the new default set has not "
				"been applied to it. Review the differences and either keep yours or use Apply "
				"New Defaults to take the shipped one."
			)
			if flag(doc.name, MAPPING_REQUIRED, detail):
				notify_attention(doc.name, MAPPING_REQUIRED, detail)
			result["flagged"].append({"uid": spec["uid"], "name": doc.name})
			continue

		_write_spec(doc, spec)
		if doc.test_signature() == current and not force:
			# Already exactly what we would have written. Stamp it so a
			# later upgrade knows, and say nothing.
			_stamp(doc)
			clear(doc.name)
			result["unchanged"].append({"uid": spec["uid"], "name": doc.name})
			continue

		doc.flags.ignore_permissions = True
		doc.save(ignore_permissions=True)
		_stamp(doc)
		clear(doc.name)
		result["applied"].append({"uid": spec["uid"], "name": doc.name})

	# The version is the site's claim to have the current set. It only
	# moves when nothing is outstanding, so a site with an edited default
	# keeps being asked rather than quietly counting as up to date.
	if not result["flagged"]:
		frappe.db.set_single_value("Medusync Settings", "defaults_version", DEFAULTS_VERSION)
		frappe.clear_cache(doctype="Medusync Settings")
	return result


@frappe.whitelist()
def apply_now(force: int | bool = False) -> dict:
	"""The "Apply New Defaults" button."""
	frappe.only_for("System Manager")
	return apply_defaults(force=bool(int(force or 0)), reason="operator")


def installed_version() -> int:
	try:
		return int(frappe.db.get_single_value("Medusync Settings", "defaults_version") or 0)
	except Exception:
		return 0


def owns(uid: str | None) -> bool:
	"""Is this one of ours? The question a restore and an upgrade both ask."""
	return bool(uid) and str(uid).startswith(UID_PREFIX)
