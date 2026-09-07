# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

import hashlib
import json

import frappe
from frappe.model.document import Document

VALID_DOCEVENTS = {
	"after_insert",
	"on_update",
	"on_submit",
	"on_cancel",
	"on_trash",
	"on_update_after_submit",
}


class MedusyncMapping(Document):
	def validate(self):
		self.validate_pair()
		self.validate_docevents()
		self.validate_condition()
		self.validate_field_map()
		if not self.key_field:
			self.key_field = "name"
		self.stamp_identity()
		self.gate_enable()

	def test_signature(self) -> str:
		"""A fingerprint of what this mapping DOES.

		Not the version, which changes on every save including one that
		only ticks a checkbox; not the title, which changes nothing. Just
		the parts that decide the outcome — so a rehearsal survives the
		mapping being switched on, and does not survive somebody adding a
		field to it afterwards.
		"""
		shape = {
			"document_type": self.document_type,
			"medusa_entity": self.get("medusa_entity"),
			"direction": self.direction,
			"key_field": self.key_field or "name",
			"include_all_fields": int(self.include_all_fields or 0),
			"condition": (self.condition or "").strip(),
			"docevents": sorted(self.docevent_list()),
			"medusa_event": self.medusa_event or None,
			"allow_insert": int(self.allow_insert or 0),
			"allow_update": int(self.allow_update or 0),
			"allow_delete": int(self.allow_delete or 0),
			"fields": sorted(
				[
					row.frappe_field or "",
					row.medusa_path or "",
					row.direction or "",
					# A fixed value is behaviour: changing "Products" to
					# "Raw Material" writes something the rehearsal never
					# checked. Prefixed so a constant is never mistaken for
					# a path of the same text.
					("=" + row.constant_value) if row.get("constant_value") else "",
				]
				for row in (self.field_map or [])
			),
		}
		return hashlib.sha256(
			json.dumps(shape, sort_keys=True, default=str).encode("utf-8")
		).hexdigest()

	def gate_enable(self):
		"""Switching a mapping ON requires a rehearsal that matches it.

		Only the transition is gated. A mapping that is already running
		keeps running, whatever is edited on it — retro-fitting the rule
		would stop a working site on the next save of anything, which is
		not a safety improvement.
		"""
		if not self.enabled:
			return
		if not self.is_new() and int(self.get_db_value("enabled") or 0):
			return
		if self.get("tested_signature") and self.tested_signature == self.test_signature():
			return
		if self.flags.get("medusync_applying"):
			# The other side asked for this. Same rule as first contact:
			# nothing runs here until somebody here has looked at it. An
			# exception would turn the inbound apply into a 5xx and a retry
			# loop, so decline quietly and keep the rest of the edit.
			self.enabled = 0
			self.last_test_status = self.last_test_status or "Untested"
			return
		frappe.throw(
			frappe._(
				"Rehearse this mapping before switching it on. Use <b>Test</b> in the toolbar: "
				"it shows what would be sent and what an arriving payload would do, writes "
				"nothing, and enables the mapping if it held up."
			),
			title=frappe._("Not rehearsed yet"),
		)

	def validate_pair(self):
		"""A sync is its pair: one Medusa entity, one DocType, one store.

		Two mappings for the same pair are how one sync came to look like
		two, with each side holding a different half. The pair is also the
		identity the two systems share, so it cannot change on an existing
		mapping: a different pair is a different sync.
		"""
		from medusync import mapping_sync

		entity = self.get("medusa_entity") or None
		site = self.get("site") or None
		if not self.is_new():
			before = self.get_doc_before_save()
			if before and (
				before.document_type,
				before.get("medusa_entity") or None,
				before.get("site") or None,
			) != (self.document_type, entity, site):
				frappe.throw(
					frappe._(
						"A sync is identified by what it pairs. To keep a different Document Type, "
						"entity or store in step, add a new sync instead of changing this one."
					),
					title=frappe._("The pair cannot change"),
				)
		other = mapping_sync.find_by_pair(entity, self.document_type, site, exclude=self.name)
		if other:
			title = frappe.db.get_value("Medusync Mapping", other, "title") or other
			frappe.throw(
				frappe._(
					"{0} already keeps {1} in step with the store's {2}. Edit that sync rather than "
					"adding a second one for the same pair."
				).format(frappe.bold(title), frappe.bold(self.document_type), frappe.bold(entity or "records")),
				title=frappe._("One sync per pair"),
			)

	def stamp_identity(self):
		"""Give the mapping the id both systems share, and a version that
		says whose copy is newer.

		The id is derived from the pair (see mapping_sync.pair_uid), so the
		Medusa side arrives at the same one on its own. `version` orders the
		two copies. A save that is APPLYING a change from the other side
		carries its version already and must not bump it, or the two sides
		would ratchet each other upward forever.
		"""
		from medusync import mapping_sync

		# Always, not only when empty: a row installed before the identity
		# was the pair's converges on its next save, and the other side
		# resolves by pair anyway.
		self.mapping_uid = mapping_sync.pair_uid_of(self)
		if self.flags.get("medusync_applying"):
			self.version = int(self.version or 1)
			return
		if self.is_new():
			self.version = 1
		else:
			self.version = int(self.version or 1) + 1

	def validate_docevents(self):
		"""Reject unknown docevents at save time.

		A typo here fails silently otherwise: the wildcard hook only ever
		looks up events it was actually called with, so `on_updates` would
		simply never fire and the operator would be left wondering why
		nothing syncs.
		"""
		events = self.docevent_list()
		unknown = [e for e in events if e not in VALID_DOCEVENTS]
		if unknown:
			frappe.throw(
				"Unknown document event(s): {0}. Valid values are: {1}".format(
					", ".join(unknown), ", ".join(sorted(VALID_DOCEVENTS))
				)
			)
		if self.direction != "From Medusa" and not events:
			frappe.throw("Pick at least one document event to trigger the outbound sync.")

	def validate_condition(self):
		"""Compile the condition now rather than at fire time.

		The condition runs inside a document save; a SyntaxError there
		would surface as a failed save on an unrelated form.
		"""
		if not self.condition:
			return
		try:
			compile(self.condition.strip(), "<medusync-condition>", "eval")
		except SyntaxError as exc:
			frappe.throw(f"Condition is not a valid Python expression: {exc}")

	def validate_field_map(self):
		if self.include_all_fields or not self.field_map:
			return
		meta = frappe.get_meta(self.document_type)
		valid = {df.fieldname for df in meta.fields}
		valid.update({"name", "owner", "creation", "modified", "docstatus"})
		for row in self.field_map:
			if row.frappe_field not in valid:
				frappe.throw(
					f"Row {row.idx}: '{row.frappe_field}' is not a field on {self.document_type}."
				)
			if row.get("constant_value"):
				# A fixed value has no counterpart in the store. Filling the
				# path in from the fieldname would claim Medusa has a field
				# called `item_group`, and the outbound payload would then
				# advertise one.
				row.medusa_path = None
				continue
			if not row.medusa_path:
				row.medusa_path = row.frappe_field

	def docevent_list(self) -> list[str]:
		"""Trigger events as a clean list. Stored as one-per-line text so
		the form stays legible without a child table."""
		raw = self.docevents or ""
		return [line.strip() for line in raw.replace(",", "\n").splitlines() if line.strip()]

	def resolved_event_name(self, docevent: str) -> str:
		"""Event name Medusa will dispatch on.

		An explicit `medusa_event` wins. Otherwise derive a predictable
		one: Sales Invoice + after_insert -> sales_invoice.created.
		"""
		if self.medusa_event:
			return self.medusa_event
		slug = frappe.scrub(self.document_type)
		suffix = {
			"after_insert": "created",
			"on_update": "updated",
			"on_submit": "submitted",
			"on_cancel": "cancelled",
			"on_trash": "deleted",
			"on_update_after_submit": "updated",
		}.get(docevent, docevent)
		return f"{slug}.{suffix}"
