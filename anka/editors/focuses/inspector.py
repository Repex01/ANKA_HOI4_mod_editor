"""Right-hand inspector panel: every property of the selected focus.

The inspector is a dumb form over a `Focus`: it loads values on `show()`, and each
committed edit writes straight back through the block-backed model, marks the editor
dirty and asks it to refresh the canvas. Free-form script blocks and pickers open
their dedicated dialogs.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from ...config.constants import HOI4_LANGUAGES
from ...services.focus_service import (
    DAYS_PER_COST,
    FOCUS_FLAG_DEFAULTS,
    FOCUS_SCRIPT_FIELDS,
    Focus,
)
from ...ui.widgets import ScrollableFrame
from ...ui.widgets.tooltip import attach_help
from .dialogs import (
    FocusPreviewDialog,
    IconPickerDialog,
    MultiPickDialog,
    TextPromptDialog,
)
from .script_editor import ScriptEditorDialog

# Which block kinds each script field accepts. Condition-only fields must never
# offer effects — they are triggers by definition.
_SCRIPT_KINDS = {
    "available": ("trigger",), "bypass": ("trigger",), "cancel": ("trigger",),
    "allow_branch": ("trigger",), "ai_will_do": ("trigger",),
    "select_effect": ("effect", "trigger"), "completion_reward": ("effect", "trigger"),
    "complete_tooltip": ("effect", "trigger"),
}


class FocusInspector(ttk.Frame):
    def __init__(self, master, owner):
        super().__init__(master, style="Card.TFrame")
        self.owner = owner                     # FocusesEditor
        self.t = owner.t
        self.palette = owner.palette
        self.focus_obj: Focus | None = None
        self._loading = False
        self._icon_photo: ImageTk.PhotoImage | None = None
        self._loaded_name = ""
        self._loaded_desc = ""
        self._commit_jobs: dict[str, tuple[str, object]] = {}   # key -> (after id, commit fn)
        self._build()
        self._wire_instant_commits()
        self.show(None)

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        self._scroll = ScrollableFrame(self, bg=self.palette.surface)
        self._scroll.pack(fill="both", expand=True)
        body = self._scroll.body
        body.configure(style="Card.TFrame", padding=(14, 10))
        body.columnconfigure(1, weight=1)
        r = 0

        self._title = ttk.Label(body, text="", style="Heading.TLabel")
        self._title.grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 2)); r += 1
        self._subtitle = ttk.Label(body, text="", style="CardMuted.TLabel", wraplength=280)
        self._subtitle.grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 8)); r += 1

        self._empty = ttk.Label(body, text=self.t("focuses.inspector.empty"),
                                style="CardMuted.TLabel", wraplength=280)
        self._empty.grid(row=r, column=0, columnspan=2, sticky="w"); r += 1

        self._form = ttk.Frame(body, style="Card.TFrame")
        self._form.grid(row=r, column=0, columnspan=2, sticky="nsew")
        self._form.columnconfigure(1, weight=1)
        f = self._form
        fr = 0

        # id + rename ------------------------------------------------------
        ttk.Label(f, text="ID", style="CardMuted.TLabel").grid(
            row=fr, column=0, sticky="w", pady=3)
        id_row = ttk.Frame(f, style="Card.TFrame")
        id_row.grid(row=fr, column=1, sticky="ew", padx=(8, 0)); fr += 1
        id_row.columnconfigure(0, weight=1)
        self._id_label = ttk.Entry(id_row, state="readonly")
        self._id_label.grid(row=0, column=0, sticky="ew")
        self._rename_btn = ttk.Button(id_row, text="✎", width=3, command=self._rename)
        self._rename_btn.grid(row=0, column=1, padx=(4, 0))

        # localisation -------------------------------------------------------
        ttk.Label(f, text=self.t("focuses.inspector.language"),
                  style="CardMuted.TLabel").grid(row=fr, column=0, sticky="w", pady=3)
        self._lang = ttk.Combobox(f, state="readonly", values=list(HOI4_LANGUAGES), width=14)
        self._lang.grid(row=fr, column=1, sticky="w", padx=(8, 0), pady=3); fr += 1
        self._lang.set(self.owner.loc_language)
        self._lang.bind("<<ComboboxSelected>>", self._on_language)

        ttk.Label(f, text=self.t("focuses.inspector.name"),
                  style="CardMuted.TLabel").grid(row=fr, column=0, sticky="nw", pady=3)
        # A Text (not Entry) so it grows to fit a long name; Enter commits instead
        # of inserting a newline (a focus name is one loc line).
        # NB: not `self._name` — that is tkinter's internal widget name.
        self._name_text = tk.Text(f, height=1, wrap="word", bg=self.palette.surface_alt,
                             fg=self.palette.text, insertbackground=self.palette.text,
                             relief="flat", font=("Segoe UI", 10))
        self._name_text.grid(row=fr, column=1, sticky="ew", padx=(8, 0), pady=3); fr += 1
        self._name_text.bind("<FocusOut>", lambda e: self._commit_loc())
        self._name_text.bind("<Return>", lambda e: (self._commit_loc(), "break")[1])
        self._name_text.bind("<KeyRelease>", lambda e: (
            self._auto_grow(self._name_text, 1),
            self._debounce("loc", self._commit_loc, 1200)))

        ttk.Label(f, text=self.t("focuses.inspector.desc"),
                  style="CardMuted.TLabel").grid(row=fr, column=0, sticky="nw", pady=3)
        self._desc = tk.Text(f, height=3, wrap="word", bg=self.palette.surface_alt,
                             fg=self.palette.text, insertbackground=self.palette.text,
                             relief="flat", font=("Segoe UI", 10))
        self._desc.grid(row=fr, column=1, sticky="ew", padx=(8, 0), pady=3); fr += 1
        self._desc.bind("<FocusOut>", lambda e: self._commit_loc())

        # icon ----------------------------------------------------------------
        ttk.Label(f, text=self.t("focuses.inspector.icon"),
                  style="CardMuted.TLabel").grid(row=fr, column=0, sticky="w", pady=3)
        icon_row = ttk.Frame(f, style="Card.TFrame")
        icon_row.grid(row=fr, column=1, sticky="ew", padx=(8, 0), pady=3); fr += 1
        self._icon_btn = tk.Button(icon_row, bd=0, bg=self.palette.surface_alt,
                                   activebackground=self.palette.surface,
                                   cursor="hand2", command=self._pick_icon)
        self._icon_btn.pack(side="left")
        self._icon_name = ttk.Label(icon_row, text="", style="CardMuted.TLabel",
                                    wraplength=170)
        self._icon_name.pack(side="left", padx=(8, 0))

        # position -------------------------------------------------------------
        ttk.Label(f, text=self.t("focuses.inspector.position"),
                  style="CardMuted.TLabel").grid(row=fr, column=0, sticky="w", pady=3)
        pos_row = ttk.Frame(f, style="Card.TFrame")
        pos_row.grid(row=fr, column=1, sticky="w", padx=(8, 0), pady=3); fr += 1
        self._x = tk.StringVar()
        self._y = tk.StringVar()
        for label, var in (("x", self._x), ("y", self._y)):
            ttk.Label(pos_row, text=label, style="CardMuted.TLabel").pack(side="left")
            sp = ttk.Spinbox(pos_row, textvariable=var, width=5, from_=-999, to=999,
                             command=self._commit_position)
            sp.pack(side="left", padx=(2, 10))
            sp.bind("<FocusOut>", lambda e: self._commit_position())
            sp.bind("<Return>", lambda e: self._commit_position())

        ttk.Label(f, text=self.t("focuses.inspector.relative"),
                  style="CardMuted.TLabel").grid(row=fr, column=0, sticky="w", pady=3)
        self._relative = ttk.Combobox(f, state="readonly")
        self._relative.grid(row=fr, column=1, sticky="ew", padx=(8, 0), pady=3); fr += 1
        self._relative.bind("<<ComboboxSelected>>", lambda e: self._commit_relative())

        # cost -------------------------------------------------------------------
        ttk.Label(f, text=self.t("focuses.inspector.cost"),
                  style="CardMuted.TLabel").grid(row=fr, column=0, sticky="w", pady=3)
        cost_row = ttk.Frame(f, style="Card.TFrame")
        cost_row.grid(row=fr, column=1, sticky="w", padx=(8, 0), pady=3); fr += 1
        self._cost = tk.StringVar()
        sp = ttk.Spinbox(cost_row, textvariable=self._cost, width=7, from_=0, to=999,
                         increment=1, command=self._commit_cost)
        sp.pack(side="left")
        sp.bind("<FocusOut>", lambda e: self._commit_cost())
        sp.bind("<Return>", lambda e: self._commit_cost())
        self._days = ttk.Label(cost_row, text="", style="CardMuted.TLabel")
        self._days.pack(side="left", padx=(8, 0))

        # flags -------------------------------------------------------------------
        self._flags: dict[str, tk.BooleanVar] = {}
        flag_box = ttk.Frame(f, style="Card.TFrame")
        flag_box.grid(row=fr, column=0, columnspan=2, sticky="ew", pady=(6, 3)); fr += 1
        for name in FOCUS_FLAG_DEFAULTS:
            var = tk.BooleanVar()
            self._flags[name] = var
            ttk.Checkbutton(flag_box, text=name, style="Card.TCheckbutton", variable=var,
                            command=lambda n=name: self._commit_flag(n)).pack(anchor="w")

        # will_lead_to_war_with ------------------------------------------------------
        ttk.Label(f, text="will_lead_to_war_with", style="CardMuted.TLabel").grid(
            row=fr, column=0, sticky="w", pady=3)
        self._war = tk.StringVar()
        war_entry = ttk.Entry(f, textvariable=self._war, width=10)
        war_entry.grid(row=fr, column=1, sticky="w", padx=(8, 0), pady=3); fr += 1
        war_entry.bind("<FocusOut>", lambda e: self._commit_war())

        # search filters -----------------------------------------------------------
        ttk.Label(f, text="search_filters", style="CardMuted.TLabel").grid(
            row=fr, column=0, sticky="nw", pady=3)
        filt_row = ttk.Frame(f, style="Card.TFrame")
        filt_row.grid(row=fr, column=1, sticky="ew", padx=(8, 0), pady=3); fr += 1
        filt_row.columnconfigure(0, weight=1)
        self._filters_label = ttk.Label(filt_row, text="", style="CardMuted.TLabel",
                                        wraplength=210)
        self._filters_label.grid(row=0, column=0, sticky="w")
        ttk.Button(filt_row, text="✎", width=3, command=self._pick_filters).grid(
            row=0, column=1, sticky="e")

        # prerequisites ----------------------------------------------------------------
        ttk.Separator(f).grid(row=fr, column=0, columnspan=2, sticky="ew", pady=8); fr += 1
        head = ttk.Frame(f, style="Card.TFrame")
        head.grid(row=fr, column=0, columnspan=2, sticky="ew"); fr += 1
        ttk.Label(head, text=self.t("focuses.inspector.prerequisites"),
                  style="Card.TLabel").pack(side="left")
        ttk.Button(head, text="➕", width=3, command=self._add_prereq_group).pack(side="right")
        self._prereq_box = ttk.Frame(f, style="Card.TFrame")
        self._prereq_box.grid(row=fr, column=0, columnspan=2, sticky="ew"); fr += 1
        self._prereq_box.columnconfigure(0, weight=1)

        # mutually exclusive --------------------------------------------------------------
        mut_head = ttk.Frame(f, style="Card.TFrame")
        mut_head.grid(row=fr, column=0, columnspan=2, sticky="ew", pady=(8, 0)); fr += 1
        ttk.Label(mut_head, text=self.t("focuses.inspector.mutex"),
                  style="Card.TLabel").pack(side="left")
        ttk.Button(mut_head, text="✎", width=3, command=self._pick_mutex).pack(side="right")
        self._mutex_label = ttk.Label(f, text="", style="CardMuted.TLabel", wraplength=280)
        self._mutex_label.grid(row=fr, column=0, columnspan=2, sticky="w"); fr += 1

        # scripts ---------------------------------------------------------------------------
        ttk.Separator(f).grid(row=fr, column=0, columnspan=2, sticky="ew", pady=8); fr += 1
        scripts_head = ttk.Frame(f, style="Card.TFrame")
        scripts_head.grid(row=fr, column=0, columnspan=2, sticky="ew", pady=(0, 4)); fr += 1
        ttk.Label(scripts_head, text=self.t("focuses.inspector.scripts"),
                  style="Card.TLabel").pack(side="left")
        ttk.Button(scripts_head, text="👁 " + self.t("focuses.preview"),
                   command=self._preview).pack(side="right")
        self._script_status: dict[str, ttk.Label] = {}
        for field_name in FOCUS_SCRIPT_FIELDS:
            row = ttk.Frame(f, style="Card.TFrame")
            row.grid(row=fr, column=0, columnspan=2, sticky="ew", pady=1); fr += 1
            status = ttk.Label(row, text="○", style="CardMuted.TLabel", width=2)
            status.pack(side="left")
            label = ttk.Label(row, text=field_name, style="CardMuted.TLabel")
            label.pack(side="left")
            attach_help(label, self.t, f"script.{field_name}", self.palette)
            self._script_status[field_name] = status
            # the edit button hugs the label instead of the far-right edge
            ttk.Button(row, text="✎", width=3,
                       command=lambda n=field_name: self._edit_script(n)).pack(
                side="left", padx=(6, 0))

        # delete --------------------------------------------------------------------------------
        ttk.Separator(f).grid(row=fr, column=0, columnspan=2, sticky="ew", pady=8); fr += 1
        self._delete_btn = ttk.Button(f, text="🗑  " + self.t("focuses.inspector.delete"),
                                      command=lambda: self.owner.delete_focus(self.focus_obj))
        self._delete_btn.grid(row=fr, column=0, columnspan=2, sticky="ew")

    def _wire_instant_commits(self) -> None:
        """Commit edits as the user types (debounced) — no Enter/FocusOut required.
        FocusOut/Return bindings stay as an immediate flush."""
        wiring = (
            ("pos", self._x, self._commit_position, 700),
            ("pos", self._y, self._commit_position, 700),
            ("cost", self._cost, self._commit_cost, 700),
            ("war", self._war, self._commit_war, 700),
        )
        for job_key, var, commit, delay in wiring:
            var.trace_add("write",
                          lambda *_, k=job_key, c=commit, d=delay: self._debounce(k, c, d))
        self._desc.bind("<KeyRelease>", lambda e: (
            self._auto_grow(self._desc, 3),
            self._debounce("loc", self._commit_loc, 1200)))

    def _debounce(self, key: str, commit, delay: int) -> None:
        if self._loading or self.focus_obj is None:
            return
        pending = self._commit_jobs.pop(key, None)
        if pending is not None:
            self.after_cancel(pending[0])
        job = self.after(delay, lambda: (self._commit_jobs.pop(key, None), commit()))
        self._commit_jobs[key] = (job, commit)

    def flush_pending(self) -> None:
        """Run pending debounced commits *now* — called before the inspector is
        repointed at another focus (or the editor is left), so an edit made moments
        before clicking elsewhere is saved, not dropped."""
        for job, commit in list(self._commit_jobs.values()):
            self.after_cancel(job)
        pending = [commit for _job, commit in self._commit_jobs.values()]
        self._commit_jobs.clear()
        for commit in pending:
            commit()

    # ------------------------------------------------------------------- show
    def show(self, focus: Focus | None, editable: bool = True) -> None:
        # Pending debounced commits belong to the previous focus: the canvas press
        # handler runs before the entry's FocusOut, so flush them here while the old
        # focus and field values are still intact — otherwise the edit is lost.
        self.flush_pending()
        self.focus_obj = focus
        self._editable = editable
        self._loading = True
        try:
            if focus is None:
                self._form.grid_remove()
                self._empty.grid()
                self._title.configure(text=self.t("focuses.inspector.title"))
                self._subtitle.configure(text="")
                return
            self._empty.grid_remove()
            self._form.grid()
            kind_key = {"focus": "focuses.kind.focus",
                        "shared_focus": "focuses.kind.shared",
                        "joint_focus": "focuses.kind.joint"}[focus.kind]
            self._title.configure(text=focus.id or "—")
            self._subtitle.configure(
                text=self.t(kind_key) + ("" if editable
                                         else "  ·  " + self.t("focuses.readonly")))
            self._set_entry(self._id_label, focus.id)
            lang = self.owner.loc_language
            self._lang.set(lang)
            self._loaded_name = self.owner.service.focus_name(focus.text or focus.id, lang)
            self._loaded_desc = self.owner.service.focus_desc(focus.text or focus.id, lang)
            name = (self._loaded_name
                    if self._loaded_name != (focus.text or focus.id) else "")
            self._set_text(self._name_text, name)
            self._desc.configure(state="normal")   # a disabled Text ignores edits
            self._desc.delete("1.0", "end")
            self._desc.insert("1.0", self._loaded_desc.replace("\\n", "\n"))
            self._auto_grow(self._name_text, 1)
            self._auto_grow(self._desc, 3)
            self._x.set(str(focus.x))
            self._y.set(str(focus.y))
            self._cost.set(f"{focus.cost:g}")
            self._update_days()
            self._war.set(focus.will_lead_to_war_with or "")
            for name, var in self._flags.items():
                var.set(focus.get_flag(name))
            ids = sorted(fid for fid in self.owner.known_ids() if fid != focus.id)
            self._relative.configure(values=[""] + ids)
            self._relative.set(focus.relative_position_id or "")
            self._refresh_icon()
            self._refresh_filters()
            self._refresh_prereqs()
            self._refresh_mutex()
            self._refresh_scripts()
            state = "normal" if editable else "disabled"
            for widget in self._iter_inputs(self._form):
                try:
                    widget.configure(state=("readonly" if isinstance(widget, ttk.Combobox)
                                            and editable else state))
                except tk.TclError:
                    pass
            self._lang.configure(state="readonly")
            self._id_label.configure(state="readonly")   # the mass pass re-enabled it
        finally:
            self._loading = False

    def _iter_inputs(self, parent):
        for child in parent.winfo_children():
            if isinstance(child, (ttk.Entry, ttk.Spinbox, ttk.Combobox, ttk.Button,
                                  ttk.Checkbutton, tk.Text, tk.Button)):
                yield child
            yield from self._iter_inputs(child)

    @staticmethod
    def _set_entry(entry: ttk.Entry, value: str) -> None:
        entry.configure(state="normal")
        entry.delete(0, "end")
        entry.insert(0, value)
        entry.configure(state="readonly")

    @staticmethod
    def _set_text(text: tk.Text, value: str) -> None:
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("1.0", value)

    @staticmethod
    def _auto_grow(text: tk.Text, min_lines: int, max_lines: int = 12) -> None:
        """Resize a Text widget's height to fit its (wrapped) content, clamped
        between `min_lines` and `max_lines`."""
        try:
            text.update_idletasks()
            # count returns the number of display-line boundaries between the two
            # indices — one less than the line count — so add 1.
            shown = text.count("1.0", "end - 1c", "displaylines")
            lines = (shown[0] if shown else 0) + 1
        except tk.TclError:
            lines = int(text.index("end-1c").split(".")[0])
        lines = max(min_lines, min(max(lines, 1), max_lines))
        try:
            if int(text.cget("height")) != lines:
                text.configure(height=lines)
        except tk.TclError:
            pass

    # ----------------------------------------------------------------- refresh
    def _refresh_icon(self) -> None:
        focus = self.focus_obj
        sprite = focus.icon if focus else ""
        img = None
        path = (self.owner.resolver.resolve(sprite)
                if sprite and self.owner.resolver_ready() else None)
        if path is not None:
            try:
                with Image.open(path) as im:
                    img = im.convert("RGBA")
                img.thumbnail((64, 56), Image.LANCZOS)
            except Exception:
                img = None
        if img is None:
            img = Image.new("RGBA", (64, 56), (0, 0, 0, 0))
        self._icon_photo = ImageTk.PhotoImage(img)
        self._icon_btn.configure(image=self._icon_photo)
        extra = "  (+" + self.t("focuses.inspector.cond_icons") + ")" \
            if focus is not None and focus.has_conditional_icons else ""
        self._icon_name.configure(text=(sprite or "—") + extra)

    def _refresh_filters(self) -> None:
        values = self.focus_obj.search_filters if self.focus_obj else []
        text = ", ".join(v.replace("FOCUS_FILTER_", "") for v in values) or "—"
        self._filters_label.configure(text=text)

    def _refresh_prereqs(self) -> None:
        for w in self._prereq_box.winfo_children():
            w.destroy()
        focus = self.focus_obj
        if focus is None:
            return
        groups = focus.prerequisites
        if not groups:
            ttk.Label(self._prereq_box, text="—", style="CardMuted.TLabel").grid(
                row=0, column=0, sticky="w")
        for i, group in enumerate(groups):
            row = ttk.Frame(self._prereq_box, style="Card.TFrame")
            row.grid(row=i, column=0, sticky="ew", pady=1)
            row.columnconfigure(0, weight=1)
            join = f"  {self.t('focuses.inspector.or')}  "
            ttk.Label(row, text=join.join(group), style="CardMuted.TLabel",
                      wraplength=230).grid(row=0, column=0, sticky="w")
            if self._editable:
                ttk.Button(row, text="✎", width=3,
                           command=lambda idx=i: self._edit_prereq_group(idx)).grid(
                    row=0, column=1, padx=2)
                ttk.Button(row, text="✕", width=3,
                           command=lambda idx=i: self._remove_prereq_group(idx)).grid(
                    row=0, column=2)

    def _refresh_mutex(self) -> None:
        values = self.focus_obj.mutually_exclusive if self.focus_obj else []
        self._mutex_label.configure(text=", ".join(values) or "—")

    def _refresh_scripts(self) -> None:
        focus = self.focus_obj
        for name, label in self._script_status.items():
            filled = bool(focus and focus.get_script(name).strip())
            label.configure(text="●" if filled else "○",
                            foreground=self.palette.accent if filled
                            else self.palette.text_muted)

    def _update_days(self) -> None:
        try:
            days = int(float(self._cost.get()) * DAYS_PER_COST)
            self._days.configure(text=self.t("focuses.inspector.days", days=days))
        except ValueError:
            self._days.configure(text="")

    # ------------------------------------------------------------------ commits
    def _guard(self) -> bool:
        return not self._loading and self.focus_obj is not None and self._editable

    def _commit_position(self) -> None:
        if not self._guard():
            return
        try:
            x, y = int(self._x.get()), int(self._y.get())
        except ValueError:
            return
        if (x, y) != (self.focus_obj.x, self.focus_obj.y):
            self.focus_obj.x = x
            self.focus_obj.y = y
            self.owner.mark_dirty()
            self.owner.refresh_canvas()

    def _commit_relative(self) -> None:
        if not self._guard():
            return
        value = self._relative.get() or None
        if value != self.focus_obj.relative_position_id:
            positions = self.owner.positions()
            old_abs = positions.get(self.focus_obj.id)
            self.focus_obj.relative_position_id = value
            if old_abs is not None:
                base = positions.get(value, (0, 0)) if value else (0, 0)
                self.focus_obj.x = old_abs[0] - base[0]
                self.focus_obj.y = old_abs[1] - base[1]
                self._x.set(str(self.focus_obj.x))
                self._y.set(str(self.focus_obj.y))
            self.owner.mark_dirty()
            self.owner.refresh_canvas()

    def _commit_cost(self) -> None:
        if not self._guard():
            return
        try:
            cost = float(self._cost.get())
        except ValueError:
            return
        if cost != self.focus_obj.cost:
            self.focus_obj.cost = cost
            self.owner.mark_dirty()
            self.owner.refresh_canvas()
        self._update_days()

    def _commit_flag(self, name: str) -> None:
        if not self._guard():
            return
        self.focus_obj.set_flag(name, self._flags[name].get())
        self.owner.mark_dirty()

    def _commit_war(self) -> None:
        if not self._guard():
            return
        self.focus_obj.will_lead_to_war_with = self._war.get().strip() or None
        self.owner.mark_dirty()

    def _commit_loc(self) -> None:
        if not self._guard():
            return
        focus = self.focus_obj
        lang = self._lang.get()
        name = self._name_text.get("1.0", "end").strip()
        desc = self._desc.get("1.0", "end").strip()
        key = focus.text or focus.id
        current_name = self.owner.service.focus_name(key, lang)
        # loc stores line breaks as literal \n; the Text widget holds real ones
        current_desc = self.owner.service.focus_desc(key, lang).replace("\\n", "\n")
        new_name = name if name else None
        if (new_name and new_name != current_name) or (desc != current_desc and (desc or current_desc)):
            self.owner.service.set_focus_loc(
                key, lang,
                new_name if new_name and new_name != current_name else None,
                desc if desc != current_desc else None)
            self.owner.refresh_canvas()

    def _on_language(self, _event=None) -> None:
        self.owner.loc_language = self._lang.get()
        if self.focus_obj is not None:
            self.show(self.focus_obj, self._editable)
        self.owner.refresh_canvas()

    # ------------------------------------------------------------------- pickers
    def _rename(self) -> None:
        if not self._guard():
            return
        focus = self.focus_obj
        TextPromptDialog(self, self.owner, self.t("focuses.rename"),
                         self.t("focuses.rename_label"),
                         lambda new_id: self.owner.rename_focus(focus, new_id),
                         initial=focus.id,
                         taken=set(self.owner.known_ids()) - {focus.id})

    def _pick_icon(self) -> None:
        if not self._guard():
            return
        focus = self.focus_obj

        def picked(sprite: str) -> None:
            focus.icon = sprite
            self.owner.mark_dirty()
            self._refresh_icon()
            self.owner.refresh_canvas()

        def imported(path, keep_size: bool = False) -> None:
            sprite = self.owner.import_icon(path, focus, keep_size=keep_size)
            if sprite:
                picked(sprite)

        IconPickerDialog(self, self.owner, self.owner.resolver, focus.icon,
                         picked, imported)

    def _pick_filters(self) -> None:
        if not self._guard():
            return
        focus = self.focus_obj

        def picked(values: list[str]) -> None:
            focus.search_filters = values
            self.owner.mark_dirty()
            self._refresh_filters()

        MultiPickDialog(self, self.owner, "search_filters",
                        self.owner.service.search_filter_values(), picked,
                        preselected=set(focus.search_filters))

    def _add_prereq_group(self) -> None:
        if not self._guard():
            return
        self._edit_prereq_group(None)

    def _edit_prereq_group(self, index: int | None) -> None:
        focus = self.focus_obj
        groups = focus.prerequisites
        preselected = set(groups[index]) if index is not None else set()

        def picked(values: list[str]) -> None:
            new_groups = list(groups)
            if index is None:
                if values:
                    new_groups.append(values)
            elif values:
                new_groups[index] = values
            else:
                del new_groups[index]
            focus.prerequisites = new_groups
            self.owner.mark_dirty()
            self._refresh_prereqs()
            self.owner.refresh_canvas()

        options = self.owner.known_id_options(exclude=focus.id)
        MultiPickDialog(self, self.owner, self.t("focuses.inspector.prerequisites"),
                        options, picked, preselected=preselected)

    def _remove_prereq_group(self, index: int) -> None:
        focus = self.focus_obj
        groups = focus.prerequisites
        del groups[index]
        focus.prerequisites = groups
        self.owner.mark_dirty()
        self._refresh_prereqs()
        self.owner.refresh_canvas()

    def _pick_mutex(self) -> None:
        if not self._guard():
            return
        focus = self.focus_obj

        def picked(values: list[str]) -> None:
            self.owner.set_mutex(focus, values)
            self._refresh_mutex()

        options = self.owner.known_id_options(exclude=focus.id)
        MultiPickDialog(self, self.owner, self.t("focuses.inspector.mutex"),
                        options, picked, preselected=set(focus.mutually_exclusive))

    def _edit_script(self, name: str) -> None:
        focus = self.focus_obj
        if focus is None:
            return
        kinds = _SCRIPT_KINDS.get(name, ("effect", "trigger"))
        if not self._editable:
            ScriptEditorDialog(self, self.owner, name, focus.get_script(name),
                               lambda text: None, kinds, focus.id)
            return

        def submitted(text: str) -> None:
            try:
                focus.set_script(name, text)
            except Exception:
                return
            self.owner.mark_dirty()
            self._refresh_scripts()

        ScriptEditorDialog(self, self.owner, name, focus.get_script(name),
                           submitted, kinds, focus.id)

    def _preview(self) -> None:
        if self.focus_obj is not None:
            FocusPreviewDialog(self, self.owner, self.focus_obj)
