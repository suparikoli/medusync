# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Closing the inbound audit row must never take the request down.

`Medusync Log.document_name` is a Dynamic Link on `document_type`. The
handler-driven `receive()` path inserts the row with no document_type
(a handler may touch many doctypes), so writing a name into it used to
raise a LinkValidationError *after* the handler had committed — the
caller saw a 417 and retried forever.
"""

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import api


def _new_log(**overrides):
	log = frappe.new_doc("Medusync Log")
	log.update(
		{
			"direction": "Inbound",
			"status": "Queued",
			"event": "customer.synced",
			"event_id": "test:" + frappe.generate_hash(length=8),
		}
	)
	log.update(overrides)
	log.insert(ignore_permissions=True)
	return log


class TestCloseLog(IntegrationTestCase):
	def test_close_without_doctype_drops_the_name_instead_of_failing(self):
		log = _new_log()
		api._close(log, "Success", document_name="CUST-0001", action="updated")
		log.reload()
		self.assertEqual(log.status, "Success")
		self.assertFalse(log.document_name)
		self.assertEqual(log.action, "updated")

	def test_close_with_doctype_keeps_the_link(self):
		log = _new_log()
		api._close(log, "Success", document_type="DocType", document_name="Customer", action="updated")
		log.reload()
		self.assertEqual(log.document_type, "DocType")
		self.assertEqual(log.document_name, "Customer")

	def test_result_doctype_only_accepts_real_doctypes(self):
		self.assertEqual(api._result_doctype({"doctype": "Customer"}), "Customer")
		self.assertIsNone(api._result_doctype({"doctype": "No Such DocType"}))
		self.assertIsNone(api._result_doctype({}))
		self.assertIsNone(api._result_doctype(None))

	def test_close_survives_a_bad_select_value(self):
		# A status outside the Select vocabulary would fail validation; the
		# closer must degrade to a plain column write instead of raising.
		log = _new_log()
		api._close(log, "Success", action="not-a-real-action")
		log.reload()
		self.assertEqual(log.status, "Success")
