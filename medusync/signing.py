# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""HMAC-SHA256 request signing, shared by both directions.

Both sides sign the EXACT request bytes. That matters more than it
sounds: re-serialising a parsed body changes key order and whitespace,
which changes the digest, which produces a 401 that looks like a wrong
secret and sends people rotating credentials that were fine.

Encoding: we emit hex and accept either hex or base64. The Medusa
plugin emits hex on its own pushes but Frappe's *native* Webhook
framework emits base64, and a site may be running a mix of the two
while migrating onto this app — so the verifier takes both.
"""

import hashlib
import hmac

SIGNATURE_HEADER = "X-Medusa-Signature"
EVENT_ID_HEADER = "X-Medusa-Event-Id"


def sign(body: bytes, secret: str) -> str:
	"""Hex HMAC-SHA256 of the raw body."""
	return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify(body: bytes, secret: str, provided: str | None) -> bool:
	"""Constant-time check against both accepted encodings.

	`hmac.compare_digest` on every branch — an early return on a length
	mismatch would leak the digest length through timing.
	"""
	if not provided or not secret:
		return False

	provided = provided.strip()
	digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256)

	if hmac.compare_digest(provided, digest.hexdigest()):
		return True

	import base64

	b64 = base64.b64encode(digest.digest()).decode("ascii")
	return hmac.compare_digest(provided, b64)
