// Medusync Settings — operator conveniences.
frappe.ui.form.on("Medusync Settings", {
    refresh(frm) {
        frm.add_custom_button(__("Test connection to Medusa"), () => {
            frappe.show_alert({ message: __("Pinging Medusa…"), indicator: "blue" });
            frappe.call({
                method: "medusync.api.test_medusa_connection",
                callback: (r) => {
                    const m = r.message || {};
                    if (m.ok) {
                        frappe.msgprint({
                            title: __("Connection OK"),
                            indicator: "green",
                            message: __("Reached Medusa at {0} (HTTP {1}).", [m.url, m.status_code]),
                        });
                    } else {
                        frappe.msgprint({
                            title: __("Could not reach Medusa"),
                            indicator: "red",
                            message: frappe.utils.escape_html(m.message || "unknown error"),
                        });
                    }
                },
            });
        });

        // Picking sixty thousand records one at a time is not a workflow.
        // Choose the lot, then untick the few that should not go — the
        // opposite starting point, and usually the one people want.
        (frm.doc.selection_doctypes || [])
            .filter((row) => row.enabled && row.mode === "Only chosen documents")
            .forEach((row) => {
                frm.add_custom_button(
                    __("Choose all {0}", [row.document_type]),
                    () => medusync_choose_all(row.document_type),
                    __("Medusa sync"),
                );
                frm.add_custom_button(
                    __("Choose none of {0}", [row.document_type]),
                    () => medusync_choose_none(row.document_type),
                    __("Medusa sync"),
                );
            });
    },
});

async function medusync_counts(doctype) {
    const r = await frappe.call({ method: "medusync.selection.chosen_count", args: { doctype } });
    return r.message || { chosen: 0, total: 0 };
}

async function medusync_choose_all(doctype) {
    const counts = await medusync_counts(doctype);
    const d = new frappe.ui.Dialog({
        title: __("Choose all {0}", [doctype]),
        fields: [
            {
                fieldtype: "HTML",
                options: `<p>${__("{0} of {1} are chosen now.", [counts.chosen, counts.total])}</p>`,
            },
            {
                fieldtype: "Code",
                fieldname: "filters",
                label: __("Narrow it first (optional)"),
                options: "JSON",
                description: __(
                    'Frappe filters as JSON, e.g. {"item_group": "Electrical"}. Leave blank for every record.',
                ),
            },
        ],
        primary_action_label: __("Choose them"),
        primary_action: async (values) => {
            let filters = null;
            if (values.filters && values.filters.trim()) {
                try {
                    filters = JSON.parse(values.filters);
                } catch (e) {
                    frappe.msgprint(__("That is not valid JSON."));
                    return;
                }
            }
            d.hide();
            const r = await frappe.call({
                method: "medusync.selection.choose_all",
                args: { doctype, filters },
                freeze: true,
                freeze_message: __("Choosing records…"),
            });
            const m = r.message || {};
            frappe.msgprint(
                m.ok
                    ? __("{0} {1} records are now chosen.", [m.chosen, doctype])
                    : frappe.utils.escape_html(m.message || "unknown error"),
            );
        },
    });
    d.show();
}

async function medusync_choose_none(doctype) {
    const counts = await medusync_counts(doctype);
    frappe.confirm(
        __("Un-choose all {0} chosen {1} records? Nothing of this type will sync afterwards.", [
            counts.chosen,
            doctype,
        ]),
        async () => {
            const r = await frappe.call({
                method: "medusync.selection.choose_none",
                args: { doctype },
                freeze: true,
            });
            const m = r.message || {};
            frappe.msgprint(__("{0} choices removed.", [m.removed || 0]));
        },
    );
}
