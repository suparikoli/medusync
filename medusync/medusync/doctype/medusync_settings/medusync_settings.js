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
    },
});
