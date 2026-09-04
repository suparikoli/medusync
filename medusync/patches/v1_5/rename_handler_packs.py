# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""The handler packs stop being named after deployments.

One of the two old packs was never specific in behaviour — stock levels,
prices, delivery notes, invoices and order metadata are what every store
has. Only its name was, and it is now `commerce`.

The other genuinely was: it wrote Security, Security Sale, Wallet Deposit
and Withdrawal, doctypes no ordinary ERPNext has. It has been removed from
this app. A site that needs that behaviour should carry it as its own
package rather than have every other installation ship it.

A site still naming either in `site_config.json` would fail to load a pack
on the next save — the registry logs that and carries on, so the sync would
go quiet rather than break, which is worse. This rewrites the key.
"""

import frappe
from frappe.installer import update_site_config

CONF_KEY = "medusync_handler_packs"

#: Old name -> new name. The removed pack maps to nothing: it is gone, and
#: a site that was running it needs a decision, not a silent substitution.
RENAMED = {"risitex": "commerce"}
REMOVED = {"polemarch"}


def execute():
	current = frappe.conf.get(CONF_KEY)

	if current is None:
		# No opinion recorded. The default used to be the removed pack and is
		# now the commerce one; writing it down makes that visible in the site
		# config rather than implied by a constant in the code.
		update_site_config(CONF_KEY, ["commerce"])
		print("medusync: handler packs set to ['commerce']")
		return

	if isinstance(current, str):
		packs = [p.strip() for p in current.split(",") if p.strip()]
	else:
		packs = [str(p).strip() for p in current if str(p).strip()]

	out, dropped = [], []
	for pack in packs:
		if pack in REMOVED:
			dropped.append(pack)
			continue
		renamed = RENAMED.get(pack, pack)
		if renamed not in out:
			out.append(renamed)

	if not out:
		# Everything this site listed is gone. Leaving it empty would mean no
		# domain handlers at all and no explanation; the commerce pack is the
		# closest honest answer and the operator can change it.
		out = ["commerce"]

	if out != packs:
		update_site_config(CONF_KEY, out)
		print("medusync: handler packs %s -> %s" % (packs, out))
		if dropped:
			print(
				"medusync: the %s pack was removed from this app. If this site "
				"needs it, it has to be carried as its own package."
				% ", ".join(dropped)
			)
	else:
		print("medusync: handler packs already current (%s)" % out)
