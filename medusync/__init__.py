__version__ = "0.1.0"

# Handler packs (site-specific event handlers) are NOT registered here.
# A site opts in through its `site_config.json`:
#
#     "medusync_handler_packs": ["risitex"]
#
# and `medusync.handlers` builds the registry lazily on first use, so
# importing this package never needs a site context. When the key is
# absent the Polemarch pack loads (the pre-existing behaviour).
# See medusync/handlers/__init__.py.
