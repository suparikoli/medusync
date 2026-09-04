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
				[row.frappe_field or "", row.medusa_path or "", row.direction or ""]
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

	def stamp_identity(self):
		"""Give the mapping an id both systems share, and a version that
		says whose copy is newer.

		The same mapping exists on the Medusa side; edits can start from
		either. `mapping_uid` pairs the two copies and `version` orders
		them. A save that is APPLYING a change from the other side carries
		its version already and must not bump it, or the two sides would
		ratchet each other upward forever.
		"""
		if not self.mapping_uid:
			self.mapping_uid = frappe.generate_hash(length=32)
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
