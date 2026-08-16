__version__ = "0.1.0"


# Register the Polemarch event-handler pack at module-import time.
# Any process that imports medusync (e.g. on a webhook hit, or via
# `frappe.call`) gets the handlers wired up before it dispatches.
# `register_handler` is idempotent — re-registration is a no-op when
# the handler is already there, so a future migration that calls
# `after_install` won't double-register.
#
# To opt out (e.g. on a non-MISPL site that wants a clean Polemarch-
# free medusync): set `medusync.skip_polemarch_handlers = True` in
# `site_config.json`. The import guard below reads it.
import os

if not os.environ.get("MEDUSYNC_SKIP_POLEMARCH"):
	# The flag above is the test/operator escape hatch. The import path
	# never touches a DB and never calls Frappe APIs, so it is safe in
	# any process (request worker, scheduler, console, bench CLI).
	try:
		from medusync.handlers.polemarch import register as _register_polemarch
		_register_polemarch()
	except Exception:
		# Don't break the medusync import if the Polemarch pack is
		# missing or broken. The empty registry is still a valid
		# medusync — inbound webhooks will return
		# `{ok, skipped, "no_handler_for_event"}` for everything.
		import frappe
		frappe.log_error(
			title="medusync: failed to register Polemarch handler pack",
			message=frappe.get_traceback(),
		)
