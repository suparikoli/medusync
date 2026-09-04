// Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
// For license information, please see license.txt

// The hard reset, from the side that has to explain itself.
//
// Two systems, two secrets, three minutes. Neither can reset itself and
// neither can reset the other, so the dialog is a handshake rather than a
// confirmation: it shows the secret this side generated exactly once, and
// asks for the one the store generated. Only when both have crossed does
// the reset button appear.

frappe.ui.form.on("Medusync Site", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(
			__("Hard Reset…"),
			() => openResetDialog(frm),
			__("Danger Zone"),
		);
		frm.page.set_inner_btn_group_as_primary?.(__("Danger Zone"));
	},
});

function openResetDialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Hard reset: {0}", [frm.doc.title || frm.doc.site_id]),
		size: "large",
		fields: [
			{ fieldtype: "HTML", fieldname: "intro" },
			{ fieldtype: "Section Break" },
			{ fieldtype: "HTML", fieldname: "ours" },
			{ fieldtype: "Section Break" },
			{
				fieldtype: "Data",
				fieldname: "their_secret",
				label: __("The secret the store showed you"),
				description: __(
					"Ask the Medusa admin for a reset of this connection. It will show a secret once. Paste it here.",
				),
			},
			{ fieldtype: "HTML", fieldname: "state" },
		],
		primary_action_label: __("Generate our secret"),
		primary_action: () => generate(frm, dialog),
	});

	dialog.fields_dict.intro.$wrapper.html(`
		<div class="text-muted" style="line-height:1.6">
			<p>${__("A reset puts the shipped mappings back and switches everything off. It keeps:")}</p>
			<ul>
				<li>${__("every business document, and every Medusa id recorded on it")}</li>
				<li>${__("this store's record and its two secrets")}</li>
				<li>${__("the Don't Sync list, and the warehouse and price-list maps")}</li>
			</ul>
			<p>${__("It clears the sync log and switches off every mapping, including ones written by hand. Nothing is deleted except the log.")}</p>
			<p><b>${__("Both systems have to agree.")}</b> ${__(
				"Each generates a secret, shows it once, and has to be handed the other's. A secret lasts three minutes and works once.",
			)}</p>
		</div>
	`);
	dialog.show();
}

let live = null;
let ticker = null;

function generate(frm, dialog) {
	frappe.call({
		method: "medusync.reset.request",
		args: { site_id: frm.doc.site_id },
		freeze: true,
		callback: (r) => {
			live = r.message;
			showSecret(dialog, live);
			dialog.set_primary_action(__("Send the store's secret back"), () =>
				confirmRemote(dialog),
			);
			startTicker(dialog);
		},
	});
}

function showSecret(dialog, request) {
	dialog.fields_dict.ours.$wrapper.html(`
		<label class="control-label">${__("Our secret — copy it into the store now")}</label>
		<div class="alert alert-warning" style="word-break:break-all;font-family:monospace">
			${frappe.utils.escape_html(request.secret)}
		</div>
		<p class="text-muted small">${__(
			"Shown once. It is not stored and never reaches any log, so if you lose it, generate another.",
		)}</p>
	`);
}

function startTicker(dialog) {
	if (ticker) clearInterval(ticker);
	ticker = setInterval(() => {
		if (!live || !dialog.$wrapper.is(":visible")) {
			clearInterval(ticker);
			ticker = null;
			return;
		}
		frappe.call({
			method: "medusync.reset.status",
			args: { name: live.name },
			callback: (r) => renderState(dialog, r.message),
		});
	}, 5000);
}

function renderState(dialog, state) {
	const tick = (yes) =>
		yes
			? `<span class="text-success">&#10003;</span>`
			: `<span class="text-muted">&#8212;</span>`;
	dialog.fields_dict.state.$wrapper.html(`
		<div class="text-muted small" style="line-height:1.8">
			<div>${tick(state.local_verified_at)} ${__("The store proved it holds our secret")}</div>
			<div>${tick(state.remote_confirmed_at)} ${__("We proved we hold the store's secret")}</div>
			<div>${__("Status")}: <b>${frappe.utils.escape_html(state.status)}</b>
			 &nbsp;·&nbsp; ${__("{0}s left", [state.seconds_left])}</div>
		</div>
	`);
	if (state.ready && state.status !== "Completed") {
		dialog.set_primary_action(__("Reset now"), () => perform(dialog));
	}
}

function confirmRemote(dialog) {
	const secret = (dialog.get_value("their_secret") || "").trim();
	if (!secret) {
		frappe.msgprint(__("Paste the secret the store showed you first."));
		return;
	}
	frappe.call({
		method: "medusync.reset.confirm_remote",
		args: { name: live.name, secret },
		freeze: true,
		callback: (r) => {
			const out = r.message || {};
			if (!out.ok) {
				frappe.msgprint({
					title: __("The store did not accept it"),
					message: frappe.utils.escape_html(out.reason || ""),
					indicator: "red",
				});
				return;
			}
			frappe.show_alert({ message: __("The store accepted it."), indicator: "green" });
			frappe.call({
				method: "medusync.reset.status",
				args: { name: live.name },
				callback: (s) => renderState(dialog, s.message),
			});
		},
	});
}

function perform(dialog) {
	frappe.confirm(
		__("Reset this connection now? The log goes, every mapping switches off, and the shipped mappings come back."),
		() => {
			frappe.call({
				method: "medusync.reset.perform_in_background",
				args: { name: live.name },
				freeze: true,
				callback: () => {
					dialog.hide();
					frappe.msgprint({
						title: __("Reset started"),
						indicator: "blue",
						message: __(
							"It runs in the background. The Medusync Reset Request record will say what was done. Every mapping is now switched off; rehearse and enable the ones you want.",
						),
					});
				},
			});
		},
	);
}
