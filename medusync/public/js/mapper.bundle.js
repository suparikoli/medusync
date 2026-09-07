// Copyright (c) 2026, Mithtech Innovative Solutions PVT LTD and contributors
// For license information, please see license.txt

// Shared across the app, because two places need it: the Medusync Mapping
// form, and the Mappings page that replaced the plain list view. Living in
// the doctype's own JS meant only the form could open it.

frappe.provide("medusync");

// ── The field mapper ─────────────────────────────────────────────────
//
// The grid is an honest record of a mapping and a poor way to build one.
// It asks for a Frappe fieldname and a Medusa dot-path as free text, offers
// neither list, and never says which fields are mandatory — so the usual
// failure is a mapping that saves, rehearses, and is then refused by the
// document because a required field was never mapped.
//
// Here both sides are dropdowns. Each lists its required fields first,
// under a "Required" group, with the rest below a separator — so what has
// to be filled in is the first thing offered rather than something to go
// looking for. The Medusa side is fetched live through
// `medusync.medusa_fields`, which is the first time this side has been able
// to see the other's fields at all.
//
// Nothing reaches the document until Apply.

const MM_EVENTS = [
	{ value: "after_insert", label: __("Created") },
	{ value: "on_update", label: __("Updated") },
	{ value: "on_submit", label: __("Submitted") },
	{ value: "on_cancel", label: __("Cancelled") },
	{ value: "on_trash", label: __("Deleted") },
	{ value: "on_update_after_submit", label: __("Updated after submit") },
];


const mmEsc = (v) => frappe.utils.escape_html(String(v == null ? "" : v));

/** One <select> whose required options sit above a separator. */
function mmSelect(cls, value, groups, placeholder) {
	const opt = (o) =>
		`<option value="${mmEsc(o.value)}"${o.value === value ? " selected" : ""}${
			o.disabled ? " disabled" : ""
		}${o.title ? ` title="${mmEsc(o.title)}"` : ""}>${mmEsc(o.label)}</option>`;
	const body = groups
		.filter((g) => g.options.length)
		.map(
			(g) =>
				`<optgroup label="${mmEsc(g.label)}">${g.options.map(opt).join("")}</optgroup>`,
		)
		.join("");
	// A value the lists do not contain is still legal — the engine walks
	// any dotted path — so keep it selectable rather than silently dropping
	// what somebody already wrote.
	const known = groups.some((g) => g.options.some((o) => o.value === value));
	const orphan =
		value && !known
			? `<optgroup label="${__("Already in this mapping")}"><option value="${mmEsc(value)}" selected>${mmEsc(value)}</option></optgroup>`
			: "";
	return `<select class="form-control input-xs ${cls}">
		<option value=""${value ? "" : " selected"}>${mmEsc(placeholder)}</option>
		${orphan}${body}
	</select>`;
}

class MedusyncFieldMapper {
	constructor(frm, onApply) {
		this.frm = frm;
		// The form saves itself; the Mappings page has no form to save, so
		// it hands in what to do once Apply has written the draft.
		this.onApply = onApply || null;
		/** Accepted values per ERPNext fieldname, for fixed-value rows.
		 *  A constant usually targets a Link — `company` above all — and a
		 *  free text box is how a wrong one survives until the first push. */
		this.optionsFor = {};
		this.here = [];
		this.there = [];
		this.note = "";
		this.rows = (frm.doc.field_map || []).map((r) => ({
			frappe_field: r.frappe_field || "",
			medusa_path: r.medusa_path || "",
			constant_value: r.constant_value || "",
			direction: r.direction || "Two-way",
		}));
		this.events = new Set(
			(frm.doc.docevents || "")
				.replace(/,/g, "\n")
				.split("\n")
				.map((s) => s.trim())
				.filter(Boolean),
		);
	}

	show() {
		this.dialog = new frappe.ui.Dialog({
			title: __("Map {0} ↔ {1}", [this.frm.doc.document_type, this.frm.doc.medusa_entity]),
			size: "extra-large",
			fields: [{ fieldtype: "HTML", fieldname: "body" }],
			primary_action_label: __("Apply"),
			primary_action: () => this.apply(),
		});
		this.$body = this.dialog.fields_dict.body.$wrapper;
		this.dialog.show();
		this.$body.html(`<div class="text-muted p-4">${__("Reading both sides…")}</div>`);
		this.load();
	}

	async load() {
		try {
			const here = await frappe.call({
				method: "medusync.studio.get_fields",
				args: { doctype: this.frm.doc.document_type },
			});
			this.here = here.message || [];
		} catch (e) {
			this.note = __("Could not read this doctype's fields.");
		}
		try {
			const there = await frappe.call({
				method: "medusync.medusa_fields.fields",
				args: {
					entity: this.frm.doc.medusa_entity,
					site_id: this.frm.doc.site || null,
				},
			});
			this.there = (there.message || {}).fields || [];
			if ((there.message || {}).fields_source === "curated") {
				this.note = __(
					"The store could not read its own model, so its list is only what its connector curates and may be short.",
				);
			}
		} catch (e) {
			// Mapping by hand is what everyone did until now, so degrade to
			// a free-text path rather than refusing to open.
			this.note =
				(e && e.message) ||
				__("Could not reach the store, so its fields are not listed.");
		}
		await Promise.all(
			this.rows
				.filter((r) => String(r.constant_value).trim())
				.map((r) => this.loadOptions(r.frappe_field)),
		);
		this.render();
	}

	/** Frappe fields, required first. Anything already used elsewhere is
	 *  shown but disabled, so one column cannot be mapped twice. */
	hereGroups(currentValue) {
		const taken = new Set(
			this.rows.map((r) => r.frappe_field).filter((f) => f && f !== currentValue),
		);
		const mk = (f) => ({
			value: f.fieldname,
			label: `${f.label || f.fieldname} · ${f.fieldname}`,
			disabled: taken.has(f.fieldname),
		});
		return [
			{ label: __("Required"), options: this.here.filter((f) => f.reqd).map(mk) },
			{ label: "──────────", options: this.here.filter((f) => !f.reqd).map(mk) },
		];
	}

	/** Medusa paths, required first, then grouped by the record they
	 *  belong to. The store says which are required: a column with no
	 *  default that it will not create a record without — not merely NOT
	 *  NULL, which would list `id` and every timestamp. Bookkeeping
	 *  columns and second names for the same thing stay out of sight
	 *  until asked for, except one a row already uses. */
	thereGroups(currentValue) {
		const own = this.frm.doc.medusa_entity || __("the store");
		const visible = this.there.filter(
			(f) => this.showAll || !f.advanced || f.path === currentValue,
		);
		const mk = (f) => ({
			value: f.path,
			label: `${f.label || f.path} · ${f.path}`,
			title: f.description || "",
		});
		const groups = [{ label: __("Required"), options: visible.filter((f) => f.required).map(mk) }];
		for (const f of visible.filter((f) => !f.required)) {
			const label = f.group || own;
			let group = groups.find((g) => g.label === label);
			if (!group) {
				group = { label, options: [] };
				groups.push(group);
			}
			group.options.push(mk(f));
		}
		return groups;
	}

	hiddenThere() {
		return this.there.filter((f) => f.advanced).length;
	}

	/** Data reaches this ERPNext through this mapping. */
	flowsHere() {
		return ["Two-way", "From Medusa"].includes(this.frm.doc.direction);
	}

	/** Data reaches the store through this mapping. */
	flowsThere() {
		return ["Two-way", "To Medusa"].includes(this.frm.doc.direction);
	}

	/** What ERPNext will not create a record without, that is ours to
	 *  fill. A field Frappe derives from a link or defaults is not; nor is
	 *  `name`, which the naming rule sets and the Key Field correlates. */
	requiredHere() {
		return this.here.filter(
			(f) => f.reqd && f.fieldname !== "name" && !f.fetch_from && !f.default,
		);
	}

	/** What the store will not create a record without. It says which: a
	 *  column with no default — not merely NOT NULL, which would list `id`
	 *  and every timestamp. */
	requiredThere() {
		return this.there.filter((f) => f.required);
	}

	/** Covered when a pair writes it INTO this side: a fixed value, or a
	 *  store field on a row that flows this way. */
	coveredHere(f) {
		return this.rows.some(
			(r) =>
				r.frappe_field === f.fieldname &&
				(String(r.constant_value).trim() ||
					(r.medusa_path && ["Two-way", "From Medusa"].includes(r.direction))),
		);
	}

	coveredThere(f) {
		return this.rows.some(
			(r) =>
				r.medusa_path === f.path &&
				r.frappe_field &&
				!String(r.constant_value).trim() &&
				["Two-way", "To Medusa"].includes(r.direction),
		);
	}

	missingRequired() {
		return this.requiredHere().filter((f) => !this.coveredHere(f));
	}

	missingRequiredThere() {
		return this.requiredThere().filter((f) => !this.coveredThere(f));
	}

	styles() {
		return `
			<style>
				.mm { font-size: var(--text-md); }
				.mm-head { display:flex; gap:10px; padding:0 0 8px; align-items:flex-end; }
				.mm-head .mm-side { flex:1 1 0; min-width:0; }
				.mm-head .mm-gap { flex:0 0 96px; }
				.mm-side-name { font-weight:600; }
				.mm-side-sub { color:var(--text-muted); font-size:11px; }
				.mm-banner { border-radius:8px; padding:8px 12px; margin-bottom:10px;
					display:flex; align-items:center; gap:10px; }
				.mm-ok { background:var(--green-50); color:var(--green-700); }
				.mm-warn { background:var(--yellow-50); color:var(--yellow-700); }
				.mm-bad { background:var(--red-50); color:var(--red-600); }
				.mm-banner .mm-grow { flex:1 1 auto; }
				.mm-list { max-height:46vh; overflow:auto; padding:2px; }
				.mm-row { display:flex; gap:10px; align-items:center;
					padding:6px 8px; border-radius:8px; }
				.mm-row:hover { background:var(--bg-light-gray); }
				.mm-row + .mm-row { margin-top:2px; }
				.mm-cell { flex:1 1 0; min-width:0; }
				.mm-mid { flex:0 0 96px; display:flex; gap:4px; justify-content:center; }
				.mm-dirbtn { border:1px solid var(--border-color); background:var(--card-bg);
					border-radius:6px; width:30px; height:26px; line-height:1;
					cursor:pointer; color:var(--text-muted); font-size:13px; }
				.mm-dirbtn.is-on { background:var(--bg-blue); border-color:var(--blue-300);
					color:var(--blue-600); font-weight:700; }
				.mm-x { border:none; background:transparent; color:var(--text-muted);
					cursor:pointer; padding:2px 6px; border-radius:6px; }
				.mm-x:hover { background:var(--bg-light-gray); color:var(--red-600); }
				.mm-req { display:inline-block; font-size:10px; padding:0 5px;
					border-radius:8px; background:var(--red-50); color:var(--red-600);
					margin-left:6px; vertical-align:middle; }
				.mm-empty { text-align:center; color:var(--text-muted);
					padding:22px; border:1px dashed var(--border-color); border-radius:8px; }
				.mm-foot { border-top:1px solid var(--border-color); margin-top:12px; padding-top:10px; }
				.mm-ev { display:inline-flex; align-items:center; gap:5px;
					border:1px solid var(--border-color); border-radius:14px;
					padding:3px 10px; margin:0 6px 6px 0; cursor:pointer; }
				.mm-ev.is-on { background:var(--bg-blue); border-color:var(--blue-300);
					color:var(--blue-600); }
				.mm-ev input { margin:0; }
				.mm-req { display:flex; gap:12px; margin:0 0 12px; }
				.mm-req-box { flex:1; border:1px solid var(--border-color); border-radius:6px;
					padding:8px 10px; }
				.mm-req-title { font-weight:600; margin-bottom:4px; display:flex;
					justify-content:space-between; align-items:center; }
				.mm-req-count { font-weight:400; font-size:var(--text-sm); color:var(--text-muted); }
				.mm-req-item { display:flex; gap:6px; padding:1px 0; }
				button.mm-req-item { width:100%; text-align:left; background:none; border:1px dashed var(--border-color);
					border-radius:4px; padding:2px 6px; margin:2px 0; cursor:pointer; font:inherit; }
				button.mm-req-item:hover { background:var(--bg-light-gray); border-style:solid; }
				.mm-req-ok { color:var(--green-600); }
				.mm-req-no { color:var(--red-600); }
				.mm-req-item .mm-grow { color:var(--text-color); }
			</style>`;
	}

	bannerHtml() {
		if (this.note) {
			return `<div class="mm-banner mm-warn"><span>⚠</span>
				<span class="mm-grow">${mmEsc(this.note)}</span></div>`;
		}
		return "";
	}

	/** One box per side that receives data: what it insists on, and
	 *  whether this mapping fills it. Shown only for the direction the
	 *  mapping actually moves — a sync that only sends to the store has
	 *  nothing to fill in ERPNext. */
	boxesHtml() {
		// A missing field is a button: one click adds that one row. The
		// bulk button stays for when several are missing at once.
		const box = (title, required, covered, side, addClass, emptyNote) => {
			const items = required.length
				? required
						.map((f) => {
							const ok = covered(f);
							const key = f.fieldname || f.path;
							const label = `${mmEsc(f.label || key)} <span class="text-muted">· ${mmEsc(key)}</span>`;
							return ok
								? `<div class="mm-req-item mm-req-ok"><span>✓</span><span class="mm-grow">${label}</span></div>`
								: `<button class="mm-req-item mm-req-no mm-addone" data-side="${side}" data-field="${mmEsc(key)}"
										title="${__("Add this field to the mapping")}"><span>＋</span><span class="mm-grow">${label}</span></button>`;
						})
						.join("")
				: `<div class="text-muted small">${mmEsc(emptyNote)}</div>`;
			const missing = required.filter((f) => !covered(f)).length;
			return `<div class="mm-req-box">
				<div class="mm-req-title">${mmEsc(title)}
					${missing ? `<span class="mm-req-count">${__("{0} missing", [missing])}</span>` : required.length ? `<span class="mm-req-count mm-req-ok">${__("all covered")}</span>` : ""}
				</div>
				${items}
				${missing > 1 ? `<button class="btn btn-xs btn-default ${addClass}" style="margin-top:6px">${__("Add all {0}", [missing])}</button>` : ""}
			</div>`;
		};
		const boxes = [];
		if (this.flowsHere()) {
			boxes.push(
				box(
					__("Required in ERPNext"),
					this.requiredHere(),
					(f) => this.coveredHere(f),
					"here",
					"mm-addreq",
					__("{0} has no mandatory field this mapping has to fill.", [this.frm.doc.document_type]),
				),
			);
		}
		if (this.flowsThere()) {
			boxes.push(
				box(
					__("Required in the store"),
					this.requiredThere(),
					(f) => this.coveredThere(f),
					"there",
					"mm-addreqthere",
					this.there.length
						? __("The store reports no field it cannot create this record without.")
						: __("The store's fields could not be read, so what it requires is unknown."),
				),
			);
		}
		return boxes.length ? `<div class="mm-req">${boxes.join("")}</div>` : "";
	}

	render() {
		this.$body.html(`
			${this.styles()}
			<div class="mm">
				${this.bannerHtml()}
				${this.boxesHtml()}
				<div class="mm-head">
					<div class="mm-side">
						<div class="mm-side-name">${mmEsc(this.frm.doc.document_type)}</div>
						<div class="mm-side-sub">${__("this ERPNext")} · ${this.here.length} ${__("fields")}</div>
					</div>
					<div class="mm-gap"></div>
					<div class="mm-side">
						<div class="mm-side-name">${mmEsc(this.frm.doc.medusa_entity)}</div>
						<div class="mm-side-sub">${__("the store")} · ${this.there.length} ${__("fields")}${
							this.hiddenThere()
								? ` · <label style="cursor:pointer"><input type="checkbox" class="mm-showall" ${this.showAll ? "checked" : ""}> ${__("show {0} internal", [this.hiddenThere()])}</label>`
								: ""
						}</div>
					</div>
					<div style="flex:0 0 28px"></div>
				</div>
				<div class="mm-list">${
					this.rows.length
						? this.rows.map((r, i) => this.rowHtml(r, i)).join("")
						: `<div class="mm-empty">${__("Nothing mapped yet — add a field to begin.")}</div>`
				}</div>
				<div style="margin-top:8px; display:flex; gap:6px; align-items:center">
					<button class="btn btn-xs btn-default mm-add">+ ${__("Add a field")}</button>
					<button class="btn btn-xs btn-default mm-auto">✨ ${__("Suggest matches")}</button>
					<span class="text-muted small mm-autonote">${mmEsc(this.autoNote || "")}</span>
				</div>

				<div class="mm-foot">
					<div style="font-weight:600; margin-bottom:6px">${__("Sync when the document is")}</div>
					${MM_EVENTS.map(
						(e) => `<label class="mm-ev ${this.events.has(e.value) ? "is-on" : ""}">
							<input type="checkbox" class="mm-event" value="${e.value}" ${this.events.has(e.value) ? "checked" : ""}>
							${mmEsc(e.label)}</label>`,
					).join("")}
					<div class="text-muted small" style="margin-top:2px">
						${__("Nothing ticked means this mapping never fires on a document change here.")}
					</div>
				</div>
			</div>
		`);
		this.bind();
	}

	rowHtml(r, i) {
		const useConstant = !!String(r.constant_value).trim() || r.constant_value === " ";
		const f = this.here.find((x) => x.fieldname === r.frappe_field);
		const dirBtn = (value, glyph, title) =>
			`<button class="mm-dirbtn ${r.direction === value ? "is-on" : ""}" data-dir="${value}" title="${title}">${glyph}</button>`;
		return `<div class="mm-row" data-idx="${i}">
			<div class="mm-cell">
				${mmSelect("mm-here", r.frappe_field, this.hereGroups(r.frappe_field), __("Pick a field…"))}
				${f && f.reqd ? `<span class="mm-req">${__("required")}</span>` : ""}
			</div>
			<div class="mm-mid">
				${dirBtn("To Medusa", "→", __("Only out to the store"))}
				${dirBtn("Two-way", "↔", __("Both ways"))}
				${dirBtn("From Medusa", "←", __("Only in from the store"))}
				${dirBtn("Don't Sync", "∅", __("Documented, but never moves"))}
			</div>
			<div class="mm-cell">${
				useConstant
					? this.constantControl(r)
					: mmSelect("mm-there", r.medusa_path, this.thereGroups(r.medusa_path), __("Pick a store field…"))
			}</div>
			<button class="mm-x mm-toggle" title="${useConstant ? __("Use a store field instead") : __("Send a fixed value instead")}">${useConstant ? "↩" : "="}</button>
			<button class="mm-x mm-del" title="${__("Remove")}">✕</button>
		</div>`;
	}

	/** Ask this site what a field accepts. Cached: the answer is the same
	 *  for every row targeting that field, and for the life of the dialog. */
	loadOptions(fieldname) {
		if (!fieldname || this.optionsFor[fieldname]) return Promise.resolve();
		return new Promise((resolve) => {
			frappe.call({
				method: "medusync.portal.field_options",
				args: { doctype: this.frm.doc.document_type, fieldname },
				callback: (r) => {
					this.optionsFor[fieldname] = r.message || { options: [] };
					resolve();
				},
				// A field we cannot ask about still takes a typed value.
				error: () => {
					this.optionsFor[fieldname] = { options: [] };
					resolve();
				},
			});
		});
	}

	constantControl(r) {
		const meta = this.optionsFor[r.frappe_field];
		const value = String(r.constant_value).trim();
		if (meta && (meta.options || []).length) {
			const opts = meta.options
				.map(
					(o) =>
						`<option value="${mmEsc(o)}"${o === value ? " selected" : ""}>${mmEsc(o)}</option>`,
				)
				.join("");
			// A value that is no longer in the list stays selectable rather
			// than being silently dropped from a mapping that already ran.
			const orphan =
				value && !meta.options.includes(value)
					? `<option value="${mmEsc(value)}" selected>${mmEsc(value)} — ${__("not on this site")}</option>`
					: "";
			return `<select class="form-control input-xs mm-const-sel">
				<option value="">${__("Pick a value…")}</option>${orphan}${opts}
			</select>`;
		}
		return `<input class="form-control input-xs mm-const" placeholder="${__("Fixed value, sent every time")}" value="${mmEsc(value)}">`;
	}

	idx(el) {
		return Number(el.closest(".mm-row").dataset.idx);
	}

	newRow(fieldname) {
		const f = this.here.find((x) => x.fieldname === fieldname);
		return {
			frappe_field: fieldname || "",
			// A same-named path on the other side is right often enough to
			// be worth pre-selecting, and wrong in a way that is visible.
			medusa_path:
				fieldname && this.there.some((m) => m.path === fieldname) ? fieldname : "",
			constant_value: "",
			// A field this side computes cannot be written from the store —
			// Frappe refills it — so offering Two-way would promise
			// something that never holds.
			direction: f && (f.fetch_from || f.read_only) ? "To Medusa" : this.frm.doc.direction || "Two-way",
		};
	}

	bind() {
		this.$body.on("change", ".mm-showall", (e) => {
			this.showAll = e.target.checked;
			this.render();
		});
		const $b = this.$body;
		$b.find(".mm-add").on("click", () => {
			this.rows.push(this.newRow(""));
			this.render();
		});
		$b.find(".mm-auto").on("click", () => this.automap());
		$b.find(".mm-addone").on("click", (e) => {
			const field = e.currentTarget.dataset.field;
			if (e.currentTarget.dataset.side === "here") {
				this.rows.push(this.newRow(field));
			} else {
				this.rows.push({
					frappe_field: "",
					medusa_path: field,
					constant_value: "",
					direction: this.frm.doc.direction === "Two-way" ? "Two-way" : "To Medusa",
				});
			}
			this.render();
		});
		$b.find(".mm-addreqthere").on("click", () => {
			for (const f of this.missingRequiredThere()) {
				this.rows.push({
					frappe_field: "",
					medusa_path: f.path,
					constant_value: "",
					direction: this.frm.doc.direction === "Two-way" ? "Two-way" : "To Medusa",
				});
			}
			this.render();
		});
		$b.find(".mm-addreq").on("click", () => {
			for (const f of this.missingRequired()) this.rows.push(this.newRow(f.fieldname));
			this.render();
		});
		$b.find(".mm-here").on("change", (e) => {
			const i = this.idx(e.currentTarget);
			const picked = e.target.value;
			const keep = this.rows[i];
			const wasConstant = String(keep.constant_value).trim() || keep.constant_value === " ";
			if (wasConstant) this.loadOptions(picked).then(() => this.render());
			this.rows[i] = Object.assign(this.newRow(picked), {
				// Keep what the operator already chose on the other side.
				medusa_path: keep.medusa_path || this.newRow(picked).medusa_path,
				constant_value: keep.constant_value,
				direction: keep.frappe_field ? keep.direction : this.newRow(picked).direction,
			});
			this.render();
		});
		$b.find(".mm-there").on("change", (e) => {
			this.rows[this.idx(e.currentTarget)].medusa_path = e.target.value;
		});
		$b.find(".mm-const-sel").on("change", (e) => {
			this.rows[this.idx(e.currentTarget)].constant_value = e.target.value;
		});
		$b.find(".mm-const").on("input", (e) => {
			this.rows[this.idx(e.currentTarget)].constant_value = e.target.value;
		});
		$b.find(".mm-dirbtn").on("click", (e) => {
			this.rows[this.idx(e.currentTarget)].direction = e.currentTarget.dataset.dir;
			this.render();
		});
		$b.find(".mm-toggle").on("click", (e) => {
			const r = this.rows[this.idx(e.currentTarget)];
			if (String(r.constant_value).trim() || r.constant_value === " ") {
				r.constant_value = "";
				this.render();
			} else {
				r.constant_value = " ";
				r.medusa_path = "";
				this.loadOptions(r.frappe_field).then(() => this.render());
			}
		});
		$b.find(".mm-del").on("click", (e) => {
			this.rows.splice(this.idx(e.currentTarget), 1);
			this.render();
		});
		$b.find(".mm-event").on("change", (e) => {
			if (e.target.checked) this.events.add(e.target.value);
			else this.events.delete(e.target.value);
			this.render();
		});
	}

	automap() {
		const $btn = this.$body.find(".mm-auto");
		$btn.prop("disabled", true).text(__("Matching…"));
		frappe.call({
			method: "medusync.medusa_fields.suggest",
			args: {
				entity: this.frm.doc.medusa_entity,
				doctype: this.frm.doc.document_type,
				site_id: this.frm.doc.site || null,
			},
			callback: (r) => {
				const rows = (r.message || {}).suggestions || [];
				let added = 0;
				let filled = 0;
				for (const s of rows) {
					if (!s.erpnext_field || !s.medusa_path) continue;
					// `none` is emitted for a mandatory field with no guess —
					// useful to surface, useless to auto-apply.
					if (s.confidence === "none") continue;
					const existing = this.rows.find((x) => x.frappe_field === s.erpnext_field);
					if (existing) {
						// Never overwrite a pairing somebody chose.
						if (existing.medusa_path || String(existing.constant_value).trim()) continue;
						existing.medusa_path = s.medusa_path;
						filled += 1;
					} else {
						const row = this.newRow(s.erpnext_field);
						row.medusa_path = s.medusa_path;
						if (s.transform) row.transform = s.transform;
						this.rows.push(row);
						added += 1;
					}
				}
				const weak = rows.filter((s) => s.confidence === "weak").length;
				this.autoNote = added || filled
					? __("{0} added, {1} filled in{2}.", [
							added,
							filled,
							weak ? __(" — {0} are loose guesses, check them", [weak]) : "",
					  ])
					: __("Nothing new to suggest.");
				this.render();
			},
			error: () => {
				this.autoNote = __("The store could not be asked for suggestions.");
				this.render();
			},
		});
	}

	apply() {
		const rows = this.rows.filter((r) => r.frappe_field);

		const seen = new Set();
		const repeated = new Set();
		for (const r of rows) {
			if (seen.has(r.frappe_field)) repeated.add(r.frappe_field);
			seen.add(r.frappe_field);
		}
		if (repeated.size) {
			// Two rows writing one column is not a merge — the last one wins
			// and the other silently does nothing, which is the hardest kind
			// of mapping bug to see afterwards.
			frappe.msgprint({
				title: __("The same field is mapped twice"),
				message: __("Only one row may write {0}. Remove the extra.", [
					Array.from(repeated).join(", "),
				]),
				indicator: "orange",
			});
			return;
		}
		const bad = rows.filter(
			(r) => !r.medusa_path && !String(r.constant_value).trim(),
		);
		if (bad.length) {
			frappe.msgprint({
				title: __("Some rows have no source"),
				message: __("Neither a Medusa field nor a fixed value: {0}", [
					bad.map((r) => r.frappe_field).join(", "),
				]),
				indicator: "orange",
			});
			return;
		}

		this.frm.clear_table("field_map");
		for (const r of rows) {
			const row = this.frm.add_child("field_map", {
				frappe_field: r.frappe_field,
				direction: r.direction,
			});
			if (String(r.constant_value).trim()) {
				row.constant_value = String(r.constant_value).trim();
			} else {
				row.medusa_path = r.medusa_path;
			}
		}
		this.frm.refresh_field("field_map");
		this.frm.set_value("docevents", Array.from(this.events).join("\n"));
		this.dialog.hide();
		if (this.onApply) {
			this.onApply();
			return;
		}
		frappe.show_alert({
			message: __("{0} field(s) mapped. Rehearse before switching it on.", [rows.length]),
			indicator: "green",
		});
	}
}

medusync.openFieldMapper = function (frm, onApply) {
	if (!frm.doc.document_type) {
		frappe.msgprint(__("Pick a Document Type first."));
		return;
	}
	if (!frm.doc.medusa_entity) {
		frappe.msgprint(
			__("Pick a Medusa Entity first, so we know what to offer on the other side."),
		);
		return;
	}
	new MedusyncFieldMapper(frm, onApply).show();
};
