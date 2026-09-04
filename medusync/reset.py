# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Starting over, with both hands on the switch.

A hard reset throws away configuration somebody spent a week getting
right, so the interesting question is not what it does but who may ask.
The answer is: nobody, alone.

Each side generates a secret and shows it once. Each side has to be
handed the other's and prove it holds it. Only a side that has both — the
far side proved it holds ours, and we proved we hold theirs — resets.
Neither system can reset itself, and neither can reset the other.

The secrets are 32 random bytes, live for three minutes, work once, and
are never stored or logged in the clear. Only a hash is kept, and the
inbound receiver redacts the body of a verify before it writes its audit
row, regardless of whether payload logging is on. A secret that reaches a
log is a secret with a much longer life than three minutes.

What a reset keeps is as deliberate as what it clears. Business documents
keep every cross-system id on them: a reset that took `medusa_customer_id`
with it would leave both systems holding the same customers and no longer
knowing it, which is worse than the configuration mistake anyone was
trying to fix. Stores and their secrets stay, so recovering from a bad
configuration does not also mean re-pairing. Exclusions stay, because they
are decisions about individual documents and quietly resuming one is the
one thing a reset must not do.
"""

import base64
import hashlib
import hmac
import json
import secrets

import frappe
from frappe.utils import add_to_date, now_datetime

from medusync import config, defaults, echo, envelope, sites

REQUEST_DOCTYPE = "Medusync Reset Request"

#: 32 bytes. Anything shorter is a password, and this is not typed by a
#: person often enough to be worth shortening.
SECRET_BYTES = 32

#: Three minutes: long enough to carry a secret between two browser tabs,
#: short enough that one left on a screen is worthless by the time anyone
#: walks past.
WINDOW_SECONDS = 180

VERIFY_EVENT = "reset.verify"

#: Set while a reset is rewriting mappings. Both sides restore the same
#: identifiers at the same moment; if each pushed its copy they would
#: collide on version and the conflict rule would pick a winner nobody
#: asked for. See mapping_sync.on_mapping_update.
RESET_FLAG = "medusync_reset"


def _hash(secret: str) -> str:
	return hashlib.sha256((secret or "").encode("utf-8")).hexdigest()


def _new_secret() -> str:
	return base64.urlsafe_b64encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


# ── Asking ───────────────────────────────────────────────────────────


@frappe.whitelist()
def request(site_id: str) -> dict:
	"""Start a reset for one store and return its secret, once.

	The plaintext is in the return value and nowhere else. Any previous
	live request for the store is retired first: two live secrets would
	mean an operator holding two slips of paper they cannot tell apart.
	"""
	frappe.only_for("System Manager")
	if not sites.get_site(site_id):
		frappe.throw(frappe._("There is no store called {0}.").format(site_id))

	for row in frappe.get_all(
		REQUEST_DOCTYPE, filters={"site": site_id, "status": "Pending"}, fields=["name"]
	):
		frappe.db.set_value(REQUEST_DOCTYPE, row.name, "status", "Cancelled", update_modified=False)

	secret = _new_secret()
	doc = frappe.new_doc(REQUEST_DOCTYPE)
	doc.update(
		{
			"site": site_id,
			"status": "Pending",
			"secret_hash": _hash(secret),
			"expires_at": add_to_date(now_datetime(), seconds=WINDOW_SECONDS),
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return {
		"name": doc.name,
		"site": site_id,
		"secret": secret,
		"expires_at": str(doc.expires_at),
		"window_seconds": WINDOW_SECONDS,
	}


@frappe.whitelist()
def cancel(name: str) -> dict:
	frappe.only_for("System Manager")
	frappe.db.set_value(REQUEST_DOCTYPE, name, "status", "Cancelled", update_modified=False)
	frappe.db.commit()
	return {"ok": True, "name": name, "status": "Cancelled"}


# ── Proving ──────────────────────────────────────────────────────────


def verify_local(secret: str) -> dict:
	"""The far side is claiming to hold the secret we generated.

	Every check is against the database inside one transaction, and the
	row is read for update, so two verifies arriving together cannot both
	succeed. A wrong secret deliberately does NOT spend the request: a
	typo, or anyone who can reach the endpoint, must not cost the operator
	the three minutes and the trip.
	"""
	if not secret:
		return {"ok": False, "reason": "no secret offered"}

	digest = _hash(secret)
	candidates = frappe.get_all(
		REQUEST_DOCTYPE,
		filters={"status": ["in", ["Pending", "Verified"]]},
		fields=["name", "secret_hash", "expires_at", "used_at", "status"],
		order_by="creation desc",
		limit=20,
	)
	for row in candidates:
		# Constant time, so a caller cannot learn the hash a byte at a time.
		if not hmac.compare_digest(row.secret_hash or "", digest):
			continue
		if row.used_at:
			return {"ok": False, "reason": "that secret has already been used"}
		if row.expires_at and row.expires_at < now_datetime():
			frappe.db.set_value(REQUEST_DOCTYPE, row.name, "status", "Expired", update_modified=False)
			frappe.db.commit()
			return {"ok": False, "reason": "that secret has expired"}

		locked = frappe.db.get_value(
			REQUEST_DOCTYPE, row.name, ["used_at", "status"], as_dict=True, for_update=True
		)
		if locked.used_at or locked.status not in ("Pending", "Verified"):
			return {"ok": False, "reason": "that secret has already been used"}
		now = now_datetime()
		frappe.db.set_value(
			REQUEST_DOCTYPE,
			row.name,
			{"used_at": now, "local_verified_at": now, "status": "Verified"},
			update_modified=False,
		)
		frappe.db.commit()
		return {"ok": True, "name": row.name}

	return {"ok": False, "reason": "no live reset request matches that secret"}


@frappe.whitelist()
def confirm_remote(name: str, secret: str) -> dict:
	"""Prove to the store that we hold the secret IT generated.

	The operator carries it here from the store's own screen. We send it
	back over the ordinary signed channel; the store checks it against its
	own live request and answers. Only its answer counts.
	"""
	frappe.only_for("System Manager")
	doc = frappe.get_doc(REQUEST_DOCTYPE, name)
	if doc.status in ("Completed", "Cancelled", "Expired"):
		return {"ok": False, "reason": f"this request is {doc.status.lower()}"}
	if doc.expires_at and doc.expires_at < now_datetime():
		frappe.db.set_value(REQUEST_DOCTYPE, name, "status", "Expired", update_modified=False)
		frappe.db.commit()
		return {"ok": False, "reason": "this request has expired"}

	site = sites.get_site(doc.site)
	answer = deliver_verify(site, secret)
	if not answer.get("ok"):
		return {"ok": False, "reason": answer.get("reason") or "the store did not accept that secret"}

	frappe.db.set_value(
		REQUEST_DOCTYPE, name, "remote_confirmed_at", now_datetime(), update_modified=False
	)
	frappe.db.commit()
	return {"ok": True, "name": name, "ready": ready(name)}


def deliver_verify(site, secret: str) -> dict:
	"""POST one `reset.verify` to a store.

	Deliberately not through `outbound.emit`. That path writes an audit row
	carrying the request body, filters on sync selection, tags echoes and
	moves the store's health clock — none of which belong to a control
	message, and the first of which would write the secret to the log.
	"""
	import requests

	from medusync.signing import EVENT_ID_HEADER, SIGNATURE_HEADER, sign

	if not site:
		return {"ok": False, "reason": "no such store"}
	endpoint = sites.endpoint(site)
	outbound_secret = sites.secret(site, "outbound_secret")
	if not endpoint or not outbound_secret:
		return {"ok": False, "reason": "that store has no URL or outbound secret"}

	event_id = "reset:%s:%s" % (site["site_id"], frappe.generate_hash(length=12))
	body = json.dumps(
		envelope.build(
			VERIFY_EVENT,
			event_id,
			site_id=site["site_id"],
			data={"secret": secret},
		),
		separators=(",", ":"),
		default=str,
	).encode("utf-8")
	try:
		response = requests.post(
			endpoint,
			data=body,
			headers={
				"Content-Type": "application/json",
				SIGNATURE_HEADER: sign(body, outbound_secret),
				EVENT_ID_HEADER: event_id,
			},
			timeout=sites.timeout(site),
			verify=sites.verify_ssl(site),
		)
	except Exception as exc:
		# The message may quote the URL but never the body.
		return {"ok": False, "reason": "could not reach the store: %s" % exc}

	if not 200 <= response.status_code < 300:
		return {"ok": False, "reason": "the store answered %s" % response.status_code}
	try:
		payload = response.json()
	except Exception:
		return {"ok": False, "reason": "the store's answer was not JSON"}
	result = payload.get("result") or payload
	if result.get("ok") or payload.get("status") == "success":
		return {"ok": True}
	return {"ok": False, "reason": result.get("reason") or "the store refused that secret"}


def ready(name: str) -> bool:
	"""Both hands on the switch?"""
	row = frappe.db.get_value(
		REQUEST_DOCTYPE, name, ["local_verified_at", "remote_confirmed_at", "status"], as_dict=True
	)
	if not row or row.status in ("Completed", "Cancelled", "Expired", "Failed"):
		return False
	return bool(row.local_verified_at and row.remote_confirmed_at)


# ── Doing it ─────────────────────────────────────────────────────────

#: Cleared by a reset. Everything not named here survives, and the test
#: suite has a case per line of both lists.
CLEARS = ("Medusync Log",)

KEEPS = (
	"business documents and every medusa_* reference on them",
	"Medusync Site records and their secrets",
	"Medusync Exclusion entries",
	"the warehouse and price-list maps on each store",
)


@frappe.whitelist()
def perform(name: str) -> dict:
	"""Restore the configuration. Refuses unless both sides proved themselves."""
	if not ready(name):
		frappe.throw(
			frappe._(
				"This reset is not verified by both sides yet. Each system generates a "
				"secret and has to be handed the other's."
			)
		)

	doc = frappe.get_doc(REQUEST_DOCTYPE, name)
	report = {"site": doc.site, "cleared": {}, "kept": list(KEEPS)}

	frappe.flags[RESET_FLAG] = True
	try:
		restored = defaults.restore_defaults(reason="hard reset")
		report["mappings"] = restored["mappings"]
		report["defaults_version"] = restored["version"]

		# Everything else somebody wrote is switched off, not deleted. A
		# reset that discarded it would lose work over a configuration
		# change; one that left it running would have reset nothing.
		theirs = [
			row.name
			for row in frappe.get_all(
				config.MAPPING_DOCTYPE, fields=["name", "mapping_uid", "enabled"]
			)
			if row.enabled and not defaults.owns(row.mapping_uid)
		]
		for mapping_name in theirs:
			frappe.db.set_value(
				config.MAPPING_DOCTYPE,
				mapping_name,
				{"enabled": 0, "tested_signature": None, "last_test_status": "Untested"},
				update_modified=False,
			)
		report["disabled"] = theirs

		count = frappe.db.count("Medusync Log")
		frappe.db.delete("Medusync Log")
		report["cleared"]["Medusync Log"] = count

		echo.forget_all()
		report["cleared"]["echo breadcrumbs"] = True

		frappe.db.set_value(
			REQUEST_DOCTYPE,
			name,
			{
				"status": "Completed",
				"completed_at": now_datetime(),
				"reset_report": json.dumps(report, indent=2, default=str),
			},
			update_modified=False,
		)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.db.set_value(
			REQUEST_DOCTYPE,
			name,
			{"status": "Failed", "reset_report": frappe.get_traceback()[:8000]},
			update_modified=False,
		)
		frappe.db.commit()
		raise
	finally:
		frappe.flags[RESET_FLAG] = False
		frappe.clear_cache()

	return report


@frappe.whitelist()
def perform_in_background(name: str) -> dict:
	"""What the button calls. The work deletes rows and rewrites mappings,
	which is not something to do inside the request that asked for it."""
	frappe.only_for("System Manager")
	if not ready(name):
		frappe.throw(frappe._("This reset is not verified by both sides yet."))
	frappe.enqueue("medusync.reset.perform", queue="long", name=name, enqueue_after_commit=True)
	return {"ok": True, "name": name, "queued": True}


@frappe.whitelist()
def status(name: str) -> dict:
	frappe.only_for("System Manager")
	row = frappe.db.get_value(
		REQUEST_DOCTYPE,
		name,
		["site", "status", "expires_at", "local_verified_at", "remote_confirmed_at", "completed_at"],
		as_dict=True,
	)
	if not row:
		frappe.throw(frappe._("No such reset request."))
	row["ready"] = ready(name)
	row["seconds_left"] = max(
		0, int((row.expires_at - now_datetime()).total_seconds()) if row.expires_at else 0
	)
	return dict(row)
