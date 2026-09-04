# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Try the mapping before you trust it.

A mapping is a small program somebody wrote in a form, and until now the
only way to find out what it did was to save a real document and read the
log afterwards. That is a poor place to discover that a field name is
wrong, and a worse one to discover that a condition excludes everything.

So: a sample of what a record on this side actually looks like, and a
dry run in either direction that reports exactly what would happen and
changes nothing at all. "Changes nothing" is the load-bearing claim here
and most of these tests exist to hold it.
"""

from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import studio


class StudioCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		# Saving a mapping tells the connected stores about it. These tests
		# are about the studio, not the wire.
		self._push = patch("medusync.mapping_sync.push_mapping", return_value=None)
		self._push.start()
		self._mappings = []

	def tearDown(self):
		for name in self._mappings:
			if frappe.db.exists("Medusync Mapping", name):
				frappe.delete_doc("Medusync Mapping", name, force=1, ignore_permissions=True)
		self._push.stop()
		super().tearDown()

	def _mapping(self, **over):
		spec = {
			"title": "Studio test mapping",
			"enabled": 0,
			"document_type": "Customer",
			"direction": "Two-way",
			"docevents": "on_update",
			"key_field": "customer_name",
			"include_all_fields": 0,
			"allow_insert": 1,
			"allow_update": 1,
			"allow_delete": 0,
		}
		spec.update(over)
		field_map = spec.pop("field_map", [{"frappe_field": "customer_name", "medusa_path": "name"}])
		doc = frappe.new_doc("Medusync Mapping")
		doc.update(spec)
		for row in field_map:
			doc.append("field_map", row)
		doc.insert(ignore_permissions=True)
		self._mappings.append(doc.name)
		return doc


class TestTheSample(StudioCase):
	def test_a_doctype_with_records_shows_a_real_one(self):
		# Synthesised values are a fallback, not a preference: a real record
		# is the only thing that shows the shapes an operator will actually
		# meet, including the empty fields.
		sample = studio.sample_for("Customer")
		self.assertTrue(sample["from_record"])
		self.assertTrue(sample["name"])
		self.assertIn("customer_name", sample["data"])

	def test_a_named_record_can_be_asked_for(self):
		name = frappe.get_all("Customer", fields=["name"], limit=1)[0].name
		sample = studio.sample_for("Customer", name=name)
		self.assertEqual(sample["name"], name)
		self.assertEqual(sample["data"]["name"], name)

	def test_an_empty_doctype_is_still_worth_showing(self):
		# A brand-new mapping is exactly when a sample is most useful, and
		# exactly when there may be nothing to sample.
		with patch("medusync.studio._latest", return_value=None):
			sample = studio.sample_for("Customer")
		self.assertFalse(sample["from_record"])
		self.assertIn("customer_name", sample["data"])
		self.assertIn("doctype", sample["data"])

	def test_a_synthesised_value_suits_its_field(self):
		with patch("medusync.studio._latest", return_value=None):
			data = studio.sample_for("Customer")["data"]
		self.assertIsInstance(data["customer_name"], str)
		# A Select offers its own first option rather than free text.
		self.assertIn(data["customer_type"], ("Company", "Individual"))

	def test_a_doctype_nobody_has_is_refused_rather_than_guessed(self):
		with self.assertRaises(frappe.DoesNotExistError):
			studio.sample_for("No Such DocType At All")

	def test_the_picker_lists_fields_with_the_value_it_saw(self):
		fields = studio.fields_of("Customer")
		by_name = {f["fieldname"]: f for f in fields}
		self.assertIn("customer_name", by_name)
		self.assertEqual(by_name["customer_name"]["fieldtype"], "Data")
		self.assertIn("sample", by_name["customer_name"])
		# `name` is not in meta.fields but every mapping can key on it.
		self.assertIn("name", by_name)

	def test_the_picker_leaves_out_what_cannot_be_mapped(self):
		names = {f["fieldname"] for f in studio.fields_of("Customer")}
		self.assertNotIn("column_break_1", names)
		for field in studio.fields_of("Customer"):
			self.assertNotIn(field["fieldtype"], ("Section Break", "Column Break", "Tab Break"))


class TestTheOutboundDryRun(StudioCase):
	def test_it_reports_the_payload_that_would_travel(self):
		mapping = self._mapping()
		report = studio.dry_run_outbound(mapping.name)
		self.assertTrue(report["ok"])
		self.assertIn("customer_name", report["payload"])
		self.assertIn("name", report["payload"])

	def test_it_names_the_events_that_would_fire(self):
		mapping = self._mapping(docevents="after_insert\non_update")
		report = studio.dry_run_outbound(mapping.name)
		self.assertEqual(sorted(report["events"]), ["customer.created", "customer.updated"])

	def test_it_says_which_stores_would_receive_it_and_why_not(self):
		mapping = self._mapping()
		report = studio.dry_run_outbound(mapping.name)
		self.assertTrue(report["sites"])
		for site in report["sites"]:
			self.assertIn("site_id", site)
			self.assertIn("allowed", site)
			if not site["allowed"]:
				self.assertTrue(site["reason"])

	def test_a_condition_that_excludes_everything_is_reported_not_hidden(self):
		# The commonest silent failure: a mapping that is enabled, correct,
		# and never fires.
		mapping = self._mapping(condition="doc.get('customer_name') == 'nobody at all'")
		report = studio.dry_run_outbound(mapping.name)
		self.assertFalse(report["condition_passes"])
		self.assertTrue(report["ok"])  # not an error — an answer

	def test_a_broken_condition_is_an_error_with_its_reason(self):
		mapping = self._mapping()
		frappe.db.set_value("Medusync Mapping", mapping.name, "condition", "doc[", update_modified=False)
		frappe.clear_cache(doctype="Medusync Mapping")
		report = studio.dry_run_outbound(mapping.name)
		self.assertFalse(report["condition_passes"])

	def test_a_mapping_medusa_owns_has_nothing_to_send(self):
		mapping = self._mapping(direction="From Medusa", docevents="")
		report = studio.dry_run_outbound(mapping.name)
		self.assertFalse(report["ok"])
		self.assertIn("From Medusa", report["message"])

	def test_it_writes_no_log_row(self):
		mapping = self._mapping()
		before = frappe.db.count("Medusync Log")
		studio.dry_run_outbound(mapping.name)
		self.assertEqual(frappe.db.count("Medusync Log"), before)


class TestTheInboundDryRun(StudioCase):
	def _sample(self, **over):
		data = {"name": "Studio Sample Customer", "customer_name": "Studio Sample Customer"}
		data.update(over)
		return data

	def test_an_unknown_record_would_be_created(self):
		mapping = self._mapping()
		report = studio.dry_run_inbound(mapping.name, self._sample())
		self.assertTrue(report["ok"])
		self.assertEqual(report["action"], "created")
		self.assertIsNone(report["existing"])

	def test_a_known_record_would_be_updated_and_the_diff_is_shown(self):
		existing = frappe.get_all("Customer", fields=["name", "customer_name"], limit=1)[0]
		# Keyed on the id, so changing the name is an edit rather than a
		# different customer. Keyed on customer_name it would be the latter,
		# which is a real distinction and not this test's subject.
		mapping = self._mapping(
			key_field="name",
			field_map=[{"frappe_field": "customer_name", "medusa_path": "display_name"}],
		)
		report = studio.dry_run_inbound(
			mapping.name, {"name": existing.name, "display_name": "A Different Name"}
		)
		self.assertEqual(report["action"], "updated")
		self.assertEqual(report["existing"], existing.name)
		self.assertEqual(
			report["changes"]["customer_name"],
			{"before": existing.customer_name, "after": "A Different Name"},
		)

	def test_a_field_that_is_not_changing_is_not_in_the_diff(self):
		existing = frappe.get_all("Customer", fields=["name", "customer_name"], limit=1)[0]
		mapping = self._mapping(
			key_field="name",
			field_map=[{"frappe_field": "customer_name", "medusa_path": "display_name"}],
		)
		report = studio.dry_run_inbound(
			mapping.name, {"name": existing.name, "display_name": existing.customer_name}
		)
		self.assertEqual(report["changes"], {})

	def test_a_mapping_that_forbids_creates_says_so(self):
		mapping = self._mapping(allow_insert=0)
		report = studio.dry_run_inbound(mapping.name, self._sample())
		self.assertEqual(report["action"], "skipped")
		self.assertIn("create", report["reason"])

	def test_a_field_the_map_does_not_carry_inbound_is_dropped(self):
		mapping = self._mapping(
			field_map=[
				{"frappe_field": "customer_name", "medusa_path": "name"},
				{"frappe_field": "customer_type", "medusa_path": "kind", "direction": "To Medusa"},
			]
		)
		report = studio.dry_run_inbound(
			mapping.name, {"name": "Whoever", "kind": "Company", "customer_name": "Whoever"}
		)
		self.assertIn("customer_name", report["payload"])
		self.assertNotIn("customer_type", report["payload"])

	def test_identity_and_workflow_never_arrive_from_the_wire(self):
		mapping = self._mapping(include_all_fields=1)
		report = studio.dry_run_inbound(
			mapping.name,
			{"name": "Whoever", "customer_name": "Whoever", "docstatus": 1, "owner": "hacker@x.io"},
		)
		self.assertNotIn("docstatus", report["payload"])
		self.assertNotIn("owner", report["payload"])

	def test_a_mapping_this_site_sends_only_has_nothing_to_receive(self):
		mapping = self._mapping(direction="To Medusa")
		report = studio.dry_run_inbound(mapping.name, self._sample())
		self.assertFalse(report["ok"])
		self.assertIn("To Medusa", report["message"])

	def test_the_catalogue_guard_answers_here_too(self):
		# What the studio reports and what the receiver does must be the
		# same decision, or the studio is worse than nothing.
		item = frappe.get_all("Item", fields=["name"], limit=1)[0].name
		mapping = self._mapping(
			document_type="Item",
			key_field="item_code",
			field_map=[{"frappe_field": "item_name", "medusa_path": "title"}],
		)
		report = studio.dry_run_inbound(
			mapping.name, {"item_code": item, "title": "Renamed by the studio"}
		)
		self.assertEqual(report["action"], "skipped")
		self.assertEqual(report["reason"], "catalogue-protected")

	def test_it_creates_nothing_and_changes_nothing(self):
		existing = frappe.get_all("Customer", fields=["name", "customer_name"], limit=1)[0]
		mapping = self._mapping(
			key_field="name",
			field_map=[{"frappe_field": "customer_name", "medusa_path": "display_name"}],
		)
		before_count = frappe.db.count("Customer")
		before_logs = frappe.db.count("Medusync Log")
		studio.dry_run_inbound(mapping.name, {"name": existing.name, "display_name": "Nope"})
		studio.dry_run_inbound(mapping.name, self._sample())
		self.assertEqual(frappe.db.count("Customer"), before_count)
		self.assertEqual(frappe.db.count("Medusync Log"), before_logs)
		self.assertEqual(
			frappe.db.get_value("Customer", existing.name, "customer_name"), existing.customer_name
		)

	def test_a_document_the_data_would_not_satisfy_reports_the_complaint(self):
		# The whole point of running validate: a payload that translates
		# cleanly can still be refused by the doctype, and finding that out
		# from a rehearsal beats finding it out from a queue of 5xxs.
		existing = frappe.get_all("Customer", fields=["name"], limit=1)[0].name
		mapping = self._mapping(
			field_map=[
				{"frappe_field": "customer_name", "medusa_path": "name"},
				{"frappe_field": "customer_type", "medusa_path": "kind"},
			]
		)
		report = studio.dry_run_inbound(
			mapping.name,
			{"name": existing, "customer_name": "Whoever", "kind": "Not A Real Type"},
		)
		self.assertTrue(report["errors"])
		self.assertTrue(any("Not A Real Type" in e for e in report["errors"]))

	def test_a_payload_the_doctype_accepts_reports_no_complaint(self):
		existing = frappe.get_all("Customer", fields=["name", "customer_name"], limit=1)[0]
		mapping = self._mapping(
			key_field="name",
			field_map=[{"frappe_field": "customer_name", "medusa_path": "display_name"}],
		)
		report = studio.dry_run_inbound(
			mapping.name, {"name": existing.name, "display_name": existing.customer_name}
		)
		self.assertEqual(report["errors"], [])
