# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

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
