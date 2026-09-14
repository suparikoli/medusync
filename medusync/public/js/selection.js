// The Medusa sync decision for a document.
//
// A doctype in "Only chosen documents" mode carries a real `medusync_sync`
// tick (see medusync/sync_field.py), so the form, the list column and
// Frappe's own Actions -> Edit all work without us. What is left for JS is
// the part a checkbox cannot express: WHICH store, when more than one is
// connected. That is the dialog below, reachable from the form button and
// from an Actions entry on the list.

frappe.provide("medusync.selection");

medusync.selection.modes = () => frappe.boot.medusync_selection || {};
medusync.selection.mode_of = (doctype) => medusync.selection.modes()[doctype];

medusync.selection.dialog = async (doctype, names) => {
	const first = await frappe.call({
		method: "medusync.selection.document_selection",
		args: { doctype, name: names[0] },
	});
	const info = first.message || { sites: [] };
	if (!info.sites.length) {
		frappe.msgprint(__("No Medusa store is connected yet. Add one under Medusync Site."));
		return;
	}
	const many = names.length > 1;
	const d = new frappe.ui.Dialog({
		title: many ? __("Medusa sync for {0} records", [names.length]) : __("Medusa sync"),
		fields: [
			{
				fieldtype: "HTML",
				options: `<p class="text-muted small">${
					info.mode === "Only chosen documents"
						? __("Nothing of this type syncs until it is chosen for a store.")
						: __("Everything of this type syncs unless it is switched off for a store.")
				}</p>`,
			},
			...info.sites.map((s) => ({
				fieldtype: "Check",
				fieldname: s.site_id,
				label: s.title,
				default: many ? 0 : s.allowed ? 1 : 0,
			})),
		],
		primary_action_label: __("Save"),
		primary_action: async (values) => {
			const sites = info.sites.filter((s) => values[s.site_id]).map((s) => s.site_id);
			await frappe.call({
				method: "medusync.selection.set_document_selection",
				args: { doctype, names, sites },
				freeze: true,
			});
			d.hide();
			frappe.show_alert({ message: __("Medusa sync updated"), indicator: "green" });
			// The tick mirrors the inclusion rows, so whatever is on screen is
			// now a version behind.
			if (cur_list && cur_list.doctype === doctype) cur_list.refresh();
			if (cur_frm && cur_frm.doctype === doctype) cur_frm.reload_doc();
		},
	});
	d.show();
};

$(document).on("form-refresh", (e, frm) => {
	if (!frm || frm.is_new() || !medusync.selection.mode_of(frm.doctype)) return;
	frm.add_custom_button(__("Medusa sync"), () =>
		medusync.selection.dialog(frm.doctype, [frm.doc.name]),
	);
});

// Register through listview_settings rather than watching the router.
// The router fires before the list exists, so the old code raced it and the
// Actions entry was usually missing; this is the hook Frappe itself calls
// once the view is built, for exactly this.
medusync.selection.register_list_views = () => {
	Object.keys(medusync.selection.modes()).forEach((doctype) => {
		const existing = frappe.listview_settings[doctype] || {};
		if (existing.__medusync) return;
		const previous_onload = existing.onload;
		frappe.listview_settings[doctype] = Object.assign(existing, {
			__medusync: true,
			onload(list) {
				if (previous_onload) previous_onload.call(this, list);
				list.page.add_actions_menu_item(
					__("Medusa sync…"),
					() => {
						const names = list.get_checked_items(true);
						if (!names.length) {
							frappe.msgprint(__("Select the records first."));
							return;
						}
						medusync.selection.dialog(list.doctype, names);
					},
					false,
				);
			},
		});
	});
};

frappe.after_ajax(() => medusync.selection.register_list_views());
