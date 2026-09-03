# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Loop prevention that survives leaving the request.

`frappe.flags.medusync_inbound` stops the echo while the inbound write is
still in flight. It cannot stop the echo a background worker sends a
moment later, so an inbound apply also leaves a short-lived breadcrumb:
"this document was last touched by correlation C". Anything the outbound
side sends about that document is stamped `echo_of`, and the far side
drops what it recognises as its own.
"""

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import echo


class TestEchoBreadcrumb(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		echo.forget("Customer", "ECHO-TEST-1")

	def tearDown(self):
		echo.forget("Customer", "ECHO-TEST-1")
		super().tearDown()

	def test_no_breadcrumb_means_a_locally_originated_change(self):
		self.assertIsNone(echo.origin_of("Customer", "ECHO-TEST-1"))

	def test_remember_then_read_back(self):
		echo.remember("Customer", "ECHO-TEST-1", correlation_id="corr-1", origin="medusa:site-a")
		mark = echo.origin_of("Customer", "ECHO-TEST-1")
		self.assertEqual(mark["correlation_id"], "corr-1")
		self.assertEqual(mark["origin"], "medusa:site-a")

	def test_forget_clears_it(self):
		echo.remember("Customer", "ECHO-TEST-1", correlation_id="corr-1", origin="medusa:site-a")
		echo.forget("Customer", "ECHO-TEST-1")
		self.assertIsNone(echo.origin_of("Customer", "ECHO-TEST-1"))

	def test_the_breadcrumb_is_per_document(self):
		echo.remember("Customer", "ECHO-TEST-1", correlation_id="corr-1", origin="medusa:site-a")
		self.assertIsNone(echo.origin_of("Customer", "ECHO-TEST-2"))
		self.assertIsNone(echo.origin_of("Item", "ECHO-TEST-1"))

	def test_an_inbound_write_marks_the_document_it_touched(self):
		# What api.receive does around a mapped upsert.
		with echo.inbound_context(correlation_id="corr-9", origin="medusa:site-a"):
			self.assertTrue(frappe.flags.get("medusync_inbound"))
			echo.mark_touched("Customer", "ECHO-TEST-1")
		self.assertFalse(frappe.flags.get("medusync_inbound"))
		mark = echo.origin_of("Customer", "ECHO-TEST-1")
		self.assertEqual(mark["correlation_id"], "corr-9")
		self.assertEqual(mark["origin"], "medusa:site-a")

	def test_the_flag_is_restored_even_when_the_write_raises(self):
		frappe.flags.medusync_inbound = False
		with self.assertRaises(ValueError):
			with echo.inbound_context(correlation_id="c", origin="medusa:s"):
				raise ValueError("boom")
		self.assertFalse(frappe.flags.get("medusync_inbound"))
