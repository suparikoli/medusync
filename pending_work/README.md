# pending_work

Deferred requirements for the Frappe/ERPNext side of the ERPNext ↔ Medusa connector.
The Medusa plugin keeps its own `pending_work/` in `medusa-plugin-erpnext`; items that
touch both systems appear in both folders with the same file name.

Rules:
- One file per topic: `YYYY-MM-DD-<topic>.md`.
- Each file states scope, what already exists, dependencies, and the roadmap
  phase it belongs to.
- This folder is tracked in git on purpose. Never add it to `.gitignore`.
- When an item ships, delete its file in the same PR (the PR description links
  the file's last revision).
