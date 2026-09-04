# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Wire contract v2.

Every signed body now carries `v`, `ts` and an `origin` block naming the
system, the site and a correlation id that survives a causal chain. v1
bodies (no `v`) still parse, so a not-yet-upgraded sender keeps working.
"""

try:
	from frappe.tests import UnitTestCase as BaseCase
except ImportError:  # pragma: no cover - older frappe
	from unittest import TestCase as BaseCase

from medusync import envelope


class TestEnvelopeV2(BaseCase):
	def test_build_event_envelope(self):
		env = envelope.build(
			"customer.updated",
			"frappe:Customer:CUST-1:2026-09-04",
			site_id="site-a",
			data={"email_id": "a@b.c"},
		)
		self.assertEqual(env["v"], 2)
		self.assertEqual(env["kind"], "event")
		self.assertEqual(env["event"], "customer.updated")
		self.assertEqual(env["event_id"], "frappe:Customer:CUST-1:2026-09-04")
		# v1 receivers read `id`; both keys travel so a rolling upgrade works.
		self.assertEqual(env["id"], env["event_id"])
		self.assertEqual(env["origin"]["system"], "erpnext")
		self.assertEqual(env["origin"]["site_id"], "site-a")
		self.assertTrue(env["origin"]["correlation_id"])
		self.assertIsInstance(env["ts"], int)
		self.assertEqual(env["data"], {"email_id": "a@b.c"})

	def test_build_mapped_envelope_keeps_v1_field_names(self):
		env = envelope.build(
			"customer.created",
			"evt-1",
			site_id="site-a",
			kind="mapped",
			doctype="Customer",
			key_field="email_id",
			key_value="a@b.c",
			payload={"customer_name": "A"},
			allow_create=True,
			allow_update=False,
		)
		self.assertEqual(env["kind"], "mapped")
		self.assertEqual(env["doctype"], "Customer")
		self.assertEqual(env["key_field"], "email_id")
		self.assertEqual(env["payload"], {"customer_name": "A"})
		self.assertIs(env["allow_update"], False)

	def test_correlation_id_is_carried_not_regenerated(self):
		env = envelope.build("x.y", "e1", site_id="s", correlation_id="corr-123")
		self.assertEqual(env["origin"]["correlation_id"], "corr-123")

	def test_parse_v2(self):
		raw = envelope.build("customer.updated", "e1", site_id="s", data={"a": 1})
		p = envelope.parse(raw)
		self.assertEqual(p.version, 2)
		self.assertEqual(p.kind, "event")
		self.assertEqual(p.event, "customer.updated")
		self.assertEqual(p.event_id, "e1")
		self.assertEqual(p.data, {"a": 1})
		self.assertEqual(p.origin_system, "erpnext")
		self.assertEqual(p.origin_site_id, "s")

	def test_parse_v1_event_body(self):
		p = envelope.parse({"event": "customer.updated", "event_id": "e1", "data": {"a": 1}})
		self.assertEqual(p.version, 1)
		self.assertEqual(p.kind, "event")
		self.assertEqual(p.event_id, "e1")
		self.assertEqual(p.data, {"a": 1})
		self.assertIsNone(p.origin_system)

	def test_parse_v1_mapped_body_is_recognised_by_shape(self):
		p = envelope.parse(
			{
				"event": "customer.created",
				"id": "e1",
				"doctype": "Customer",
				"key_field": "email_id",
				"key_value": "a@b.c",
				"payload": {"customer_name": "A"},
			}
		)
		self.assertEqual(p.version, 1)
		self.assertEqual(p.kind, "mapped")
		self.assertEqual(p.event_id, "e1")
		self.assertEqual(p.doctype, "Customer")
		self.assertEqual(p.payload, {"customer_name": "A"})
		# absent flags default to permitted, exactly as v1 behaved
		self.assertTrue(p.allow_create)
		self.assertTrue(p.allow_update)

	def test_replay_window(self):
		fresh = envelope.build("x.y", "e1", site_id="s")
		self.assertTrue(envelope.is_fresh(envelope.parse(fresh)))
		stale = dict(fresh, ts=fresh["ts"] - envelope.REPLAY_WINDOW_SECONDS - 30)
		self.assertFalse(envelope.is_fresh(envelope.parse(stale)))
		# a v1 body without ts is still accepted (backward compatibility)
		self.assertTrue(envelope.is_fresh(envelope.parse({"event": "x.y", "event_id": "e"})))

	def test_echo_detection(self):
		ours = {"site-a", "site-b"}
		mine = envelope.build("x.y", "e1", site_id="s", echo_of="erpnext:site-a")
		self.assertTrue(envelope.is_echo(envelope.parse(mine), ours))
		theirs = envelope.build("x.y", "e2", site_id="s", echo_of="erpnext:site-z")
		self.assertFalse(envelope.is_echo(envelope.parse(theirs), ours))
		plain = envelope.build("x.y", "e3", site_id="s")
		self.assertFalse(envelope.is_echo(envelope.parse(plain), ours))

	def test_a_body_that_originated_here_is_an_echo(self):
		# Same system, one of our own sites: it is our own event coming back.
		raw = envelope.build("x.y", "e1", site_id="site-a")
		self.assertTrue(envelope.is_echo(envelope.parse(raw), {"site-a"}))
