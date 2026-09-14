# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""The connector adds nothing to any doctype, at install or ever.

Medusa ids live in Medusync Link and the sync decision in Medusync
Exclusion / Inclusion. An older site that still carries the fields has
their values moved into links and the fields dropped.
"""

import pathlib
from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import install

APP = pathlib.Path(frappe.get_app_path("medusync"))


def _listed_patches():
	for line in (APP / "patches.txt").read_text(encoding="utf-8").splitlines():
		line = line.strip()
		if line and not line.startswith(("#", "[")):
			yield line


class TestNothingIsAdded(IntegrationTestCase):
	def test_after_install_creates_no_custom_field(self):
		with patch("frappe.custom.doctype.custom_field.custom_field.create_custom_fields") as made:
			with patch("medusync.install.frappe.db.commit"):
				install.after_install()
		self.assertEqual(made.call_count, 0)

	def test_only_the_sync_tick_module_creates_a_custom_field(self):
		"""One field, from one place.

		Medusa ids and order details still live in Medusync Link, off the
		document. The single exception is the sync tick, which has to be a
		real field for a list column and Frappe's own bulk edit to work at
		all. Anything else reaching for `create_custom_fields` is the old
		habit coming back.
		"""
		offenders = [
			str(path.relative_to(APP))
			for path in APP.rglob("*.py")
			if "tests" not in path.parts
			and "create_custom_fields" in path.read_text(encoding="utf-8")
		]
		self.assertEqual(offenders, ["sync_field.py"])

	def test_the_tick_module_adds_the_tick_and_nothing_else(self):
		from medusync import sync_field

		with patch("medusync.sync_field.frappe.get_meta") as meta:
			meta.return_value.get_field.return_value = None
			meta.return_value.fields = [frappe._dict(fieldname="item_code")]
			specs = sync_field._field_specs("Item")

		self.assertEqual(
			[(f["fieldname"], f["fieldtype"]) for f in specs],
			[("medusync_tab", "Tab Break"), ("medusync_sync", "Check")],
		)

	def test_upgrading_sites_have_their_fields_moved_into_links(self):
		listed = list(_listed_patches())
		self.assertIn("medusync.patches.v1_7.move_reference_fields_to_links", listed)
		for path in listed:
			self.assertTrue(callable(frappe.get_attr(path + ".execute")), path)
