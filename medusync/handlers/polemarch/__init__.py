# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""Polemarch event handlers for the Medusa wire.

Why this lives in `medusync` and not `polemarch`: the operator's
position is that polemarch is a passive ERPNext customisation with zero
Medusa knowledge. The rich per-event logic (bank/demat child-row
upserts, Security Sale creation with submit + Investment Disposal + COGS
JE, order.canceled cascade, Cashfree gateway-fee side JE) is sync code
that responds to events on the Medusa wire, so it ships with the wire
app. medusync is no longer purely site-agnostic — it ships a Polemarch
pack because MITHTECH is the only consumer and the abstraction isn't
worth the cost of a third app.

`register()` is called from `medusync.install.after_install` so
installing medusync wires the Polemarch pack automatically. The
handlers are pure functions of `(payload, event_id)` returning a dict
envelope; the wire layer (HMAC, idempotency, Medusync Log row) is
handled upstream by `medusync.api.receive`.
"""

from medusync.handlers import register_handler

from medusync.handlers.polemarch import (
	common,
	customer,
	order,
	product,
	wallet,
)


def register() -> None:
	"""Register the Polemarch handler pack. Idempotent — safe to re-run."""
	register_handler("ping",                          common.ping)
	register_handler("wallet.deposit.captured",        wallet.handle_deposit)
	register_handler("wallet.withdrawal.posted",       wallet.handle_withdrawal)
	# Backwards-compat alias — `customer.synced` and `customer.updated`
	# are the same payload shape and run the same handler.
	register_handler("customer.synced",               customer.handle_updated)
	register_handler("customer.created",              customer.handle_created)
	register_handler("customer.updated",              customer.handle_updated)
	register_handler("customer.kyc.synced",           customer.handle_kyc_synced)
	# `product.synced` is the legacy name; `product.created` and
	# `product.updated` are the canonical event names. The Medusa
	# plugin emits all three with the same handler.
	register_handler("product.created",               product.handle_synced)
	register_handler("product.updated",               product.handle_synced)
	register_handler("product.synced",                product.handle_synced)
	register_handler("order.placed",                  order.handle_placed)
	register_handler("order.canceled",                order.handle_canceled)
	register_handler("order.cancelled",               order.handle_canceled)  # British
