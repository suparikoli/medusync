# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
"""Live outbound delivery test against a local stub receiver.

Proves the half selftest.run deliberately skips: that the wildcard hook
actually fires on a real save, that the envelope is signed with the
outbound secret, and that the log row lands on Success.

    bench --site <site> execute medusync.selftest_delivery.run
"""

import frappe

results = []


def ok(label, cond, detail=None):
	results.append((label, bool(cond), detail))


def run():
	frappe.set_user("Administrator")

	s = frappe.get_single("Medusync Settings")
	s.medusa_url = "http://127.0.0.1:8791"
	s.inbound_path = "/webhooks/erpnext-inbound"
	s.outbound_secret = "outbound-secret-xyz"
	s.use_background_jobs = 0  # deliver inline so the test can assert
	s.enabled = 1
	s.save()
	frappe.db.commit()

	mapping = frappe.get_doc("Medusync Mapping", "Selftest ToDo")
	mapping.enabled = 1
	mapping.save()
	frappe.db.commit()
	frappe.clear_cache()

	before = frappe.db.count("Medusync Log", {"direction": "Outbound"})

	todo = frappe.get_doc({
		"doctype": "ToDo", "description": "delivery test", "status": "Open", "priority": "Medium",
	})
	todo.insert(ignore_permissions=True)
	frappe.db.commit()

	after = frappe.db.count("Medusync Log", {"direction": "Outbound"})
	# Exactly ONE — Frappe runs on_update inside insert(), and a mapping
	# listening to both triggers must not emit the same state twice.
	ok("a create queues exactly one outbound event", after == before + 1, f"{before} -> {after}")

	rows = frappe.get_all(
		"Medusync Log",
		filters={"direction": "Outbound", "document_name": todo.name},
		fields=["name", "status", "status_code", "event", "event_id", "error", "attempt"],
		order_by="creation desc",
		limit=1,
	)
	ok("a log row exists for the saved document", bool(rows), rows)
	if rows:
		row = rows[0]
		ok("delivery succeeded", row.status == "Success", dict(row))
		ok("the receiver returned 200 (so the signature verified)", row.status_code == 200, row.status_code)
		ok("event name is the derived one", row.event == "todo.created", row.event)
		ok("event_id identifies the document", todo.name in (row.event_id or ""), row.event_id)
		ok("no error recorded", not row.error, row.error)

	# What the stub actually saw.
	import json, os
	path = "/tmp/medusync_stub_received.json"
	seen = json.load(open(path)) if os.path.exists(path) else []
	mine = [r for r in seen if (r.get("data") or {}).get("name") == todo.name]
	ok("the stub received the event", bool(mine), seen[-1] if seen else None)
	if mine:
		r = mine[-1]
		ok("HMAC verified on the receiving side", r["ok"] is True, r)
		ok("posted to the configured inbound path", r["path"] == "/webhooks/erpnext-inbound", r["path"])
		ok("field map applied on the wire", r["data"].get("title") == "delivery test", r["data"])
		ok("From-Medusa-only field withheld", "priority" not in r["data"], r["data"])
		ok("event id also sent as a header", r["event_id_header"] == r["event_id"], r)

	# An update must fire a SECOND, distinct event.
	todo.reload()
	todo.description = "delivery test edited"
	todo.save(ignore_permissions=True)
	frappe.db.commit()
	rows2 = frappe.get_all(
		"Medusync Log",
		filters={"direction": "Outbound", "document_name": todo.name},
		fields=["event", "event_id", "status"], order_by="creation desc", limit=2,
	)
	ok("an update fires its own event", len(rows2) == 2 and rows2[0].event == "todo.updated",
	   [dict(r) for r in rows2])
	ok("the two events have different ids", len({r.event_id for r in rows2}) == 2,
	   [r.event_id for r in rows2])
	ok("the update also delivered", rows2[0].status == "Success", dict(rows2[0]))

	# A failing endpoint must be recorded, not swallowed.
	s.medusa_url = "http://127.0.0.1:9"  # closed port
	s.max_attempts = 1
	s.save()
	frappe.db.commit()
	frappe.clear_cache()
	todo.reload()
	todo.description = "delivery test unreachable"
	todo.save(ignore_permissions=True)
	frappe.db.commit()
	fail_row = frappe.get_all(
		"Medusync Log", filters={"direction": "Outbound", "document_name": todo.name},
		fields=["status", "error"], order_by="creation desc", limit=1,
	)[0]
	ok("an unreachable Medusa is recorded as Failed", fail_row.status == "Failed", dict(fail_row))
	ok("…with the reason attached", bool(fail_row.error), fail_row.error)

	frappe.delete_doc("ToDo", todo.name, ignore_permissions=True, force=True)

	passed = sum(1 for _, c, _ in results if c)
	failed = len(results) - passed
	for label, cond, detail in results:
		print(("  PASS  " if cond else "  FAIL  ") + label + (f"   <- {detail}" if detail and not cond else ""))
	print(f"\n{passed} passed, {failed} failed")
	if failed:
		raise SystemExit(1)
