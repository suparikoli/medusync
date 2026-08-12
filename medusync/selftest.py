# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
"""In-site smoke test. `bench --site <site> execute medusync.selftest.run`

Exercises the paths that only exist inside a real Frappe request cycle:
settings encryption, mapping validation, the wildcard hook's no-op case,
payload construction, and the inbound apply. Deliberately makes no
network calls — delivery is proven separately against a live Medusa.
"""

import json

import frappe

from medusync import config, outbound
from medusync.api import apply_inbound
from medusync.signing import sign, verify

results = []


def ok(label, cond, detail=None):
	results.append((label, bool(cond), detail))


def run():
	frappe.set_user("Administrator")
	_settings()
	_signing()
	_hook_is_inert_when_unconfigured()
	_mapping_validation()
	_payload()
	_inbound()
	_loop_guard()

	passed = sum(1 for _, c, _ in results if c)
	failed = len(results) - passed
	for label, cond, detail in results:
		print(("  PASS  " if cond else "  FAIL  ") + label + (f"   <- {detail}" if detail and not cond else ""))
	print(f"\n{passed} passed, {failed} failed")
	if failed:
		raise SystemExit(1)


def _settings():
	s = frappe.get_single("Medusync Settings")
	s.medusa_url = "https://medusa.example.com/"
	s.inbound_secret = "inbound-secret-abc"
	s.outbound_secret = "outbound-secret-xyz"
	s.enabled = 1
	s.save()
	frappe.db.commit()
	s.reload()
	ok("trailing slash is stripped from medusa_url", s.medusa_url == "https://medusa.example.com", s.medusa_url)
	ok("endpoint composes correctly",
	   config.medusa_endpoint() == "https://medusa.example.com/webhooks/erpnext-inbound",
	   config.medusa_endpoint())
	ok("password field round-trips through get_password",
	   config.get_secret("inbound_secret") == "inbound-secret-abc",
	   config.get_secret("inbound_secret"))
	ok("the two secrets stay distinct",
	   config.get_secret("outbound_secret") == "outbound-secret-xyz")


def _signing():
	body = json.dumps({"event": "ping", "data": {"x": 1}}, separators=(",", ":")).encode()
	hexsig = sign(body, "s3cret")
	ok("hex signature verifies", verify(body, "s3cret", hexsig))
	import base64, hashlib, hmac
	b64 = base64.b64encode(hmac.new(b"s3cret", body, hashlib.sha256).digest()).decode()
	ok("base64 signature also verifies (native Frappe Webhook style)", verify(body, "s3cret", b64))
	ok("wrong secret is rejected", not verify(body, "other", hexsig))
	ok("tampered body is rejected", not verify(body + b" ", "s3cret", hexsig))
	ok("missing signature is rejected", not verify(body, "s3cret", None))


def _hook_is_inert_when_unconfigured():
	"""The wildcard hook runs on every save on the site. With no mapping
	for a doctype it must do nothing and, above all, never raise."""
	before = frappe.db.count("Medusync Log")
	tag = frappe.get_doc({"doctype": "Tag", "name": "medusync-selftest-tag"})
	tag.insert(ignore_permissions=True)
	tag.save(ignore_permissions=True)
	after = frappe.db.count("Medusync Log")
	ok("saving an unmapped doctype logs nothing", before == after, f"{before} -> {after}")
	frappe.delete_doc("Tag", tag.name, ignore_permissions=True, force=True)
	ok("…and deleting it is also inert", frappe.db.count("Medusync Log") == before)


def _mapping_validation():
	if frappe.db.exists("Medusync Mapping", "Selftest ToDo"):
		frappe.delete_doc("Medusync Mapping", "Selftest ToDo", force=True)

	bad = frappe.get_doc({
		"doctype": "Medusync Mapping", "title": "Selftest Bad", "document_type": "ToDo",
		"direction": "Two-way", "docevents": "on_updates",
	})
	try:
		bad.insert(ignore_permissions=True)
		ok("a typo'd docevent is rejected", False, "insert succeeded")
	except frappe.ValidationError:
		ok("a typo'd docevent is rejected", True)

	bad2 = frappe.get_doc({
		"doctype": "Medusync Mapping", "title": "Selftest Bad2", "document_type": "ToDo",
		"direction": "Two-way", "docevents": "on_update", "condition": "doc.status ==",
	})
	try:
		bad2.insert(ignore_permissions=True)
		ok("a broken condition is rejected at save time", False, "insert succeeded")
	except frappe.ValidationError:
		ok("a broken condition is rejected at save time", True)

	bad3 = frappe.get_doc({
		"doctype": "Medusync Mapping", "title": "Selftest Bad3", "document_type": "ToDo",
		"direction": "Two-way", "docevents": "on_update",
		"field_map": [{"frappe_field": "not_a_real_field", "medusa_path": "x"}],
	})
	try:
		bad3.insert(ignore_permissions=True)
		ok("a field that isn't on the doctype is rejected", False, "insert succeeded")
	except frappe.ValidationError:
		ok("a field that isn't on the doctype is rejected", True)

	good = frappe.get_doc({
		"doctype": "Medusync Mapping", "title": "Selftest ToDo", "document_type": "ToDo",
		"direction": "Two-way", "docevents": "after_insert\non_update\non_trash",
		"key_field": "name",
		"field_map": [
			{"frappe_field": "description", "medusa_path": "title", "direction": "Two-way"},
			{"frappe_field": "status", "medusa_path": "state", "direction": "To Medusa"},
			{"frappe_field": "priority", "medusa_path": "priority", "direction": "From Medusa"},
		],
	})
	good.insert(ignore_permissions=True)
	frappe.db.commit()
	ok("a valid mapping saves", frappe.db.exists("Medusync Mapping", "Selftest ToDo"))
	ok("derived event name for after_insert", good.resolved_event_name("after_insert") == "todo.created",
	   good.resolved_event_name("after_insert"))
	ok("derived event name for on_trash", good.resolved_event_name("on_trash") == "todo.deleted",
	   good.resolved_event_name("on_trash"))
	ok("blank medusa_path defaults to the fieldname",
	   all(r.medusa_path for r in good.field_map))


def _payload():
	mapping = frappe.get_doc("Medusync Mapping", "Selftest ToDo")
	todo = frappe.get_doc({"doctype": "ToDo", "description": "selftest payload", "status": "Open",
	                       "priority": "High"})
	todo.insert(ignore_permissions=True)

	payload = outbound.build_payload(mapping, todo)
	ok("mapped field is renamed to its medusa path", payload.get("title") == "selftest payload", payload)
	ok("To Medusa field is included", payload.get("state") == "Open", payload)
	ok("From Medusa field is excluded from the outbound payload", "priority" not in payload, payload)
	ok("the key field is always present", payload.get("name") == todo.name, payload)

	mapping.include_all_fields = 1
	payload_all = outbound.build_payload(mapping, todo)
	ok("send-all includes unmapped fields", payload_all.get("priority") == "High", list(payload_all)[:8])
	mapping.include_all_fields = 0

	frappe.delete_doc("ToDo", todo.name, ignore_permissions=True, force=True)


def _inbound():
	mapping = frappe.get_doc("Medusync Mapping", "Selftest ToDo")

	created = apply_inbound(mapping, {
		"event": "todo.created",
		"data": {"title": "created from medusa", "priority": "Low"},
	})
	ok("inbound insert creates a document", created.get("action") == "created", created)
	name = created.get("name")
	doc = frappe.get_doc("ToDo", name)
	ok("medusa path is translated back to the fieldname", doc.description == "created from medusa", doc.description)
	ok("From Medusa field is applied", doc.priority == "Low", doc.priority)

	updated = apply_inbound(mapping, {
		"event": "todo.updated", "key_field": "name", "key_value": name,
		"data": {"title": "updated from medusa"},
	})
	ok("inbound update targets the existing document", updated.get("action") == "updated", updated)
	ok("…and actually changed it",
	   frappe.get_doc("ToDo", name).description == "updated from medusa")

	blocked = apply_inbound(mapping, {
		"event": "todo.deleted", "key_field": "name", "key_value": name, "data": {},
	})
	ok("delete is refused unless the mapping allows it", blocked.get("status") == "Skipped", blocked)

	mapping.allow_delete = 1
	deleted = apply_inbound(mapping, {
		"event": "todo.deleted", "key_field": "name", "key_value": name, "data": {},
	})
	ok("delete works once permitted", deleted.get("action") == "deleted", deleted)
	ok("…and the document is gone", not frappe.db.exists("ToDo", name))
	mapping.allow_delete = 0

	reserved = apply_inbound(mapping, {
		"event": "todo.created",
		"data": {"title": "reserved field test", "owner": "attacker@example.com", "docstatus": 2},
	})
	victim = frappe.get_doc("ToDo", reserved["name"])
	ok("inbound cannot set `owner`", victim.owner != "attacker@example.com", victim.owner)
	ok("inbound cannot set `docstatus`", victim.docstatus == 0, victim.docstatus)
	frappe.delete_doc("ToDo", victim.name, ignore_permissions=True, force=True)


def _loop_guard():
	"""An inbound write must not fire the outbound hook."""
	mapping = frappe.get_doc("Medusync Mapping", "Selftest ToDo")
	before = frappe.db.count("Medusync Log", {"direction": "Outbound"})
	res = apply_inbound(mapping, {"event": "todo.created", "data": {"title": "loop guard"}})
	after = frappe.db.count("Medusync Log", {"direction": "Outbound"})
	ok("an inbound write queues no outbound event", before == after, f"{before} -> {after}")
	ok("the flag is cleared afterwards", not frappe.flags.get("medusync_inbound"))
	frappe.delete_doc("ToDo", res["name"], ignore_permissions=True, force=True)
