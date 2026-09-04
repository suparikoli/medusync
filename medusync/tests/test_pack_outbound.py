# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Handler packs are opt-in in BOTH directions.

`hooks.py` names no business doctype any more. It binds one wildcard
handler for the six document events; that handler asks the site's
configured packs which of their outbound hooks apply. A site running no
pack runs no domain code, inbound or outbound.
"""

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import handlers
from medusync.handlers import outbound_guard
from medusync.tests import probe_pack

CONF_KEY = handlers.CONF_KEY


class TestPackOutboundHooks(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._saved = frappe.local.conf.get(CONF_KEY, "__absent__")
		handlers.clear()

	def tearDown(self):
		if self._saved == "__absent__":
			frappe.local.conf.pop(CONF_KEY, None)
		else:
			frappe.local.conf[CONF_KEY] = self._saved
		handlers.clear()
		super().tearDown()

	def _set(self, value):
		if value is None:
			frappe.local.conf.pop(CONF_KEY, None)
		else:
			frappe.local.conf[CONF_KEY] = value

	def test_hooks_py_names_no_business_doctype(self):
		from medusync import hooks

		configured = set(hooks.doc_events) - {"*", "Medusync Mapping", "Medusync Site"}
		self.assertEqual(configured, set(), f"hooks.py still hardcodes: {sorted(configured)}")

	def test_a_configured_pack_supplies_its_outbound_hooks(self):
		self._set(["commerce"])
		fns = handlers.outbound_hooks_for("Item Price", "on_update")
		self.assertEqual(len(fns), 1)
		self.assertEqual(fns[0].__module__, "medusync.handlers.commerce.pricing")

	def test_no_pack_means_no_outbound_domain_code(self):
		self._set([])
		self.assertEqual(handlers.outbound_hooks_for("Item Price", "on_update"), [])
		self.assertEqual(handlers.outbound_hooks_for("Stock Ledger Entry", "after_insert"), [])
		self.assertEqual(handlers.outbound_hooks_for("Delivery Note", "on_submit"), [])

	def test_a_pack_that_declares_none_contributes_none(self):
		"""A pack may only listen. It must not be made to contribute a
		hook, and its silence must not disturb what other packs said."""
		with probe_pack.installed():
			self._set(["probe"])
			self.assertEqual(handlers.outbound_hooks_for("Item Price", "on_update"), [])
			self._set(["probe", "commerce"])
			self.assertEqual(len(handlers.outbound_hooks_for("Item Price", "on_update")), 1)

	def test_unrelated_doctypes_and_events_match_nothing(self):
		self._set(["commerce"])
		self.assertEqual(handlers.outbound_hooks_for("User", "on_update"), [])
		self.assertEqual(handlers.outbound_hooks_for("Item Price", "on_submit"), [])

	def test_every_declared_hook_resolves_to_a_real_function(self):
		self._set(["commerce"])
		seen = 0
		for doctype, events in handlers.outbound_hook_map().items():
			for event, fns in events.items():
				for fn in fns:
					self.assertTrue(callable(fn), f"{doctype}.{event} is not callable")
					seen += 1
		self.assertGreater(seen, 0)

	def test_the_dispatcher_runs_the_configured_hooks_only(self):
		self._set(["commerce"])
		calls = []

		def spy(doc, method=None):
			calls.append((doc.doctype, method))

		original = handlers.outbound_hooks_for
		handlers.outbound_hooks_for = lambda dt, ev: [spy] if dt == "Item Price" else []
		try:
			doc = frappe._dict({"doctype": "Item Price", "name": "X"})
			handlers.run_outbound_hooks(doc, "on_update")
			other = frappe._dict({"doctype": "User", "name": "Y"})
			handlers.run_outbound_hooks(other, "on_update")
		finally:
			handlers.outbound_hooks_for = original
		self.assertEqual(calls, [("Item Price", "on_update")])

	def test_a_raising_hook_never_aborts_the_users_save(self):
		def boom(doc, method=None):
			raise RuntimeError("hook exploded")

		original = handlers.outbound_hooks_for
		handlers.outbound_hooks_for = lambda dt, ev: [boom]
		try:
			doc = frappe._dict({"doctype": "Item Price", "name": "X"})
			handlers.run_outbound_hooks(doc, "on_update")  # must not raise
		finally:
			handlers.outbound_hooks_for = original

	def test_a_failing_hook_cannot_re_enter_the_dispatcher(self):
		"""Reporting a failure writes an Error Log, which fires the same
		wildcard hook. Without a guard that recurses until the worker
		dies, so the second entry must be refused."""
		depth = {"max": 0, "now": 0}

		def reentrant(doc, method=None):
			depth["now"] += 1
			depth["max"] = max(depth["max"], depth["now"])
			try:
				# What frappe.log_error does: insert a document, which
				# fires the wildcard hook again.
				handlers.run_outbound_hooks(frappe._dict({"doctype": "Item Price", "name": "X"}), method)
			finally:
				depth["now"] -= 1

		original = handlers.outbound_hooks_for
		handlers.outbound_hooks_for = lambda dt, ev: [reentrant]
		try:
			handlers.run_outbound_hooks(frappe._dict({"doctype": "Item Price", "name": "X"}), "on_update")
		finally:
			handlers.outbound_hooks_for = original
		self.assertEqual(depth["max"], 1)
		self.assertFalse(outbound_guard.already_running())

	def test_frappes_own_bookkeeping_is_never_a_business_change(self):
		calls = []
		original = handlers.outbound_hooks_for
		handlers.outbound_hooks_for = lambda dt, ev: [lambda doc, method=None: calls.append(dt)]
		try:
			for doctype in ("Error Log", "Version", "Scheduled Job Log"):
				handlers.run_outbound_hooks(frappe._dict({"doctype": doctype, "name": "X"}), "after_insert")
			handlers.run_outbound_hooks(frappe._dict({"doctype": "Item Price", "name": "X"}), "after_insert")
		finally:
			handlers.outbound_hooks_for = original
		self.assertEqual(calls, ["Item Price"])
