# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""RISITEX (textile e-commerce) handler pack for the Medusa wire.

Where the Polemarch pack maps Medusa products to `Security` (by ISIN)
and orders to `Security Sale`, RISITEX is ordinary commerce:

  Product  -> Item             (with the mandatory item_group / stock_uom)
  Order    -> Sales Order      (customer link + child line items)
  Invoice  -> Sales Invoice
  Customer -> Customer

The canonical-mapping push path (`medusync.api.receive_mapped`) is
pointed at `medusync.handlers.risitex.mapped.upsert_via_mapping` for
this site. See that module for the per-doctype logic.
"""


from medusync.handlers import register_handler


def register() -> None:
    """Register the RISITEX handler pack. Idempotent -- safe to re-run.

    One entry: the Medusa-initiated return request. Inbound order/product/
    customer/invoice upserts flow through the mapping receiver
    (medusync.api.receive_mapped -> mapped.upsert_via_mapping), not this
    registry; only the return-request method call needs a handler here
    (no doctype to upsert -- we call create_pending_return). Called at
    import time from medusync/__init__.py so every worker process has it
    wired before it dispatches an inbound event.
    """
    from medusync.handlers.risitex.reverse import handle_return_requested

    register_handler("order.return_requested", handle_return_requested)
