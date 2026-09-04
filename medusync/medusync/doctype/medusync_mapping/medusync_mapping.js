// Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
// For license information, please see license.txt

// The mapping studio, in the form where the mapping is written.
//
// A mapping is a small program somebody types into a grid, and the only
// feedback it used to give was a log row after a real document was saved.
// The buttons close that loop: see what a record here actually
// looks like, see what the mapping would do with it in both directions,
// and — once it holds up — switch it on.

frappe.ui.form.on("Medusync Mapping", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Rehearse"), () => rehearse(frm), __("Test"));
		frm.add_custom_button(__("Rehearse and Enable"), () => testAndEnable(frm), __("Test"));
		frm.add_custom_button(__("Send a Test Event"), () => sendTest(frm), __("Test"));
		frm.add_custom_button(__("Show a Sample Record"), () => showSample(frm), __("Test"));

		frm.add_custom_button(__("Push Everything Now"), () => pushAll(frm), __("Run"));
		frm.add_custom_button(__("Re-send What Gave Up"), () => resyncFailed(frm), __("Run"));

		// Something is waiting on a person. Say what, at the top, before
		// they get as far as wondering why nothing is syncing.
		if (frm.doc.attention) {
			frm.dashboard.set_headline_alert(
				`<b>${frappe.utils.escape_html(frm.doc.attention)}</b> — ${frappe.utils.escape_html(
					frm.doc.attention_detail || "",
				)}`,
				frm.doc.attention === "Field Missing" ? "red" : "orange",
			);
			if (frm.doc.attention === "Mapping Required") {
				frm.add_custom_button(__("Apply New Defaults"), () => applyDefaults(frm), __("Run"));
			}
			return;
		}

		if (frm.doc.last_test_status === "Passed") {
			frm.dashboard.set_headline_alert(
				__("Rehearsed {0}. Changing the doctype, direction, key or field map means rehearsing again.", [
					frappe.datetime.prettyDate(frm.doc.last_test_at),
				]),
				"green",
			);
		} else if (frm.doc.last_test_status === "Failed") {
			frm.dashboard.set_headline_alert(
				__("The last rehearsal failed. Open the Rehearsal Report below for what went wrong."),
				"red",
			);
		} else if (!frm.doc.enabled) {
			frm.dashboard.set_headline_alert(
				__("Not rehearsed yet. Test → Rehearse and Enable will try it and switch it on if it holds up."),
				"orange",
			);
		}
	},
});

function busy(message) {
	frappe.dom.freeze(message);
}

function done() {
	frappe.dom.unfreeze();
}

function rehearse(frm) {
	busy(__("Rehearsing…"));
	frappe.call({
		method: "medusync.studio.rehearse",
		args: { mapping_name: frm.doc.name },
		callback: (r) => {
			done();
			showReport(frm, r.message, __("Rehearsal"));
		},
		error: () => done(),
	});
}

function testAndEnable(frm) {
	busy(__("Rehearsing…"));
	frappe.call({
		method: "medusync.studio.test_and_enable",
		args: { mapping_name: frm.doc.name },
		callback: (r) => {
			done();
			const result = r.message || {};
			showReport(frm, result, result.passed ? __("Rehearsed and enabled") : __("Not enabled"));
			frm.reload_doc();
		},
		error: () => done(),
	});
}

function sendTest(frm) {
	frappe.confirm(
		__(
			"Send a real signed request to every connected store, marked as a test. The store checks the signature and reports what it would do, but writes nothing. Continue?",
		),
		() => {
			busy(__("Sending…"));
			frappe.call({
				method: "medusync.studio.send_test",
				args: { mapping_name: frm.doc.name },
				callback: (r) => {
					done();
					const result = r.message || {};
					if (!result.ok) {
						frappe.msgprint({
							title: __("Nothing sent"),
							message: frappe.utils.escape_html(result.message || ""),
							indicator: "orange",
						});
						return;
					}
					frappe.msgprint({
						title: __("Test event sent"),
						indicator: "blue",
						message: __(
							"Sent <b>{0}</b>. Open <a href='/app/medusync-log?is_test=1'>Medusync Log</a> (Test Run) for what each store answered. These rows are pruned within a day.",
							[frappe.utils.escape_html(result.event_sent || "")],
						),
					});
				},
				error: () => done(),
			});
		},
	);
}

function showSample(frm) {
	if (!frm.doc.document_type) {
		frappe.msgprint(__("Pick a Document Type first."));
		return;
	}
	busy(__("Reading a record…"));
	frappe.call({
		method: "medusync.studio.get_sample",
		args: { doctype: frm.doc.document_type },
		callback: (r) => {
			done();
			const sample = r.message || {};
			const origin = sample.from_record
				? __("A real {0}: {1}", [frm.doc.document_type, sample.name])
				: __("No {0} exists yet, so this one is made up from the form's own definition.", [
						frm.doc.document_type,
				  ]);
			new frappe.ui.Dialog({
				title: __("Sample record"),
				size: "large",
				fields: [
					{ fieldtype: "HTML", options: `<p class="text-muted">${frappe.utils.escape_html(origin)}</p>` },
					{
						fieldtype: "Code",
						options: "JSON",
						read_only: 1,
						default: JSON.stringify(sample.data || {}, null, 2),
					},
				],
			}).show();
		},
		error: () => done(),
	});
}

function showReport(frm, result, title) {
	result = result || {};
	const lines = [];

	const list = (items, className) =>
		items && items.length
			? `<ul class="${className}">${items
					.map((i) => `<li>${frappe.utils.escape_html(String(i))}</li>`)
					.join("")}</ul>`
			: "";

	lines.push(
		result.passed
			? `<p class="text-success"><b>${__("Nothing objected.")}</b></p>`
			: `<p class="text-danger"><b>${__("This would not work as written.")}</b></p>`,
	);
	if (result.errors && result.errors.length) {
		lines.push(`<p><b>${__("Errors")}</b></p>`, list(result.errors, "text-danger"));
	}
	if (result.warnings && result.warnings.length) {
		lines.push(`<p><b>${__("Worth knowing")}</b></p>`, list(result.warnings, "text-warning"));
	}

	if (result.outbound && result.outbound.ok) {
		const out = result.outbound;
		const sites = (out.sites || [])
			.map((s) => `${s.site_id}${s.allowed ? "" : " (excluded)"}`)
			.join(", ");
		lines.push(
			`<hr><p><b>${__("Leaving here")}</b></p>`,
			`<p class="text-muted small">${__("Events")}: ${frappe.utils.escape_html(
				(out.events || []).join(", ") || "—",
			)} &nbsp;·&nbsp; ${__("Stores")}: ${frappe.utils.escape_html(sites || "—")}</p>`,
			`<pre class="small">${frappe.utils.escape_html(JSON.stringify(out.payload || {}, null, 2))}</pre>`,
		);
	}

	if (result.inbound && result.inbound.ok) {
		const inb = result.inbound;
		const changes = Object.entries(inb.changes || {})
			.map(
				([field, change]) =>
					`<tr><td>${frappe.utils.escape_html(field)}</td>` +
					`<td class="text-muted">${frappe.utils.escape_html(String(change.before ?? ""))}</td>` +
					`<td>${frappe.utils.escape_html(String(change.after ?? ""))}</td></tr>`,
			)
			.join("");
		lines.push(
			`<hr><p><b>${__("Arriving here")}</b></p>`,
			`<p class="text-muted small">${__("Would")} ${frappe.utils.escape_html(inb.action || "")}` +
				(inb.existing ? ` <b>${frappe.utils.escape_html(inb.existing)}</b>` : "") +
				(inb.reason ? ` — ${frappe.utils.escape_html(inb.reason)}` : "") +
				`</p>`,
			changes
				? `<table class="table table-bordered small"><thead><tr><th>${__("Field")}</th><th>${__(
						"Before",
				  )}</th><th>${__("After")}</th></tr></thead><tbody>${changes}</tbody></table>`
				: `<p class="text-muted small">${__("Nothing would change.")}</p>`,
		);
	}

	new frappe.ui.Dialog({
		title: title,
		size: "large",
		fields: [{ fieldtype: "HTML", options: lines.join("") }],
	}).show();
}

// ── Running it by hand ───────────────────────────────────────────────
//
// For the day a mapping goes live and the store has never heard of the
// two thousand records that already exist, and for the morning after an
// outage. Both go through the ordinary path — same payload builder, same
// selection rules, same log, same backoff — because the thing people
// reach for when something is already wrong is the worst place to keep a
// second implementation of the sync.

function pushAll(frm) {
    if (!frm.doc.enabled) {
        frappe.msgprint({
            title: __("Not yet"),
            indicator: "orange",
            message: __(
                "This mapping is switched off. Rehearse and enable it first — a bulk push through a rule nobody has reviewed is what the gate exists to prevent.",
            ),
        });
        return;
    }
    const dialog = new frappe.ui.Dialog({
        title: __("Push everything now"),
        fields: [
            {
                fieldtype: "HTML",
                options: `<p class="text-muted">${__(
                    "Sends every {0} this mapping covers, once. Leave the limit at 0 for all of them; set a small number first if you want to watch a few land before committing to the rest.",
                    [frappe.utils.escape_html(frm.doc.document_type)],
                )}</p>`,
            },
            { fieldtype: "Int", fieldname: "limit", label: __("Limit"), default: 0 },
        ],
        primary_action_label: __("Push"),
        primary_action: (values) => {
            dialog.hide();
            frappe.call({
                method: "medusync.manual.push_all",
                args: { mapping_name: frm.doc.name, limit: values.limit || 0 },
                freeze: true,
                freeze_message: __("Queueing…"),
                callback: (r) => {
                    const out = r.message || {};
                    frappe.msgprint({
                        title: out.ok ? __("Queued") : __("Nothing sent"),
                        indicator: out.ok ? "green" : "orange",
                        message: out.ok
                            ? __("{0} queued. Watch Medusync Log for what each store answered.", [
                                  out.queued ?? out.count ?? __("The records"),
                              ])
                            : frappe.utils.escape_html(out.message || ""),
                    });
                },
            });
        },
    });
    dialog.show();
}

function resyncFailed(frm) {
    frappe.confirm(
        __(
            "Put every delivery that gave up back in the queue, with its attempts reset? Rehearsals are never re-sent.",
        ),
        () => {
            frappe.call({
                method: "medusync.manual.resync_failed",
                args: {},
                freeze: true,
                callback: (r) => {
                    const out = r.message || {};
                    frappe.msgprint({
                        title: __("Back in the queue"),
                        indicator: out.count ? "blue" : "grey",
                        message: out.count
                            ? __("{0} delivery(s) will be tried again within the minute.", [out.count])
                            : __("Nothing had given up. There was nothing to re-send."),
                    });
                },
            });
        },
    );
}

function applyDefaults(frm) {
    frappe.confirm(
        __(
            "Replace this mapping with the shipped default, discarding the changes made to it? Everything else is untouched.",
        ),
        () => {
            frappe.call({
                method: "medusync.defaults.apply_now",
                args: { force: 1 },
                freeze: true,
                callback: () => {
                    frappe.show_alert({ message: __("Defaults applied."), indicator: "green" });
                    frm.reload_doc();
                },
            });
        },
    );
}
