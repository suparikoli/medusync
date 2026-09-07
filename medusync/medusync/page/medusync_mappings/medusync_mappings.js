// Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
// For license information, please see license.txt

// The mapping list, as the same table the Medusa admin shows.
//
// One configuration lives in two systems, so the two lists of it should
// read the same. The Medusa side has always shown what a mapping *does* —
// which entity, which doctype, which way, how many pairs, when it last ran,
// and a switch. This side showed a stock Frappe list view: titles, a Status
// column, and nothing you could act on without opening each row.
//
// The columns here are deliberately the Medusa ones, in the Medusa order.
// Anything computed lives in `medusync.portal`, so both lists agree on what
// a mapping is rather than each deciding for itself.

frappe.pages["medusync-mappings"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Mappings"),
		single_column: true,
	});
	new MedusyncMappingList(page);
};

const MM_DIR_LABEL = {
	"Two-way": __("both ways"),
	"To Medusa": __("to the store"),
	"From Medusa": __("from the store"),
};

class MedusyncMappingList {
	constructor(page) {
		this.page = page;
		this.$body = $('<div class="mml"></div>').appendTo(page.body);

		page.set_primary_action(__("Add a sync"), () => this.create());
		page.add_menu_item(__("Connected stores"), () =>
			frappe.set_route("List", "Medusync Site"),
		);
		page.add_menu_item(__("Settings"), () =>
			frappe.set_route("Form", "Medusync Settings"),
		);
		page.add_menu_item(__("Sync log"), () => frappe.set_route("List", "Medusync Log"));
		page.add_menu_item(__("Send all mappings to the store"), () => this.syncNow());

		this.load();
	}

	/** A mapping travels when it is saved; this sends the whole list, so
	 *  a store connected later, or one that lost a mapping, reads the
	 *  same list as this side. */
	syncNow() {
		frappe.call({
			method: "medusync.mapping_sync.sync_now",
			freeze: true,
			freeze_message: __("Sending every mapping to the connected stores…"),
			callback: (r) => {
				const n = (r.message || {}).pushed || 0;
				frappe.show_alert({
					message: __("{0} mapping(s) queued for every connected store.", [n]),
					indicator: "green",
				});
				this.load();
			},
		});
	}

	load() {
		this.$body.html(`<div class="text-muted" style="padding:24px">${__("Loading…")}</div>`);
		frappe.call({
			method: "medusync.portal.list_mappings",
			callback: (r) => {
				this.data = r.message || { items: [], active: 0, sites: [] };
				this.render();
			},
			error: () => {
				this.$body.html(
					`<div class="text-muted" style="padding:24px">${__("Could not read the mappings.")}</div>`,
				);
			},
		});
	}

	esc(v) {
		return frappe.utils.escape_html(String(v == null ? "" : v));
	}

	render() {
		const items = this.data.items || [];
		this.$body.html(`
			${this.styles()}
			<div class="mml-head">
				<div class="mml-count">
					${__("{0} active", [this.data.active])}
					<span class="text-muted">${__("of {0}", [items.length])}</span>
				</div>
				<div class="mml-stores text-muted">
					${
						(this.data.sites || []).length
							? __("Stores: {0}", [(this.data.sites || []).map((s) => this.esc(s)).join(", ")])
							: `<span class="mml-nostore">${__("No store connected yet")}</span>`
					}
				</div>
			</div>
			${
				items.length
					? `<div class="mml-table">
							<div class="mml-row mml-th">
								<div class="c-name">${__("Name")}</div>
								<div class="c-ent">${__("Medusa")}</div>
								<div class="c-dt">${__("Frappe doctype")}</div>
								<div class="c-dir">${__("Direction")}</div>
								<div class="c-pairs">${__("Pairs")}</div>
								<div class="c-run">${__("Last run")}</div>
								<div class="c-on">${__("Enabled")}</div>
								<div class="c-del"></div>
							</div>
							${items.map((m) => this.rowHtml(m)).join("")}
						</div>`
					: `<div class="mml-empty">
							<div style="font-size:15px;margin-bottom:4px">${__("Nothing is syncing yet")}</div>
							<div class="text-muted">${__("Add a sync to keep a doctype and a store record in step.")}</div>
						</div>`
			}
		`);
		this.bind();
	}

	rowHtml(m) {
		const attention = m.attention
			? `<span class="mml-flag" title="${this.esc(m.attention)}">!</span>`
			: "";
		const untested =
			!m.enabled && m.test_status !== "Passed"
				? `<span class="mml-muted-pill">${__("not rehearsed")}</span>`
				: "";
		return `
			<div class="mml-row" data-name="${this.esc(m.name)}">
				<div class="c-name">
					<a class="mml-open" href="#">${this.esc(m.title)}</a>${attention}
					${m.site ? `<div class="mml-sub">${this.esc(m.site)}</div>` : ""}
				</div>
				<div class="c-ent">${this.esc(m.medusa_entity) || "—"}</div>
				<div class="c-dt">${this.esc(m.doctype) || "—"}</div>
				<div class="c-dir">${this.esc(MM_DIR_LABEL[m.direction] || m.direction)}</div>
				<div class="c-pairs">${m.pairs}</div>
				<div class="c-run text-muted">
					${m.last_run ? this.esc(frappe.datetime.str_to_user(m.last_run)) : __("never")} ${untested}
				</div>
				<div class="c-on">
					<label class="mml-switch">
						<input type="checkbox" class="mml-toggle" ${m.enabled ? "checked" : ""}>
						<span></span>
					</label>
				</div>
				<div class="c-del"><button class="mml-x mml-del" title="${__("Delete")}">🗑</button></div>
			</div>`;
	}

	bind() {
		const $b = this.$body;
		const nameOf = (el) => $(el).closest(".mml-row").data("name");

		$b.find(".mml-open").on("click", (e) => {
			e.preventDefault();
			this.edit(nameOf(e.currentTarget));
		});

		$b.find(".mml-toggle").on("change", (e) => {
			const name = nameOf(e.currentTarget);
			const wanted = e.currentTarget.checked ? 1 : 0;
			frappe.call({
				method: "medusync.portal.set_enabled",
				args: { name, enabled: wanted },
				callback: () => this.load(),
				error: () => {
					// The enable gate refuses a mapping nobody has rehearsed.
					// Put the switch back rather than leaving it showing a
					// state the server did not accept.
					e.currentTarget.checked = !wanted;
				},
			});
		});

		$b.find(".mml-del").on("click", (e) => {
			const name = nameOf(e.currentTarget);
			frappe.confirm(
				__(
					"Delete <b>{0}</b>? It is removed from the store it is paired with as well.",
					[frappe.utils.escape_html(name)],
				),
				() =>
					frappe.call({
						method: "medusync.portal.delete_mapping",
						args: { name },
						callback: () => {
							frappe.show_alert({ message: __("Deleted."), indicator: "green" });
							this.load();
						},
					}),
			);
		});
	}

	/** Open the mapper on a mapping without leaving the list. The form is
	 *  still there for everything the mapper does not cover. */
	edit(name) {
		frappe.model.with_doc("Medusync Mapping", name, () => {
			const doc = frappe.get_doc("Medusync Mapping", name);
			// The mapper writes through a form-ish object: enough of one to
			// hold a draft, and a save that goes back through the doctype so
			// every rule on it still applies.
			const frm = {
				doc,
				add_child: (table, values) => {
					const row = frappe.model.add_child(doc, "Medusync Field Map", table);
					Object.assign(row, values);
					return row;
				},
				clear_table: (table) => {
					doc[table] = [];
				},
				refresh_field: () => {},
				set_value: (field, value) => {
					doc[field] = value;
				},
			};
			const after = () => {
				frappe.call({
					method: "frappe.client.save",
					args: { doc },
					freeze: true,
					freeze_message: __("Saving…"),
					callback: () => {
						frappe.show_alert({
							message: __("Saved. Rehearse it before switching it on."),
							indicator: "green",
						});
						this.load();
					},
				});
			};
			medusync.openFieldMapper(frm, after);
		});
	}

	create() {
		const d = new frappe.ui.Dialog({
			title: __("Add a sync"),
			fields: [
				{
					fieldname: "title",
					label: __("Name it"),
					fieldtype: "Data",
					reqd: 1,
					description: __("What this keeps in step, in your words."),
				},
				{
					fieldname: "document_type",
					label: __("Frappe doctype"),
					fieldtype: "Link",
					options: "DocType",
					reqd: 1,
				},
				{
					fieldname: "medusa_entity",
					label: __("Medusa entity"),
					fieldtype: "Select",
					reqd: 1,
					options: [],
				},
				{
					fieldname: "direction",
					label: __("Which way"),
					fieldtype: "Select",
					reqd: 1,
					default: "Two-way",
					options: ["Two-way", "To Medusa", "From Medusa"].join("\n"),
				},
				{
					fieldname: "key_field",
					label: __("Key field"),
					fieldtype: "Data",
					default: "name",
					description: __(
						"How the two sides agree a record is the same one. Prefer the Medusa link field over a business value.",
					),
				},
			],
			primary_action_label: __("Create"),
			primary_action: (values) => {
				frappe.call({
					method: "frappe.client.insert",
					args: {
						doc: Object.assign({ doctype: "Medusync Mapping", enabled: 0 }, values),
					},
					freeze: true,
					callback: (r) => {
						d.hide();
						this.load();
						// Straight into the mapper: a mapping with no field
						// pairs does nothing, so the next step is never in
						// doubt.
						if (r.message && r.message.name) this.edit(r.message.name);
					},
				});
			},
		});

		// The entity list comes from the store, not from anything shipped —
		// two stores with different modules offer different entities.
		frappe.call({
			method: "medusync.medusa_fields.entities",
			callback: (r) => {
				const list = ((r.message || {}).entities || []).map((e) => e.key);
				d.set_df_property("medusa_entity", "options", list.join("\n"));
			},
			error: () => {
				d.set_df_property(
					"medusa_entity",
					"description",
					__("Could not reach the store, so type the entity key by hand."),
				);
				d.set_df_property("medusa_entity", "fieldtype", "Data");
				d.refresh();
			},
		});

		d.show();
	}

	styles() {
		return `
			<style>
				.mml { padding: 4px 0 24px; }
				.mml-head { display:flex; align-items:baseline; gap:12px; margin-bottom:12px; }
				.mml-count { font-weight:600; }
				.mml-nostore { color:var(--red-600); }
				.mml-table { border:1px solid var(--border-color); border-radius:10px; overflow:hidden; }
				.mml-row { display:flex; align-items:center; gap:10px; padding:10px 14px;
					border-bottom:1px solid var(--border-color); }
				.mml-row:last-child { border-bottom:none; }
				.mml-row:not(.mml-th):hover { background:var(--bg-light-gray); }
				.mml-th { background:var(--bg-light-gray); font-size:11px; text-transform:uppercase;
					letter-spacing:.04em; color:var(--text-muted); }
				.c-name { flex:2 1 0; min-width:0; }
				.c-ent, .c-dt, .c-dir { flex:1.2 1 0; min-width:0; }
				.c-pairs { flex:0 0 60px; }
				.c-run { flex:1.4 1 0; min-width:0; font-size:12px; }
				.c-on { flex:0 0 70px; }
				.c-del { flex:0 0 30px; text-align:right; }
				.mml-sub { font-size:11px; color:var(--text-muted); }
				.mml-flag { display:inline-block; margin-left:6px; width:16px; height:16px;
					line-height:16px; text-align:center; border-radius:8px;
					background:var(--red-50); color:var(--red-600); font-size:11px; }
				.mml-muted-pill { font-size:10px; padding:1px 6px; border-radius:8px;
					background:var(--bg-light-gray); color:var(--text-muted); margin-left:4px; }
				.mml-x { border:none; background:transparent; cursor:pointer; opacity:.5; }
				.mml-x:hover { opacity:1; }
				.mml-empty { text-align:center; padding:56px 20px; border:1px dashed var(--border-color);
					border-radius:10px; }
				.mml-switch { position:relative; display:inline-block; width:34px; height:19px; margin:0; }
				.mml-switch input { opacity:0; width:0; height:0; }
				.mml-switch span { position:absolute; inset:0; cursor:pointer; border-radius:19px;
					background:var(--gray-300); transition:.15s; }
				.mml-switch span:before { content:""; position:absolute; height:15px; width:15px;
					left:2px; top:2px; background:#fff; border-radius:50%; transition:.15s; }
				.mml-switch input:checked + span { background:var(--blue-500); }
				.mml-switch input:checked + span:before { transform:translateX(15px); }
			</style>`;
	}
}
