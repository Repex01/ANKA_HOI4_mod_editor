"""Technology inspector: a scrollable form over one block-backed `Technology`.

Sections: identity · general fields · folder positions · links (paths /
dependencies / XOR) · unlocks · modifiers & stat blocks · script blocks ·
XP / doctrine · localisation + icon. Values load on ``show()``; edits commit
straight back through the model (debounced for typed fields) and notify the
owning editor, which tracks dirty documents and refreshes the canvas.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ...config.constants import SUPPORTED_IMPORT_FORMATS
from ...services.technology_service import (
    TECH_SCRIPT_FIELDS,
    MAX_SUBTECHS,
    TechDocument,
    Technology,
)
from ..common import MultiPickDialog, SinglePickDialog, TextPromptDialog
from ..common.inspector_base import InspectorBase
from ..common.script_editor import ScriptEditorDialog

_FLAGS = ("show_effect_as_desc", "force_use_small_tech_layout",
          "show_equipment_icon", "is_special_project_tech")
_SCRIPT_KINDS = {
    "allow": ("trigger",),
    "allow_branch": ("trigger",),
    "ai_will_do": ("trigger", "modifier"),
    "ai_research_weights": ("modifier",),
    "on_research_complete": ("effect",),
    "on_research_complete_limit": ("trigger",),
}
_LIST_FIELDS = ("enable_equipments", "enable_equipment_modules",
                "enable_subunits", "sub_technologies",
                "special_project_specialization")


class TechInspector(InspectorBase):
    def __init__(self, master, owner):
        super().__init__(master, owner)
        self.tech: Technology | None = None
        self.doc: TechDocument | None = None
        self._sections: list[ttk.Widget] = []
        self._loc_photo = None
        self._placeholder = ttk.Label(self.body,
                                      text=self.t("technologies.inspector.empty"),
                                      style="CardMuted.TLabel")
        self._placeholder.grid(row=0, column=0, columnspan=2, pady=24)

    # ------------------------------------------------------------------ show
    def show(self, tech: Technology | None, doc: TechDocument | None = None,
             editable: bool = True) -> None:
        self.flush_pending()
        self._loading = True
        try:
            self.tech = tech
            self.doc = doc
            self._editable = editable and tech is not None
            for w in self._sections:
                w.destroy()
            self._sections = []
            if tech is None or doc is None:
                self._placeholder.grid()
                return
            self._placeholder.grid_remove()
            self._build_form()
            if not self._editable:
                self._set_state_all(False)
        finally:
            self._loading = False

    def refresh(self) -> None:
        if self.tech is not None and self.doc is not None:
            self.show(self.tech, self.doc, self._editable)

    # --------------------------------------------------------------- helpers
    def _touch(self, canvas: bool = False) -> None:
        if self.doc is not None:
            self.owner.mark_dirty([self.doc])
        if canvas:
            self.owner.refresh_canvas()
        self.owner._validate()

    def _section(self, title: str) -> ttk.Labelframe:
        frame = ttk.Labelframe(self.body, text=title, style="Card.TLabelframe",
                               padding=(8, 6))
        row = len(self._sections) + 1
        frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        frame.columnconfigure(1, weight=1)
        self._sections.append(frame)
        return frame

    def _row_entry(self, parent, row: int, label: str, value: str,
                   commit, width: int = 14) -> tk.StringVar:
        ttk.Label(parent, text=label, style="CardMuted.TLabel").grid(
            row=row, column=0, sticky="w", pady=2)
        var = tk.StringVar(value=value)
        entry = ttk.Entry(parent, textvariable=var, width=width)
        entry.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=2)
        var.trace_add("write",
                      lambda *_: self._debounce(f"{id(parent)}:{label}",
                                                lambda: self._guarded(commit, var)))
        entry.bind("<FocusOut>", lambda e: self._guarded(commit, var))
        return var

    def _guarded(self, commit, var) -> None:
        if self._guard():
            commit(var.get().strip())

    # ------------------------------------------------------------------- form
    def _build_form(self) -> None:
        tech = self.tech
        doc = self.doc
        t = self.t

        # --- identity -------------------------------------------------------
        head = self._section(t("technologies.inspector.identity"))
        ttk.Label(head, text=tech.id, style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")
        if self._editable:
            ttk.Button(head, text=t("technologies.rename"),
                       command=self._rename).grid(row=0, column=2, sticky="e")
        badge = "" if self._editable else "🔒 " + t("technologies.readonly")
        if badge:
            ttk.Label(head, text=badge, style="CardMuted.TLabel").grid(
                row=1, column=0, columnspan=2, sticky="w")

        # --- general --------------------------------------------------------
        gen = self._section(t("technologies.inspector.general"))

        def set_cost(raw: str) -> None:
            try:
                tech.research_cost = float(raw)
            except ValueError:
                return
            self._touch()

        def set_year(raw: str) -> None:
            if not raw:
                tech.start_year = None
            else:
                try:
                    tech.start_year = int(raw)
                except ValueError:
                    return
            self._touch()

        self._row_entry(gen, 0, t("technologies.field.research_cost"),
                        f"{tech.research_cost:g}", set_cost)
        self._row_entry(gen, 1, t("technologies.field.start_year"),
                        str(tech.start_year or ""), set_year)

        def set_desc(raw: str) -> None:
            tech.desc = raw or None
            self._touch()

        self._row_entry(gen, 2, t("technologies.field.desc_key"),
                        tech.desc or "", set_desc, width=24)

        ttk.Label(gen, text=t("technologies.field.categories"),
                  style="CardMuted.TLabel").grid(row=3, column=0, sticky="nw", pady=2)
        cats_line = ttk.Label(gen, text=", ".join(tech.categories) or "—",
                              style="Card.TLabel", wraplength=240, justify="left")
        cats_line.grid(row=3, column=1, sticky="w", padx=(8, 0))
        if self._editable:
            ttk.Button(gen, text="…", width=3, command=self._pick_categories).grid(
                row=3, column=2, sticky="e")

        r = 4
        for flag in _FLAGS:
            var = tk.BooleanVar(value=tech.get_flag(flag))

            def commit_flag(f=flag, v=None) -> None:
                if self._guard():
                    tech.set_flag(f, v.get())
                    self._touch(canvas=(f == "force_use_small_tech_layout"))

            chk = ttk.Checkbutton(gen, text=t(f"technologies.flag.{flag}"),
                                  style="Card.TCheckbutton", variable=var)
            chk.configure(command=lambda f=flag, v=var: commit_flag(f, v))
            chk.grid(row=r, column=0, columnspan=3, sticky="w")
            r += 1

        def set_tactic(raw: str) -> None:
            tech.enable_tactic = raw or None
            self._touch()

        self._row_entry(gen, r, t("technologies.field.enable_tactic"),
                        tech.enable_tactic or "", set_tactic, width=22)

        # --- positions --------------------------------------------------------
        pos = self._section(t("technologies.inspector.position"))
        folders = list(self.owner.service.folders())
        for i, fa in enumerate(tech.folders):
            cell = fa.cell(doc.consts)
            ttk.Label(pos, text=fa.name, style="Card.TLabel").grid(
                row=i, column=0, sticky="w", pady=2)
            var_x = tk.StringVar(value=str(cell[0]))
            var_y = tk.StringVar(value=str(cell[1]))

            def commit_pos(f=fa, vx=None, vy=None) -> None:
                if not self._guard():
                    return
                try:
                    x, y = int(vx.get()), int(vy.get())
                except ValueError:
                    return
                f.set_cell(x, y, doc)
                self._touch(canvas=True)

            for col, var in ((1, var_x), (2, var_y)):
                e = ttk.Entry(pos, textvariable=var, width=5)
                e.grid(row=i, column=col, padx=2)
                var.trace_add("write", lambda *_a, f=fa, vx=var_x, vy=var_y:
                              self._debounce(f"pos{id(f)}",
                                             lambda: commit_pos(f, vx, vy)))
            if self._editable:
                ttk.Button(pos, text="✕", width=3,
                           command=lambda f=fa: self._remove_folder(f)).grid(
                    row=i, column=3, padx=(4, 0))
        if self._editable:
            ttk.Button(pos, text="➕ " + t("technologies.add_folder_assignment"),
                       command=lambda: self._add_folder_assignment(folders)).grid(
                row=len(tech.folders), column=0, columnspan=4, sticky="w", pady=(4, 0))

        # --- links ------------------------------------------------------------
        links = self._section(t("technologies.inspector.links"))
        r = 0
        for path in tech.paths:
            coeff = path.research_cost_coeff
            extra = f"  ×{coeff:g}" if coeff is not None and coeff != 1 else ""
            extra += "  (~)" if path.ignore_for_layout else ""
            ttk.Label(links, text="→ " + path.leads_to_tech + extra,
                      style="Card.TLabel").grid(row=r, column=0, columnspan=2,
                                                sticky="w")
            r += 1
        for dep, lvl in tech.dependencies.items():
            ttk.Label(links, text=f"⇢ {dep} = {lvl}", style="Card.TLabel").grid(
                row=r, column=0, columnspan=2, sticky="w")
            r += 1
        for other in tech.xor:
            ttk.Label(links, text="⇄ " + other, style="Card.TLabel").grid(
                row=r, column=0, columnspan=2, sticky="w")
            r += 1
        if r == 0:
            ttk.Label(links, text="—", style="CardMuted.TLabel").grid(
                row=0, column=0, sticky="w")
            r = 1
        if self._editable and tech.xor:
            ttk.Button(links, text=t("technologies.make_xor_reciprocal"),
                       command=self._make_xor_reciprocal).grid(
                row=r, column=0, sticky="w", pady=(4, 0))

        # --- unlocks -----------------------------------------------------------
        unl = self._section(t("technologies.inspector.unlocks"))
        r = 0
        for key in _LIST_FIELDS:
            values = getattr(tech, key)
            ttk.Label(unl, text=t(f"technologies.field.{key}"),
                      style="CardMuted.TLabel").grid(row=r, column=0, sticky="nw",
                                                     pady=2)
            ttk.Label(unl, text=", ".join(values) or "—", style="Card.TLabel",
                      wraplength=210, justify="left").grid(
                row=r, column=1, sticky="w", padx=(8, 0))
            if self._editable:
                ttk.Button(unl, text="…", width=3,
                           command=lambda k=key: self._edit_list(k)).grid(
                    row=r, column=2, sticky="e")
            r += 1
        buildings = tech.enable_buildings
        ttk.Label(unl, text=t("technologies.field.enable_building"),
                  style="CardMuted.TLabel").grid(row=r, column=0, sticky="nw", pady=2)
        ttk.Label(unl, text=("; ".join(f"{b} ≤{l}" for b, l in buildings) or "—"),
                  style="Card.TLabel", wraplength=210, justify="left").grid(
            row=r, column=1, sticky="w", padx=(8, 0))
        if self._editable:
            ttk.Button(unl, text="…", width=3, command=self._edit_buildings).grid(
                row=r, column=2, sticky="e")

        # --- modifiers ----------------------------------------------------------
        mods = self._section(t("technologies.inspector.modifiers"))
        r = 0
        for pair in tech.modifier_pairs():
            ttk.Label(mods, text=pair.key, style="Card.TLabel").grid(
                row=r, column=0, sticky="w", pady=1)
            var = tk.StringVar(value=pair.value.raw)

            def commit_mod(p=pair, v=None) -> None:
                if self._guard() and v.get().strip():
                    from ...core.pdx import Scalar
                    p.value = Scalar(v.get().strip())
                    self._touch()

            e = ttk.Entry(mods, textvariable=var, width=9)
            e.grid(row=r, column=1, sticky="w", padx=(8, 0))
            var.trace_add("write", lambda *_a, p=pair, v=var:
                          self._debounce(f"mod{id(p)}", lambda: commit_mod(p, v)))
            if self._editable:
                ttk.Button(mods, text="✕", width=3,
                           command=lambda p=pair: self._remove_pair(p)).grid(
                    row=r, column=2, padx=(4, 0))
            r += 1
        for pair in tech.stat_blocks():
            ttk.Label(mods, text=pair.key + " { … }", style="Card.TLabel").grid(
                row=r, column=0, columnspan=2, sticky="w", pady=1)
            if self._editable:
                ttk.Button(mods, text="✎", width=3,
                           command=lambda k=pair.key: self._edit_script(
                               k, kinds=("modifier",))).grid(row=r, column=2,
                                                             padx=(4, 0))
            r += 1
        if self._editable:
            ttk.Button(mods, text="➕ " + t("technologies.add_modifier"),
                       command=self._add_modifier).grid(
                row=r, column=0, sticky="w", pady=(4, 0))
            ttk.Button(mods, text="➕ " + t("technologies.add_stat_block"),
                       command=self._add_stat_block).grid(
                row=r, column=1, sticky="w", pady=(4, 0))

        # --- scripts -------------------------------------------------------------
        scr = self._section(t("technologies.inspector.scripts"))
        for i, key in enumerate(TECH_SCRIPT_FIELDS):
            filled = "●" if tech.get_script(key).strip() else "○"
            ttk.Button(scr, text=f"{filled} {key}",
                       command=lambda k=key: self._edit_script(
                           k, kinds=_SCRIPT_KINDS.get(k, ("effect", "trigger")))
                       ).grid(row=i // 2, column=i % 2, sticky="ew", padx=2, pady=2)
        scr.columnconfigure((0, 1), weight=1)

        # --- xp / doctrine ---------------------------------------------------------
        xp = self._section(t("technologies.inspector.xp"))
        ttk.Label(xp, text=t("technologies.field.xp_research_type"),
                  style="CardMuted.TLabel").grid(row=0, column=0, sticky="w", pady=2)
        xp_var = tk.StringVar(value=tech.xp_research_type or "")
        combo = ttk.Combobox(xp, textvariable=xp_var, width=10, state="readonly",
                             values=("", "army", "navy", "air"))
        combo.grid(row=0, column=1, sticky="w", padx=(8, 0))
        combo.bind("<<ComboboxSelected>>", lambda e: self._guarded(
            lambda raw: (setattr(tech, "xp_research_type", raw or None),
                         self._touch()), xp_var))

        def num_setter(attr):
            def commit(raw: str) -> None:
                if not raw:
                    setattr(tech, attr, None)
                else:
                    try:
                        setattr(tech, attr, float(raw))
                    except ValueError:
                        return
                self._touch()
            return commit

        self._row_entry(xp, 1, "xp_boost_cost",
                        "" if tech.xp_boost_cost is None else f"{tech.xp_boost_cost:g}",
                        num_setter("xp_boost_cost"))
        self._row_entry(xp, 2, "xp_research_bonus",
                        "" if tech.xp_research_bonus is None
                        else f"{tech.xp_research_bonus:g}",
                        num_setter("xp_research_bonus"))
        self._row_entry(xp, 3, "xp_unlock_cost",
                        "" if tech.xp_unlock_cost is None
                        else f"{tech.xp_unlock_cost:g}",
                        num_setter("xp_unlock_cost"))
        doct_var = tk.BooleanVar(value=tech.doctrine)
        chk = ttk.Checkbutton(xp, text="doctrine", style="Card.TCheckbutton",
                              variable=doct_var,
                              command=lambda: self._guard() and (
                                  setattr(tech, "doctrine", doct_var.get()),
                                  self._touch(canvas=True)))
        chk.grid(row=4, column=0, columnspan=2, sticky="w")

        def set_dname(raw: str) -> None:
            tech.doctrine_name = raw or None
            self._touch()

        self._row_entry(xp, 5, "doctrine_name", tech.doctrine_name or "",
                        set_dname, width=20)

        # --- localisation ------------------------------------------------------------
        loc = self._section(t("technologies.inspector.loc"))
        langs = self.owner.service.languages()
        self._loc_lang = tk.StringVar(
            value=self.owner.loc_language if self.owner.loc_language in langs
            else langs[0])
        combo = ttk.Combobox(loc, textvariable=self._loc_lang, values=langs,
                             state="readonly", width=12)
        combo.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        combo.bind("<<ComboboxSelected>>", lambda e: self._reload_loc())

        self._loc_name = tk.StringVar()
        self._loc_desc = tk.StringVar()
        ttk.Label(loc, text=t("technologies.field.loc_name"),
                  style="CardMuted.TLabel").grid(row=1, column=0, sticky="w", pady=2)
        name_e = ttk.Entry(loc, textvariable=self._loc_name, width=24)
        name_e.grid(row=1, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(loc, text=t("technologies.field.loc_desc"),
                  style="CardMuted.TLabel").grid(row=2, column=0, sticky="w", pady=2)
        desc_e = ttk.Entry(loc, textvariable=self._loc_desc, width=24)
        desc_e.grid(row=2, column=1, sticky="ew", padx=(8, 0))
        for var, which in ((self._loc_name, "name"), (self._loc_desc, "desc")):
            var.trace_add("write", lambda *_a, w=which:
                          self._debounce(f"loc_{w}", lambda: self._commit_loc(w)))
        self._reload_loc()

        icon_sprite = f"GFX_{tech.id}_medium"
        self._loc_photo = self._icon_preview(icon_sprite, (96, 52))
        ttk.Label(loc, image=self._loc_photo).grid(row=3, column=0, pady=(6, 0))
        if self._editable:
            ttk.Button(loc, text=t("technologies.import_icon"),
                       command=self._import_icon).grid(row=3, column=1, sticky="w",
                                                       padx=(8, 0), pady=(6, 0))

    # -------------------------------------------------------------- section ops
    def _rename(self) -> None:
        taken = set(self.owner.service.tech_index())
        TextPromptDialog(self, self.owner, self.t("technologies.rename"),
                         self.t("technologies.tech_id"),
                         lambda tid: self.owner.rename_tech(self.tech, tid),
                         initial=self.tech.id, taken=taken)

    def _pick_categories(self) -> None:
        cats = self.owner.service.categories()

        def apply(values: list[str]) -> None:
            self.tech.categories = values
            self._touch()
            self.refresh()

        MultiPickDialog(self, self.owner, self.t("technologies.field.categories"),
                        cats, apply, preselected=set(self.tech.categories))

    def _remove_folder(self, fa) -> None:
        self.tech.remove_folder(fa.name)
        self._touch(canvas=True)
        self.refresh()

    def _add_folder_assignment(self, folders: list[str]) -> None:
        current = {f.name for f in self.tech.folders}
        options = [(f, f) for f in folders if f not in current]

        def apply(folder_id: str) -> None:
            self.tech.add_folder(folder_id, 0, 0)
            self._touch(canvas=True)
            self.refresh()

        SinglePickDialog(self, self.owner,
                         self.t("technologies.add_folder_assignment"),
                         options, apply)

    def _make_xor_reciprocal(self) -> None:
        for other in self.tech.xor:
            found = self.owner._find(other)
            if found is None or found[0].is_vanilla:
                continue
            _ref, odoc, twin = found
            if self.tech.id not in twin.xor:
                twin.xor = twin.xor + [self.tech.id]
                self.owner.mark_dirty([odoc])
        self.owner.refresh_canvas()
        self.owner._validate()

    def _edit_list(self, key: str) -> None:
        current = ", ".join(getattr(self.tech, key))

        def apply(raw: str) -> None:
            values = [v.strip() for v in raw.replace(",", " ").split() if v.strip()]
            if key == "sub_technologies" and len(values) > MAX_SUBTECHS:
                messagebox.showerror("ANKA", self.t("technologies.err.max_subtechs",
                                                    max=MAX_SUBTECHS))
                return
            setattr(self.tech, key, values)
            self._touch(canvas=(key == "sub_technologies"))
            self.refresh()

        TextPromptDialog(self, self.owner, self.t(f"technologies.field.{key}"),
                         self.t("technologies.list_prompt"), apply,
                         initial=current, pattern=r"^[\w, ]*$")

    def _edit_buildings(self) -> None:
        current = "; ".join(f"{b}={l}" for b, l in self.tech.enable_buildings)

        def apply(raw: str) -> None:
            values: list[tuple[str, int]] = []
            for part in raw.split(";"):
                part = part.strip()
                if not part:
                    continue
                if "=" in part:
                    name, _s, lvl = part.partition("=")
                    try:
                        values.append((name.strip(), int(lvl.strip())))
                    except ValueError:
                        values.append((name.strip(), 1))
                else:
                    values.append((part, 1))
            self.tech.enable_buildings = values
            self._touch()
            self.refresh()

        TextPromptDialog(self, self.owner,
                         self.t("technologies.field.enable_building"),
                         self.t("technologies.buildings_prompt"), apply,
                         initial=current, pattern=r"^[\w=; ]*$")

    def _remove_pair(self, pair) -> None:
        self.tech.block.items = [it for it in self.tech.block.items if it is not pair]
        self._touch()
        self.refresh()

    def _add_modifier(self) -> None:
        from ..effects import ScriptCatalog
        options = []
        for name, mod in sorted(ScriptCatalog.modifiers().items()):
            scopes = ", ".join(mod.scopes)
            options.append((f"{name} · {scopes}" if scopes else name, name))

        def apply(name: str) -> None:
            self.tech.block.set(name, 0.1)
            self._touch()
            self.refresh()

        SinglePickDialog(self, self.owner, self.t("technologies.add_modifier"),
                         options, apply)

    def _add_stat_block(self) -> None:
        def apply(key: str) -> None:
            from ...core.pdx import Block
            self.tech.block.set(key, Block())
            self._touch()
            self._edit_script(key, kinds=("modifier",))

        TextPromptDialog(self, self.owner, self.t("technologies.add_stat_block"),
                         self.t("technologies.stat_block_prompt"), apply,
                         initial="category_")

    def _edit_script(self, key: str, kinds: tuple[str, ...]) -> None:
        def apply(text: str) -> None:
            try:
                self.tech.set_script(key, text)
            except Exception as exc:
                messagebox.showerror("ANKA", str(exc))
                return
            self._touch()
            self.refresh()

        ScriptEditorDialog(self, self.owner, key, self.tech.get_script(key),
                           apply, kinds=kinds, focus_id=self.tech.id)

    # ------------------------------------------------------------------- loc
    def _reload_loc(self) -> None:
        if self.tech is None:
            return
        lang = self._loc_lang.get()
        self._loading = True
        try:
            name = self.owner.service.loc_get(self.tech.id, lang)
            desc = self.owner.service.loc_get(f"{self.tech.id}_desc", lang)
            self._loc_name.set(name or "")
            self._loc_desc.set(desc or "")
        finally:
            self._loading = False

    def _commit_loc(self, which: str) -> None:
        if self.tech is None or self._loading:
            return
        lang = self._loc_lang.get()
        value = (self._loc_name if which == "name" else self._loc_desc).get()
        self.owner.service.set_tech_loc(
            self.tech.id, lang,
            name=value if which == "name" else None,
            desc=value if which == "desc" else None)
        if which == "name":
            self.owner.refresh_canvas()

    def _import_icon(self) -> None:
        if self.tech is None:
            return
        path = filedialog.askopenfilename(filetypes=[
            (self.t("common.images"), " ".join(f"*{e}" for e in SUPPORTED_IMPORT_FORMATS))])
        if not path:
            return
        sprite = self.owner.import_icon(path, self.tech)
        if sprite:
            self.refresh()
