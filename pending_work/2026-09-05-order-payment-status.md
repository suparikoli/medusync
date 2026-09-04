# What an order's payment status actually means

**Deferred from:** Phase 3 (order source and payment metadata)
**Belongs to:** whenever somebody decides the question below; no phase depends on it
**Side:** both. This file is the ERPNext half, where the figure is computed;
the plugin repo's `pending_work/` holds the Medusa half.

## What ships today

`medusync/order_meta.py`:

```python
total = float(doc.get("grand_total") or 0)
paid = float(doc.get("advance_paid") or 0)
outstanding = round(total - paid, 2)
```

and a `status` of `paid`, `part_paid` or `unpaid` from those three numbers.
It travels on `order.source.set` and lands in the Medusa order's metadata
under `erp_order.payment`.

## What is wrong with it

`advance_paid` is money received **against the Sales Order itself**. An
order settled the ordinary way — Sales Order, then Sales Invoice, then a
Payment Entry allocated to the invoice — leaves `advance_paid` at zero, so
a fully paid order reports `unpaid`.

The invoice event corrects it a moment later:
`reverse.on_sales_invoice` sends `Paid` or `Unpaid` from the invoice's own
`outstanding_amount`, under a different metadata key. So the storefront
holds two payment opinions in two places, and the more recent one is not
reliably the more correct one.

`order.payment.set` is unaffected and is the trustworthy figure: it carries
real Payment Entry receipts, allocated per order, and the far side keeps a
running `erp_payments_total`. A storefront that needs one number should
read that one.

## What would fix it

`payment_of(doc)` would consider the Sales Invoices raised against the
order and their `outstanding_amount`, not only the order's advance.

## The decision that has to be made first

Which document is the payment authority when both exist. Three defensible
answers, and they disagree:

- **The invoice, when there is one.** Matches how accounts think. Means the
  status flips to `unpaid` the moment an invoice is raised and before the
  money lands, which reads as a regression to anyone watching the store.
- **The sum of Payment Entry receipts.** Truest to the money, and already
  computed on the far side. Ignores credit terms entirely.
- **Both, reported separately** — ordered against received, invoiced
  against paid. Honest, and moves the choice to whoever writes the
  storefront.

Until that is settled the function is documented as advance-based in the
code and here, rather than being quietly wrong.
