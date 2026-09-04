# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""A fresh install must end up with the same schema as an upgraded one.

`frappe.installer.install_app` marks every patch as completed before
`after_install` runs, so a patch that creates a custom field never runs on
the site that most needs it. `install.install_schema()` is the answer, and
the risk is that the next schema patch gets written and not added to it —
which nothing would notice until somebody installed on a new machine and
the first push failed.
"""

import pathlib

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import install
from medusync.patches.v1_1 import install_reference_fields


def _listed_patches():
	path = pathlib.Path(frappe.get_app_path("medusync")) / "patches.txt"
	for line in path.read_text(encoding="utf-8").splitlines():
		line = line.strip()
		if line and not line.startswith(("#", "[")):
			yield line


class TestFreshInstallSchema(IntegrationTestCase):
	def test_every_schema_patch_is_run_at_install_too(self):
		"""A patch whose name says it installs something must be here."""
		listed = [p for p in _listed_patches() if p.rsplit(".", 1)[-1].startswith("install_")]
		self.assertTrue(listed, "patches.txt lists no install_ patches — has it moved?")
		missing = [p for p in listed if p not in install.SCHEMA_PATCHES]
		self.assertEqual(
			missing,
			[],
			"these install schema but would not run on a fresh site — add them to "
			"install.SCHEMA_PATCHES: %s" % missing,
		)

	def test_each_one_resolves_and_is_callable(self):
		for path in install.SCHEMA_PATCHES:
			self.assertTrue(callable(frappe.get_attr(path + ".execute")), path)

	def test_the_reference_fields_are_on_this_site(self):
		"""What install_schema creates, checked against the live schema."""
		for doctype, specs in install_reference_fields.FIELDS.items():
			if not frappe.db.exists("DocType", doctype):
				continue
			for spec in specs:
				self.assertTrue(
					frappe.db.exists(
						"Custom Field", {"dt": doctype, "fieldname": spec["fieldname"]}
					),
					f"{doctype}.{spec['fieldname']} is missing",
				)

	def test_running_it_twice_changes_nothing(self):
		install.install_schema()
		before = frappe.db.count("Custom Field", {"fieldname": ["like", "medusa%"]})
		install.install_schema()
		self.assertEqual(frappe.db.count("Custom Field", {"fieldname": ["like", "medusa%"]}), before)
