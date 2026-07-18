"""Settings screen: paths, language, theme. Persists to settings.json on Apply."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ...config.settings import Settings

# Native display names of the UI languages (locale code → name).
_LANG_NAMES = {"en": "English", "ru": "Русский", "uk": "Українська"}


class SettingsScreen(ttk.Frame):
    def __init__(self, master, app, first_run: bool = False):
        super().__init__(master, style="TFrame")
        self.app = app
        self._first_run = first_run
        self._vars: dict[str, tk.StringVar] = {}
        self._status_labels: dict[str, ttk.Label] = {}
        self._is_ph: dict[str, bool] = {}          # entry currently showing its placeholder
        self._build()

    def _build(self) -> None:
        t = self.app.t
        s = self.app.settings.current

        header = ttk.Frame(self, style="TFrame")
        header.pack(fill="x", padx=24, pady=(20, 10))
        # On first launch there's nowhere to go "back" to — show a welcome hint
        # instead and let the user configure paths before continuing.
        if not self._first_run:
            ttk.Button(header, text="‹ " + t("common.back"),
                       command=self._back).pack(side="left")
        ttk.Label(header, text=t("settings.title"), style="Title.TLabel").pack(side="left", padx=16)
        if self._first_run:
            ttk.Label(self, text=t("settings.first_run_hint"), style="Muted.TLabel",
                      wraplength=620, justify="left").pack(fill="x", padx=24, pady=(0, 4))

        card = ttk.Frame(self, style="Card.TFrame", padding=24)
        card.pack(fill="both", expand=False, padx=24, pady=12)
        card.columnconfigure(0, weight=1)

        self._path_field(card, 0, "game_path", t("settings.game_path"), s.game_path)
        self._path_field(card, 1, "local_mods_path", t("settings.local_mods"), s.local_mods_path)
        self._path_field(card, 2, "workshop_mods_path", t("settings.workshop_mods"), s.workshop_mods_path)

        # Language + theme row
        row = ttk.Frame(card, style="Card.TFrame")
        row.grid(row=3, column=0, sticky="w", pady=(16, 4))

        ttk.Label(row, text=t("settings.language"), style="CardMuted.TLabel").grid(row=0, column=0, sticky="w")
        # Show native language names, not raw locale codes (unknown codes fall
        # back to the code itself so extra locale files still work).
        codes = self.app.translator.available_languages() or ["ru", "en"]
        self._lang_label_map = {_LANG_NAMES.get(c, c): c for c in codes}
        current = next((label for label, c in self._lang_label_map.items()
                        if c == s.language), _LANG_NAMES.get("en", "en"))
        self._lang = tk.StringVar(value=current)
        ttk.Combobox(row, textvariable=self._lang,
                     values=list(self._lang_label_map), state="readonly",
                     width=12).grid(row=1, column=0, padx=(0, 24), pady=4)

        ttk.Label(row, text=t("settings.theme"), style="CardMuted.TLabel").grid(row=0, column=1, sticky="w")
        self._theme_label_map = {t("settings.theme.dark"): "dark", t("settings.theme.light"): "light"}
        self._theme = tk.StringVar(value=self._theme_label(s.theme))
        ttk.Combobox(row, textvariable=self._theme, values=list(self._theme_label_map.keys()),
                     state="readonly", width=12).grid(row=1, column=1, pady=4)

        # API keys (masked; stored in settings.json — for future AI features)
        keys = ttk.Frame(card, style="Card.TFrame")
        keys.grid(row=4, column=0, sticky="ew", pady=(16, 0))
        keys.columnconfigure(1, weight=1)
        ttk.Label(keys, text=t("settings.api_keys"), style="Heading.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self._api_key_field(keys, 1, "openai_api_key", t("settings.openai_key"),
                            s.openai_api_key)
        self._api_key_field(keys, 2, "claude_api_key", t("settings.claude_key"),
                            s.claude_api_key)
        ttk.Label(keys, text=t("settings.api_keys_hint"), style="CardMuted.TLabel",
                  wraplength=560, justify="left").grid(row=3, column=0, columnspan=3,
                                                       sticky="w", pady=(4, 0))

        # Apply
        footer = ttk.Frame(self, style="TFrame")
        footer.pack(fill="x", padx=24, pady=8)
        ttk.Button(footer,
                   text=t("settings.continue") if self._first_run else t("common.apply"),
                   style="Accent.TButton", command=self._apply).pack(side="left")
        self._saved = ttk.Label(footer, text="", style="Muted.TLabel")
        self._saved.pack(side="left", padx=14)

        self._refresh_status()

    def _path_field(self, parent, row: int, key: str, label: str, value: str) -> None:
        block = ttk.Frame(parent, style="Card.TFrame")
        block.grid(row=row, column=0, sticky="ew", pady=8)
        block.columnconfigure(0, weight=1)

        ttk.Label(block, text=label, style="CardMuted.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        var = tk.StringVar(value=value)
        self._vars[key] = var
        entry = ttk.Entry(block, textvariable=var)
        entry.grid(row=1, column=0, sticky="ew", pady=2)
        entry.bind("<KeyRelease>", lambda e: self._refresh_status())
        ttk.Button(block, text=self.app.t("common.browse"), width=10,
                   command=lambda: self._browse(key, var)).grid(row=1, column=1, padx=(8, 0))
        status = ttk.Label(block, text="", style="CardMuted.TLabel")
        status.grid(row=2, column=0, sticky="w")
        self._status_labels[key] = status
        self._attach_placeholder(entry, var, key,
                                 self.app.t(f"settings.example.{key}"))

    def _attach_placeholder(self, entry, var, key: str, placeholder: str) -> None:
        """Show a greyed example path while the field is empty and unfocused. The
        placeholder is display-only — `_value()` treats it as empty so it never
        gets saved as a real path."""
        muted, normal = self.app.palette.text_muted, self.app.palette.text

        def show_ph() -> None:
            self._is_ph[key] = True
            var.set(placeholder)
            entry.configure(foreground=muted)

        def clear_ph() -> None:
            self._is_ph[key] = False
            var.set("")
            entry.configure(foreground=normal)

        if var.get().strip():
            self._is_ph[key] = False
            entry.configure(foreground=normal)
        else:
            show_ph()
        entry.bind("<FocusIn>", lambda e: clear_ph() if self._is_ph.get(key) else None,
                   add="+")
        entry.bind("<FocusOut>",
                   lambda e: show_ph() if not var.get().strip() else None, add="+")

    def _value(self, key: str) -> str:
        """A path field's real value (empty when it only shows its placeholder)."""
        return "" if self._is_ph.get(key) else self._vars[key].get().strip()

    def _api_key_field(self, parent, row: int, key: str, label: str, value: str) -> None:
        ttk.Label(parent, text=label, style="CardMuted.TLabel").grid(
            row=row, column=0, sticky="w", pady=3, padx=(0, 10))
        var = tk.StringVar(value=value)
        self._vars[key] = var
        entry = ttk.Entry(parent, textvariable=var, show="•")
        entry.grid(row=row, column=1, sticky="ew", pady=3)
        shown = tk.BooleanVar(value=False)

        def toggle() -> None:
            shown.set(not shown.get())
            entry.configure(show="" if shown.get() else "•")

        ttk.Button(parent, text="👁", width=3, command=toggle).grid(
            row=row, column=2, padx=(6, 0))

    def _browse(self, key: str, var: tk.StringVar) -> None:
        path = filedialog.askdirectory(initialdir=self._value(key) or None)
        if path:
            self._is_ph[key] = False
            # Windows: backslashed; Linux/macOS: kept as-is.
            var.set(Settings.normalize_separators(path))
            self._refresh_status()

    def _refresh_status(self) -> None:
        probe = Settings(
            game_path=self._value("game_path"),
            local_mods_path=self._value("local_mods_path"),
            workshop_mods_path=self._value("workshop_mods_path"),
        )
        validity = probe.validate_paths()
        for key, label in self._status_labels.items():
            value = self._value(key)
            if not value:
                label.configure(text="")
                continue
            if not validity[key]:
                label.configure(text="✕ " + self.app.t("settings.path_invalid"),
                                foreground=self.app.palette.danger)
                continue
            issue = Settings.path_format_issue(key, value)
            if issue:
                label.configure(text="⚠ " + self.app.t(f"settings.err.{issue}"),
                                foreground=self.app.palette.danger)
            else:
                label.configure(text="✔ " + self.app.t("settings.path_ok"),
                                foreground=self.app.palette.text_muted)

    def _theme_label(self, code: str) -> str:
        return self.app.t("settings.theme.light") if code == "light" else self.app.t("settings.theme.dark")

    def _persist(self) -> None:
        """Save the form into settings.json (no navigation/rebuild). On Windows
        paths are normalized to backslashes — the HOI4/launcher convention."""
        self.app.settings.update(
            game_path=Settings.normalize_separators(self._value("game_path")),
            local_mods_path=Settings.normalize_separators(self._value("local_mods_path")),
            workshop_mods_path=Settings.normalize_separators(self._value("workshop_mods_path")),
            language=self._lang_label_map.get(self._lang.get(), "en"),
            theme=self._theme_label_map.get(self._theme.get(), "dark"),
            openai_api_key=self._vars["openai_api_key"].get().strip(),
            claude_api_key=self._vars["claude_api_key"].get().strip(),
        )

    def _apply(self) -> None:
        self._persist()
        # First-run: settings.json now exists, so head to the main menu (Edit mod is
        # enabled there once the required paths are set). Otherwise rebuild in place.
        if self._first_run:
            if self._saved_errors() and not self._errors_exit_prompt():
                return
            self.app.show_main_menu()
        else:
            # Settings change triggers app re-theme/re-language; rebuild this screen.
            self.app.show_settings()

    # --------------------------------------------------------- leave guards
    def _dirty(self) -> bool:
        """Does the form differ from what settings.json currently holds?"""
        s = self.app.settings.current
        norm = Settings.normalize_separators
        return (norm(self._value("game_path")) != s.game_path
                or norm(self._value("local_mods_path")) != s.local_mods_path
                or norm(self._value("workshop_mods_path")) != s.workshop_mods_path
                or self._lang_label_map.get(self._lang.get(), "en") != s.language
                or self._theme_label_map.get(self._theme.get(), "dark") != s.theme
                or self._vars["openai_api_key"].get().strip() != s.openai_api_key
                or self._vars["claude_api_key"].get().strip() != s.claude_api_key)

    def _saved_errors(self) -> bool:
        """Any ✕/⚠ problem among the SAVED (persisted) paths? Empty fields
        don't count — the main menu simply keeps mod editing disabled then."""
        s = self.app.settings.current
        validity = s.validate_paths()
        for key in ("game_path", "local_mods_path", "workshop_mods_path"):
            value = getattr(s, key)
            if not value:
                continue
            if not validity.get(key, False) or Settings.path_format_issue(key, value):
                return True
        return False

    def _errors_exit_prompt(self) -> bool:
        """Saved settings contain path errors: show the warning with an
        explicit "Exit anyway" choice. Returns True when the user still wants
        to leave."""
        t = self.app.t
        top = self.winfo_toplevel()
        dlg = tk.Toplevel(self)
        self._err_dlg = dlg                    # exposed for tests
        dlg.title("ANKA")
        dlg.transient(top)
        dlg.resizable(False, False)
        dlg.configure(bg=self.app.palette.bg)
        result = {"exit": False}
        body = ttk.Frame(dlg, style="Card.TFrame", padding=16)
        body.pack(fill="both", expand=True, padx=10, pady=10)
        ttk.Label(body, text="⚠ " + t("settings.err.exit_errors"),
                  style="Card.TLabel", wraplength=400, justify="left").pack(
            anchor="w")
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x", pady=(14, 0))

        def leave() -> None:
            result["exit"] = True
            dlg.destroy()

        ttk.Button(row, text=t("common.cancel"),
                   command=dlg.destroy).pack(side="right", padx=(6, 0))
        dlg._exit_btn = ttk.Button(row, text=t("settings.exit_anyway"),
                                   command=leave)
        dlg._exit_btn.pack(side="right")
        dlg.update_idletasks()
        x = top.winfo_rootx() + (top.winfo_width() - dlg.winfo_reqwidth()) // 2
        y = top.winfo_rooty() + (top.winfo_height() - dlg.winfo_reqheight()) // 3
        dlg.geometry(f"+{max(0, x)}+{max(0, y)}")
        dlg.grab_set()
        from ..widgets.modal import guard_modal
        guard_modal(dlg, top)
        dlg.wait_window()
        return result["exit"]

    def _back(self) -> None:
        """Back to menu: warn about unsaved changes; on saved path errors show
        the warning with an "Exit anyway" escape hatch."""
        t = self.app.t
        discard = False
        if self._dirty():
            if not messagebox.askyesno("ANKA", t("settings.confirm_unsaved")):
                return
            discard = True                    # leave without applying the form
        if self._saved_errors() and not self._errors_exit_prompt():
            return
        self._discard = discard
        self.app.show_main_menu()

    def on_leave(self) -> None:
        """Auto-save settings when navigating away (no rebuild) — unless the
        user chose to discard the form in the unsaved-changes prompt."""
        if getattr(self, "_discard", False):
            self._discard = False
            return
        self._persist()
