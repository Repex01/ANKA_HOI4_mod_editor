"""New Folder wizard: creates a complete custom tech-tree folder in one pass —
the ``technology_tags`` declaration, the ``.gui`` container/tab/item skeleton
(cloned from a template folder), the two-frame tab sprite and the localisation.
"""
from __future__ import annotations

import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ...config.constants import SUPPORTED_IMPORT_FORMATS
from ..common import BaseDialog

_ID_RE = re.compile(r"^\w+$")
_LEDGERS = ("army", "navy", "air", "military", "civilian", "all", "hidden")


class NewFolderDialog(BaseDialog):
    def __init__(self, master, editor, on_done):
        super().__init__(master, editor, editor.t("technologies.new_folder"),
                         (500, 430))
        self.editor = editor
        self._on_done = on_done

        body = ttk.Frame(self, style="Card.TFrame", padding=16)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.columnconfigure(1, weight=1)

        def label(r, key):
            ttk.Label(body, text=self.t(key), style="CardMuted.TLabel").grid(
                row=r, column=0, sticky="w", pady=4)

        label(0, "technologies.folder.id")
        self._id = tk.StringVar(value="my_folder")
        ttk.Entry(body, textvariable=self._id).grid(row=0, column=1, columnspan=2,
                                                    sticky="ew", padx=(10, 0), pady=4)

        label(1, "technologies.folder.display_name")
        # NB: not `self._name` — that is tkinter's internal widget name.
        self._display_name = tk.StringVar()
        ttk.Entry(body, textvariable=self._display_name).grid(row=1, column=1, columnspan=2,
                                                      sticky="ew", padx=(10, 0),
                                                      pady=4)

        label(2, "technologies.col.ledger")
        self._ledger = tk.StringVar(value="army")
        ttk.Combobox(body, textvariable=self._ledger, values=_LEDGERS,
                     state="readonly", width=12).grid(row=2, column=1, sticky="w",
                                                      padx=(10, 0), pady=4)

        self._doctrine = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text=self.t("technologies.tags.doctrine"),
                        style="Card.TCheckbutton", variable=self._doctrine).grid(
            row=3, column=1, sticky="w", padx=(10, 0))

        label(4, "technologies.folder.template")
        views = editor.gui.folder_views()
        templates = [fid for fid, v in views.items()
                     if v.has_gui and not v.definition.doctrine]
        self._template = tk.StringVar(
            value="infantry_folder" if "infantry_folder" in templates
            else (templates[0] if templates else ""))
        ttk.Combobox(body, textvariable=self._template, values=templates,
                     state="readonly").grid(row=4, column=1, columnspan=2,
                                            sticky="ew", padx=(10, 0), pady=4)

        label(5, "technologies.folder.tab_icon")
        self._icon = tk.StringVar()
        ttk.Entry(body, textvariable=self._icon).grid(row=5, column=1, sticky="ew",
                                                      padx=(10, 0), pady=4)
        ttk.Button(body, text="…", width=3, command=self._browse).grid(
            row=5, column=2, padx=(4, 0))
        ttk.Label(body, text=self.t("technologies.folder.tab_icon_hint"),
                  style="CardMuted.TLabel", wraplength=420, justify="left").grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(2, 0))

        ttk.Label(body, text=self.t("technologies.folder.gui_note"),
                  style="CardMuted.TLabel", wraplength=420, justify="left").grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(10, 0))

        self.buttons_row(body, self.t("common.create")).grid(
            row=8, column=0, columnspan=3, sticky="ew", pady=(14, 0))

    def _browse(self) -> None:
        path = filedialog.askopenfilename(filetypes=[
            (self.t("common.images"),
             " ".join(f"*{e}" for e in SUPPORTED_IMPORT_FORMATS))])
        if path:
            self._icon.set(path)

    def _submit(self) -> None:
        editor = self.editor
        folder_id = self._id.get().strip()
        if not _ID_RE.match(folder_id):
            messagebox.showerror("ANKA", self.t("technologies.folder.err_id"),
                                 parent=self)
            return
        if folder_id in editor.service.folders():
            messagebox.showerror("ANKA", self.t("technologies.folder.err_exists"),
                                 parent=self)
            return
        template = self._template.get()
        if not template:
            messagebox.showerror("ANKA", self.t("technologies.folder.err_template"),
                                 parent=self)
            return
        doctrine = self._doctrine.get()

        # 1. tags declaration (additive mod file)
        try:
            editor.service.add_folder(folder_id, ledger=self._ledger.get(),
                                      doctrine=doctrine)
        except ValueError as exc:
            messagebox.showerror("ANKA", str(exc), parent=self)
            return

        # 2. tab sprite: imported strip, or reuse of the template's texture
        tab_sprite = f"GFX_{folder_id}_tab"
        icon_path = self._icon.get().strip()
        if icon_path:
            try:
                dds, _gfx = editor.context.icons.add_tech_tab_icon(
                    Path(icon_path), folder_id)
                editor.interface.resolver().add(tab_sprite, dds)
            except Exception as exc:
                messagebox.showerror("ANKA", self.t("technologies.err.icon",
                                                    error=str(exc)), parent=self)
                icon_path = ""
        if not icon_path:
            tab_sprite = self._reuse_template_tab(template, folder_id) or tab_sprite

        # 3. GUI skeleton
        try:
            editor.gui.create_folder_gui(folder_id, doctrine=doctrine,
                                         template_folder=template,
                                         tab_sprite=tab_sprite)
        except Exception as exc:
            messagebox.showerror("ANKA", self.t("technologies.err.save",
                                                error=str(exc)), parent=self)
            return

        # 4. localisation
        name = self._display_name.get().strip()
        if name:
            editor.service.loc_set(folder_id, editor.loc_language, name)

        self.destroy()
        self._on_done(folder_id)

    def _reuse_template_tab(self, template: str, folder_id: str) -> str | None:
        """No custom icon: register GFX_<folder>_tab pointing at the template
        tab's texture so the new tab is visible immediately."""
        editor = self.editor
        sdef = editor.interface.sprite_catalog().get(f"GFX_{template}_tab")
        if sdef is None or not sdef.view.texture:
            return None
        from ...core.gfx import SpriteRegistry
        gfx_path = editor.context.mod.path / "interface/anka_technologies.gfx"
        registry = SpriteRegistry(gfx_path)
        registry.register(f"GFX_{folder_id}_tab", sdef.view.texture,
                          noOfFrames=sdef.view.no_of_frames()).save()
        resolved = editor.interface.resolver().resolve(f"GFX_{template}_tab")
        if resolved is not None:
            editor.interface.resolver().add(f"GFX_{folder_id}_tab", resolved)
        return f"GFX_{folder_id}_tab"
