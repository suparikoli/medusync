# Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
# For license information, please see license.txt

"""A second handler pack, for the tests that need two.

The app ships one pack. The registry, though, is about *several*: it
splits a comma-separated list, rebuilds when the list changes, takes the
mapped upsert from the first pack that offers one, and lets a pack
declare no outbound hooks at all. None of that says anything with a
single pack to point at.

So the tests bring their own. `installed()` puts this module in
`sys.modules` under the name the loader looks for, which means the
registry loads it by its ordinary path — `importlib.import_module` finds
it exactly as it would find a pack on disk. Nothing is stubbed out.

Deliberately, this pack declares no `OUTBOUND_HOOKS`: a pack that only
listens is a real shape, and one test asserts it contributes nothing.
"""

import contextlib
import sys

from medusync import handlers

NAME = "probe"
MODULE = f"medusync.handlers.{NAME}"

#: The event this pack owns. Nothing else registers it, so its presence
#: in the registry is proof this pack — and only this pack — loaded.
EVENT = "probe.pinged"

MAPPED_UPSERT = "medusync.tests.probe_pack.upsert"


def upsert(doctype, payload, **kwargs):
	return {"status": "ok", "doctype": doctype}


def register():
	handlers.register_handler(EVENT, lambda payload, event_id="": {"status": "ok"})


@contextlib.contextmanager
def installed():
	"""Make `medusync.handlers.probe` importable for the duration."""
	previous = sys.modules.get(MODULE)
	sys.modules[MODULE] = sys.modules[__name__]
	try:
		yield
	finally:
		if previous is None:
			sys.modules.pop(MODULE, None)
		else:
			sys.modules[MODULE] = previous
		handlers.clear()
