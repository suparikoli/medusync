# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Backfilling a doctype that only syncs what somebody chose.

`dispatch` refuses an unchosen document, so nothing wrong ever reached a
store. But reading every row of a 60,000-record table to deliver three of
them costs minutes, and the summary counted those reads as if they had
been sent — which is the number an operator reads to decide whether the
sync worked.
"""

from unittest.mock import patch

import frappe

try:
	from frappe.tests import IntegrationTestCase
except ImportError:  # pragma: no cover - older frappe
	from frappe.tests.utils import FrappeTestCase as IntegrationTestCase

from medusync import backfill, selection

MAPPING = "Medusync Mapping"


class BackfillSelectionCase(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._push = patch("medusync.mapping_sync.push_mapping", return_value=None)
		self._push.start()
		self.addCleanup(self._push.stop)
		site = frappe.get_all("Medusync Site", fields=["site_id"], limit=1)
		if not site:
			self.skipTest("no Medusync Site configured")
		self.site = site[0].site_id
		self.mapping = self._mapping()

	def _mapping(self):
		doc = frappe.new_doc(MAPPING)
		doc.update(
			{
				"title": "Backfill selection probe %s" % frappe.generate_hash(length=5),
				"enabled": 0,
				"document_type": "Customer",
				"direction": "To Medusa",
				"docevents": "on_update",
				"key_field": "name",
			}
		)
		doc.append("field_map", {"frappe_field": "customer_name", "medusa_path": "name"})
		doc.insert(ignore_permissions=True)
		frappe.db.set_value(MAPPING, doc.name, "enabled", 1, update_modified=False)
		doc.reload()
		self.addCleanup(
			lambda: frappe.db.exists(MAPPING, doc.name)
			and frappe.delete_doc(MAPPING, doc.name, force=1, ignore_permissions=True)
		)
		return doc


class TestItOnlyReadsWhatWasChosen(BackfillSelectionCase):
	def test_a_chosen_doctype_enumerates_the_chosen_ones_not_the_table(self):
		with patch.object(selection, "mode_of", return_value=selection.MODE_ONLY_CHOSEN), patch.object(
			backfill, "_chosen_names", return_value=["CUST-A", "CUST-B"]
		), patch("frappe.get_all") as get_all, patch("frappe.get_doc") as get_doc, patch.object(
			selection, "sites_allowed", return_value=[{"site_id": self.site}]
		), patch(
			"medusync.outbound._condition_passes", return_value=True
		), patch(
			"medusync.outbound.dispatch"
		) as dispatch:
			result = backfill.run(mapping=self.mapping.name)

		# The whole point: the table was never enumerated.
		self.assertEqual(result["matched"], 2)
		self.assertEqual(result["sent"], 2)
		self.assertEqual(dispatch.call_count, 2)
		self.assertFalse(
			any(call.args and call.args[0] == "Customer" for call in get_all.call_args_list),
			"the Customer table should not be listed when the chosen set is known",
		)
		# One fetch for the mapping itself, then one per chosen record.
		self.assertEqual(get_doc.call_count, 3)

	def test_an_unrestricted_doctype_still_walks_the_table(self):
		with patch.object(selection, "mode_of", return_value=None), patch(
			"frappe.get_all", return_value=["C1", "C2", "C3"]
		), patch("frappe.get_doc"), patch.object(
			selection, "sites_allowed", return_value=[{"site_id": self.site}]
		), patch(
			"medusync.outbound._condition_passes", return_value=True
		), patch(
			"medusync.outbound.dispatch"
		):
			result = backfill.run(mapping=self.mapping.name)
		self.assertEqual(result["matched"], 3)
		self.assertEqual(result["sent"], 3)


class TestTheSummarySaysWhatLeft(BackfillSelectionCase):
	def test_a_document_no_store_may_receive_is_not_counted_as_sent(self):
		with patch.object(selection, "mode_of", return_value=None), patch(
			"frappe.get_all", return_value=["C1", "C2"]
		), patch("frappe.get_doc"), patch.object(selection, "sites_allowed", return_value=[]), patch(
			"medusync.outbound._condition_passes", return_value=True
		), patch(
			"medusync.outbound.dispatch"
		) as dispatch:
			result = backfill.run(mapping=self.mapping.name)

		self.assertEqual(result["sent"], 0, "nothing reached a store")
		self.assertEqual(result["skipped_not_selected"], 2)
		dispatch.assert_not_called()

	def test_the_condition_and_the_selection_are_counted_apart(self):
		with patch.object(selection, "mode_of", return_value=None), patch(
			"frappe.get_all", return_value=["C1", "C2"]
		), patch("frappe.get_doc"), patch.object(
			selection, "sites_allowed", side_effect=[[{"site_id": self.site}]]
		), patch(
			"medusync.outbound._condition_passes", side_effect=[False, True]
		), patch(
			"medusync.outbound.dispatch"
		):
			result = backfill.run(mapping=self.mapping.name)

		self.assertEqual(result["skipped_by_condition"], 1)
		self.assertEqual(result["sent"], 1)
		self.assertEqual(result["skipped_not_selected"], 0)

	def test_a_rehearsal_still_sends_nothing(self):
		with patch.object(selection, "mode_of", return_value=None), patch(
			"frappe.get_all", return_value=["C1"]
		), patch("frappe.get_doc"), patch.object(
			selection, "sites_allowed", return_value=[{"site_id": self.site}]
		), patch(
			"medusync.outbound._condition_passes", return_value=True
		), patch(
			"medusync.outbound.dispatch"
		) as dispatch:
			result = backfill.run(mapping=self.mapping.name, dry_run=True)

		self.assertTrue(result["dry_run"])
		self.assertEqual(result["sent"], 1)
		dispatch.assert_not_called()
