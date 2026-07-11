"""Inspector for one scripted-GUI entry.

Scalar fields come from `SGUI_SCALARS` (comboboxes for context types / parent
window tokens, a window picker fed by the interface service's window index);
`visible`/`ai_*` are script rows; `effects`/`triggers`/`properties`/
`dynamic_lists` are structured key lists whose "add" wizards offer the actual
element names of the bound window — the big UX win over hand-typing
``<element>_<modifiers>_click`` keys.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ...core.guitypes.schema import AttrKind, SGUI_SCALARS
from ...core.pdx import Block, Pair, dumps
from ...core.pdx import parse as pdx_parse
from ...services.scripted_gui_service import build_handler_key
from ..common import (InspectorBase, PdxPreviewDialog, ScriptEditorDialog,
                      SinglePickDialog, TextPromptDialog)

_SCRIPT_FIELDS = ("visible", "ai_enabled", "ai_check", "ai_check_scope",
                  "ai_test_scopes", "ai_weights")

_HANDLER_KINDS = (
    ("click", [], "click"),
    ("right_click", ["right"], "click"),
    ("shift_click", ["shift"], "click"),
    ("control_click", ["control"], "click"),
    ("alt_click", ["alt"], "click"),
    ("click_enabled", [], "click_enabled"),
    ("visible", [], "visible"),
)

_CLICKABLE = ("buttontype", "guibuttontype", "checkboxtype",
              "containerwindowtype", "icontype")


class SguiInspector(InspectorBase):
    def __init__(self, master, owner):
        super().__init__(master, owner)
        self.doc = None
        self.entry = None

    # -------------------------------------------------------------------- show
    def show(self, doc, entry, editable: bool) -> None:
        self.flush_pending()
        self._loading = True
        self.doc, self.entry, self._editable = doc, entry, editable
        for child in self.body.winfo_children():
            child.destroy()
        if entry is not None:
            self._build_form()
            self._set_state_all(editable)
        self._loading = False

    def _rebuild(self) -> None:
        self.show(self.doc, self.entry, self._editable)

    def _build_form(self) -> None:
        entry, b = self.entry, self.body
        r = 0
        ttk.Label(b, text=entry.name + ("  🔒" if not self._editable else ""),
                  style="Heading.TLabel").grid(row=r, column=0, columnspan=2,
                                               sticky="w")
        r += 1
        ttk.Label(b, text=self.doc.ref.rel_file, style="CardMuted.TLabel",
                  wraplength=430).grid(row=r, column=0, columnspan=2,
                                       sticky="w", pady=(0, 6))
        r += 1

        for attr in SGUI_SCALARS:
            r = self._scalar_row(r, attr)

        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew",
                              pady=8)
        r += 1
        for name in _SCRIPT_FIELDS:
            r = self._script_row(r, name)

        r = self._handler_section(r, "effects", self._add_effect)
        r = self._handler_section(r, "triggers", self._add_trigger)
        r = self._handler_section(r, "properties", self._add_property)
        r = self._handler_section(r, "dynamic_lists", self._add_dynamic_list)

        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew",
                              pady=8)
        r += 1
        actions = ttk.Frame(b, style="Card.TFrame")
        actions.grid(row=r, column=0, columnspan=2, sticky="ew")
        ttk.Button(actions, text="👁 " + self.t("focuses.preview"),
                   command=self._preview_pdx).pack(side="left")
        ttk.Button(actions, text="▣ " + self.t("interface.sgui.open_window"),
                   command=self._open_window).pack(side="left", padx=6)
        self._delete_btn = ttk.Button(
            actions, text="🗑 " + self.t("interface.gfx.delete"),
            command=self._delete)
        self._delete_btn.pack(side="right")

    # -------------------------------------------------------------- field rows
    def _scalar_row(self, row: int, attr) -> int:
        entry, b = self.entry, self.body
        name = attr.name
        var = tk.StringVar(value=entry.get_attr(name))
        ttk.Label(b, text=name, style="CardMuted.TLabel").grid(
            row=row, column=0, sticky="w", pady=3)

        def commit() -> None:
            if not self._guard():
                return
            entry.set_attr(name, var.get())
            self.owner.mark_dirty(self.doc)

        if name == "window_name":
            values = tuple(sorted(self.owner.window_names()))
            box = ttk.Combobox(b, textvariable=var, width=28, values=values)
            box.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
            box.bind("<<ComboboxSelected>>", lambda _e: commit())
            var.trace_add("write", lambda *_a: self._debounce(name, commit))
        elif attr.kind == AttrKind.ENUM:
            box = ttk.Combobox(b, textvariable=var, width=28,
                               state="readonly",
                               values=("",) + attr.enum_values)
            box.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
            box.bind("<<ComboboxSelected>>", lambda _e: commit())
        else:
            width = 10 if attr.kind in (AttrKind.INT, AttrKind.FLOAT) else 28
            e = ttk.Entry(b, textvariable=var, width=width)
            e.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
            var.trace_add("write", lambda *_a: self._debounce(name, commit))
            e.bind("<FocusOut>", lambda _e: commit())
        return row + 1

    def _script_row(self, row: int, name: str) -> int:
        entry, b = self.entry, self.body
        line = ttk.Frame(b, style="Card.TFrame")
        line.grid(row=row, column=0, columnspan=2, sticky="ew", pady=1)
        filled = bool(entry.get_script(name).strip())
        ttk.Label(line, text="●" if filled else "○",
                  style="CardMuted.TLabel", width=2).pack(side="left")
        ttk.Label(line, text=name, style="CardMuted.TLabel").pack(side="left")
        ttk.Button(line, text="✎", width=3,
                   command=lambda: self._edit_script(name)).pack(
            side="left", padx=(6, 0))
        return row + 1

    def _edit_script(self, name: str) -> None:
        entry = self.entry

        def submitted(text: str) -> None:
            try:
                entry.set_script(name, text)
            except Exception:
                return
            self.owner.mark_dirty(self.doc)
            self._rebuild()

        ScriptEditorDialog(self, self.owner, name, entry.get_script(name),
                           submitted if self._editable else (lambda t: None),
                           ("trigger", "effect"), entry.name)

    # ---------------------------------------------------------- handler blocks
    def _handler_section(self, row: int, block_key: str, add_command) -> int:
        entry, b = self.entry, self.body
        ttk.Separator(b).grid(row=row, column=0, columnspan=2, sticky="ew",
                              pady=(8, 2))
        row += 1
        head = ttk.Frame(b, style="Card.TFrame")
        head.grid(row=row, column=0, columnspan=2, sticky="ew")
        ttk.Label(head, text=block_key, style="CardMuted.TLabel",
                  font=("Segoe UI Semibold", 9)).pack(side="left")
        ttk.Button(head, text="➕", width=3, command=add_command).pack(
            side="right")
        row += 1
        block = entry.get_attr_block(block_key)
        if block is None:
            return row
        for pair in list(block.pairs()):
            line = ttk.Frame(b, style="Card.TFrame")
            line.grid(row=row, column=0, columnspan=2, sticky="ew", pady=1)
            ttk.Label(line, text=pair.key, style="CardMuted.TLabel").pack(
                side="left", padx=(10, 0))
            ttk.Button(line, text="🗑", width=3,
                       command=lambda k=pair.key, bk=block_key:
                       self._remove_handler(bk, k)).pack(side="right")
            ttk.Button(line, text="✎", width=3,
                       command=lambda p=pair, bk=block_key:
                       self._edit_handler(bk, p)).pack(side="right",
                                                       padx=(0, 2))
            row += 1
        return row

    def _handler_block(self, block_key: str, create: bool = False) -> Block | None:
        block = self.entry.get_attr_block(block_key)
        if block is None and create:
            block = Block()
            self.entry.block.set_ci(block_key, block)
        return block

    def _edit_handler(self, block_key: str, pair: Pair) -> None:
        text = (dumps(pair.value, top_level=False)
                if isinstance(pair.value, Block) else str(pair.value.raw))

        def submitted(new_text: str) -> None:
            try:
                parsed = pdx_parse(new_text, recover=False)
            except Exception:
                return
            pair.value = Block(parsed.items)
            self.owner.mark_dirty(self.doc)

        kinds = ("effect",) if block_key == "effects" else ("trigger", "effect")
        ScriptEditorDialog(self, self.owner, f"{block_key}: {pair.key}", text,
                           submitted if self._editable else (lambda t: None),
                           kinds, self.entry.name)

    def _remove_handler(self, block_key: str, key: str) -> None:
        if not self._editable:
            return
        block = self._handler_block(block_key)
        if block is None:
            return
        block.remove(key)
        if not len(block.items):
            self.entry.block.remove_ci(block_key)
        self.owner.mark_dirty(self.doc)
        self._rebuild()

    def _add_key(self, block_key: str, key: str,
                 payload: Block | None = None) -> None:
        block = self._handler_block(block_key, create=True)
        block.add(key, payload if payload is not None else Block())
        self.owner.mark_dirty(self.doc)
        self._rebuild()

    # ------------------------------------------------------------ add wizards
    def _pick_element(self, title: str, only: tuple[str, ...],
                      then) -> None:
        elements = self.owner.window_elements(
            self.entry.get_attr("window_name"))
        options = [(f"{name} · {type_key}", name)
                   for name, type_key in sorted(elements.items())
                   if not only or type_key.lower() in only]
        options.append((f"✏ {self.t('interface.sgui.custom_element')}",
                        "__custom__"))

        def picked(value: str) -> None:
            if value == "__custom__":
                TextPromptDialog(self, self, title,
                                 self.t("interface.sgui.element_name"),
                                 then, pattern=r"^\w+$")
            else:
                then(value)

        SinglePickDialog(self, self, title, options, picked)

    def _add_effect(self) -> None:
        if not self._editable:
            return

        def element_picked(element: str) -> None:
            options = [(label, label) for label, _m, _k in _HANDLER_KINDS
                       if label not in ("click_enabled", "visible")]
            SinglePickDialog(self, self, self.t("interface.sgui.pick_kind"),
                             options,
                             lambda kind: self._add_key(
                                 "effects", _compose(element, kind)))

        self._pick_element(self.t("interface.sgui.pick_element"),
                           _CLICKABLE, element_picked)

    def _add_trigger(self) -> None:
        if not self._editable:
            return

        def element_picked(element: str) -> None:
            options = [("click_enabled", "click_enabled"),
                       ("visible", "visible")]
            SinglePickDialog(self, self, self.t("interface.sgui.pick_kind"),
                             options,
                             lambda kind: self._add_key(
                                 "triggers", _compose(element, kind)))

        self._pick_element(self.t("interface.sgui.pick_element"), (),
                           element_picked)

    def _add_property(self) -> None:
        if not self._editable:
            return

        def picked(element: str) -> None:
            payload = Block()
            payload.add("frame", "some_variable")
            self._add_key("properties", element, payload)

        self._pick_element(self.t("interface.sgui.pick_element"), (), picked)

    def _add_dynamic_list(self) -> None:
        if not self._editable:
            return

        def picked(element: str) -> None:
            payload = Block()
            payload.add("array", "my_array")
            payload.add("entry_container", "my_entry_container")
            self._add_key("dynamic_lists", element, payload)

        self._pick_element(self.t("interface.sgui.pick_gridbox"),
                           ("gridboxtype",), picked)

    # ----------------------------------------------------------------- actions
    def _open_window(self) -> None:
        window = self.entry.get_attr("window_name") if self.entry else ""
        if window:
            self.owner.open_in_designer(window)

    def _preview_pdx(self) -> None:
        if self.entry is not None:
            PdxPreviewDialog(self, self.owner, self.entry.name,
                             Block([Pair("scripted_gui",
                                         Block([self.entry.pair]))]))

    def _delete(self) -> None:
        if self.entry is None or not self._editable:
            return
        if not messagebox.askyesno(
                "ANKA", self.t("interface.sgui.confirm_delete",
                               name=self.entry.name)):
            return
        self.owner.delete_entry(self.doc, self.entry)


def _compose(element: str, kind_label: str) -> str:
    mods, kind = next((m, k) for label, m, k in _HANDLER_KINDS
                      if label == kind_label)
    return build_handler_key(element, mods, kind)
