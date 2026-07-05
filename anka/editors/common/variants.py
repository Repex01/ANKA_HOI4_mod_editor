"""Reusable editor for ordered lists of conditional text/sprite variants.

HOI4 events may repeat ``title``/``desc``/``picture`` several times, each entry
optionally carrying a trigger (``desc = { text = X trigger = {...} }``); the game
shows the FIRST variant whose trigger holds, so order matters. This widget edits
such a list over the `TextVariant` API of `services.event_service.Event`:

* ``value_kind="loc"``    — key entry + per-language localisation text (title/desc);
* ``value_kind="sprite"`` — thumbnail + sprite gallery picker (picture).

Owner protocol (an editor module): ``t``, ``palette``, ``loc_language``,
``loc_get/loc_set``, ``resolver``, ``resolver_ready()``, plus the protocol
`ScriptEditorDialog` itself needs for the trigger editor.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from PIL import Image, ImageTk

from .dialogs import IconPickerDialog
from .script_editor import ScriptEditorDialog

_LETTERS = "abcdefghijklmnopqrstuvwxyz"


class VariantListEditor(ttk.Frame):
    """One section of variant rows: [▲][▼] value [trigger ✎] [✕] + an add button."""

    THUMB = (110, 44)

    def __init__(self, master, owner, key: str, value_kind: str,
                 on_change: Callable[[], None],
                 sprite_prefixes: Callable[[], tuple[str, ...]] | None = None,
                 on_import: Callable[[object], str | None] | None = None):
        super().__init__(master, style="Card.TFrame")
        self.owner = owner
        self.t = owner.t
        self.palette = owner.palette
        self._key = key                     # "title" | "desc" | "picture"
        self._value_kind = value_kind       # "loc" | "sprite"
        self._on_change = on_change
        self._sprite_prefixes = sprite_prefixes or (lambda: ("GFX_",))
        self._on_import = on_import
        self._event = None
        self._editable = True
        self._loading = False
        self._jobs: dict[str, tuple[str, object]] = {}
        self._rows: list[dict] = []

        head = ttk.Frame(self, style="Card.TFrame")
        head.pack(fill="x")
        label = ttk.Label(head, text=self.t(f"events.field.{key}"),
                          style="Card.TLabel")
        label.pack(side="left")
        from ...ui.widgets.tooltip import attach_help
        attach_help(label, self.t, f"event.{key}", self.palette)
        self._add_btn = ttk.Button(head, text="➕ " + self.t("events.add_variant"),
                                   command=self._add)
        self._add_btn.pack(side="left", padx=(8, 0))
        self._order_hint = ttk.Label(head, text=self.t("events.variant_order_hint"),
                                     style="CardMuted.TLabel")
        self._rows_host = ttk.Frame(self, style="Card.TFrame")
        self._rows_host.pack(fill="x", pady=(2, 0))

    # ------------------------------------------------------------------ public
    def show(self, event, editable: bool) -> None:
        self.flush()
        self._event = event
        self._editable = editable
        self._rebuild()

    def flush(self) -> None:
        for job, _c in list(self._jobs.values()):
            self.after_cancel(job)
        pending = [c for _j, c in self._jobs.values()]
        self._jobs.clear()
        for commit in pending:
            commit()

    # --------------------------------------------------------------- internals
    def _debounce(self, name: str, commit, delay: int = 900) -> None:
        if self._loading:
            return
        pending = self._jobs.pop(name, None)
        if pending is not None:
            self.after_cancel(pending[0])
        job = self.after(delay, lambda: (self._jobs.pop(name, None), commit()))
        self._jobs[name] = (job, commit)

    def _variants(self) -> list:
        return self._event.variants(self._key) if self._event is not None else []

    def _rebuild(self) -> None:
        self._loading = True
        try:
            for row in self._rows:
                row["frame"].destroy()
            self._rows.clear()
            variants = self._variants()
            # the order hint only matters once there is something to reorder
            if len(variants) > 1:
                self._order_hint.pack(side="left", padx=(10, 0))
            else:
                self._order_hint.pack_forget()
            for i, variant in enumerate(variants):
                self._build_row(i, variant, len(variants))
            state = "normal" if self._editable and self._event is not None else "disabled"
            self._add_btn.configure(state=state)
        finally:
            self._loading = False

    def _build_row(self, index: int, variant, total: int) -> None:
        row: dict = {"variant": variant}
        frame = ttk.Frame(self._rows_host, style="Card.TFrame")
        frame.pack(fill="x", pady=1)
        row["frame"] = frame
        state = "normal" if self._editable else "disabled"

        up = ttk.Button(frame, text="▲", width=2, state=state,
                        command=lambda v=variant: self._move(v, -1))
        up.pack(side="left")
        down = ttk.Button(frame, text="▼", width=2, state=state,
                          command=lambda v=variant: self._move(v, +1))
        down.pack(side="left", padx=(1, 4))
        if index == 0:
            up.configure(state="disabled")
        if index == total - 1:
            down.configure(state="disabled")

        if self._value_kind == "sprite":
            self._build_sprite_cell(frame, row, variant, state)
        else:
            self._build_loc_cell(frame, row, variant, state)

        trig = ttk.Button(frame, width=10, state=state,
                          command=lambda v=variant: self._edit_trigger(v))
        trig.pack(side="left", padx=(6, 0))
        row["trigger_btn"] = trig
        self._refresh_trigger(row)
        ttk.Button(frame, text="✕", width=2, state=state,
                   command=lambda v=variant: self._remove(v)).pack(
            side="left", padx=(4, 0))
        self._rows.append(row)

    def _build_loc_cell(self, frame, row, variant, state) -> None:
        key_var = tk.StringVar(value=variant.text)
        key_entry = ttk.Entry(frame, textvariable=key_var, width=22,
                              font=("Consolas", 9), state=state)
        key_entry.pack(side="left")
        text_var = tk.StringVar(value=self.owner.loc_get(variant.text,
                                                         self.owner.loc_language))
        text_entry = ttk.Entry(frame, textvariable=text_var, state=state)
        text_entry.pack(side="left", fill="x", expand=True, padx=(4, 0))
        row.update(key_var=key_var, text_var=text_var)

        uid = str(id(variant))
        key_var.trace_add("write", lambda *_: self._debounce(
            f"key:{uid}", lambda: self._commit_key(row)))
        key_entry.bind("<FocusOut>", lambda e: self._commit_key(row))
        text_var.trace_add("write", lambda *_: self._debounce(
            f"text:{uid}", lambda: self._commit_text(row), 1200))
        text_entry.bind("<FocusOut>", lambda e: self._commit_text(row))

    def _build_sprite_cell(self, frame, row, variant, state) -> None:
        photo = self._thumb(variant.text)
        row["photo"] = photo
        btn = tk.Button(frame, image=photo, bd=0, bg=self.palette.surface_alt,
                        activebackground=self.palette.surface, cursor="hand2",
                        state=state, command=lambda v=variant: self._pick_sprite(v))
        btn.pack(side="left")
        name = ttk.Label(frame, text=variant.text or "—", style="CardMuted.TLabel",
                         wraplength=210)
        name.pack(side="left", padx=(6, 0))
        row.update(thumb_btn=btn, name_label=name)

    def _thumb(self, sprite: str) -> ImageTk.PhotoImage:
        img = None
        path = (self.owner.resolver.resolve(sprite)
                if sprite and self.owner.resolver_ready() else None)
        if path is not None:
            try:
                with Image.open(path) as im:
                    img = im.convert("RGBA")
                img.thumbnail(self.THUMB, Image.LANCZOS)
            except Exception:
                img = None
        if img is None:
            img = Image.new("RGBA", self.THUMB, (0, 0, 0, 0))
        return ImageTk.PhotoImage(img)

    def _refresh_trigger(self, row: dict) -> None:
        filled = bool(row["variant"].trigger_script.strip())
        row["trigger_btn"].configure(text=("● trigger" if filled else "○ trigger"))

    # ------------------------------------------------------------------ commits
    def _commit_key(self, row: dict) -> None:
        if self._loading or not self._editable:
            return
        variant = row["variant"]
        new_key = row["key_var"].get().strip()
        if not new_key or new_key == variant.text:
            return
        # pending loc text belongs to the OLD key — flush it there first
        self.flush()
        variant.text = new_key
        self._on_change()
        was = self._loading
        self._loading = True
        row["text_var"].set(self.owner.loc_get(new_key, self.owner.loc_language))
        self._loading = was

    def _commit_text(self, row: dict) -> None:
        if self._loading or not self._editable:
            return
        variant = row["variant"]
        key = variant.text
        if not key:
            return
        text = row["text_var"].get().strip()
        current = self.owner.loc_get(key, self.owner.loc_language)
        if text != current and (text or current):
            self.owner.loc_set(key, self.owner.loc_language, text)
            if self._key == "title":
                self.owner.refresh_tree_labels()

    # ------------------------------------------------------------------ actions
    def _auto_key(self) -> str:
        event = self._event
        base = f"{event.id}.{'t' if self._key == 'title' else 'd'}"
        used = {v.text for v in self._variants()}
        if base not in used:
            return base
        for letter in _LETTERS:
            candidate = f"{base}.{letter}"
            if candidate not in used:
                return candidate
        return f"{base}.{len(used) + 1}"

    def _add(self) -> None:
        if self._event is None or not self._editable:
            return
        self.flush()
        if self._value_kind == "sprite":
            def picked(sprite: str) -> None:
                if self._event is None:
                    return
                self._event.add_variant(self._key, sprite)
                self._on_change()
                self._rebuild()

            def imported(path) -> None:
                if self._on_import is None:
                    return
                sprite = self._on_import(path)
                if sprite:
                    picked(sprite)

            IconPickerDialog(self, self.owner, self.owner.resolver, "",
                             picked, imported if self._on_import else None,
                             prefixes=self._sprite_prefixes())
            return
        self._event.add_variant(self._key, self._auto_key())
        self._on_change()
        self._rebuild()

    def _remove(self, variant) -> None:
        if self._event is None or not self._editable:
            return
        self.flush()
        self._event.remove_variant(variant)
        self._on_change()
        self._rebuild()

    def _move(self, variant, delta: int) -> None:
        if self._event is None or not self._editable:
            return
        self.flush()
        if self._event.move_variant(variant, delta):
            self._on_change()
            self._rebuild()

    def _pick_sprite(self, variant) -> None:
        if self._event is None or not self._editable:
            return

        def picked(sprite: str) -> None:
            variant.text = sprite
            self._on_change()
            self._rebuild()

        def imported(path) -> None:
            if self._on_import is None:
                return
            sprite = self._on_import(path)
            if sprite:
                picked(sprite)

        IconPickerDialog(self, self.owner, self.owner.resolver, variant.text,
                         picked, imported if self._on_import else None,
                         prefixes=self._sprite_prefixes())

    def _edit_trigger(self, variant) -> None:
        row = next((r for r in self._rows if r["variant"] is variant), None)

        def submitted(text: str) -> None:
            if not self._editable:
                return
            try:
                variant.trigger_script = text
            except Exception:
                return
            self._on_change()
            self._rebuild()

        ScriptEditorDialog(self, self.owner,
                           f"{self._key} · trigger",
                           variant.trigger_script,
                           submitted if self._editable else (lambda text: None),
                           ("trigger",),
                           self._event.id if self._event is not None else "")
        if row is not None:
            self._refresh_trigger(row)
