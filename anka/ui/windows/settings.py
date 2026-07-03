"""Settings screen: paths, language, theme. Persists to settings.json on Apply."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk

from ...config.settings import Settings


class SettingsScreen(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, style="TFrame")
        self.app = app
        self._vars: dict[str, tk.StringVar] = {}
        self._status_labels: dict[str, ttk.Label] = {}
        self._build()

    def _build(self) -> None:
        t = self.app.t
        s = self.app.settings.current

        header = ttk.Frame(self, style="TFrame")
        header.pack(fill="x", padx=24, pady=(20, 10))
        ttk.Button(header, text="‹ " + t("common.back"), command=self.app.show_main_menu).pack(side="left")
        ttk.Label(header, text=t("settings.title"), style="Title.TLabel").pack(side="left", padx=16)

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
        self._lang = tk.StringVar(value=s.language)
        langs = self.app.translator.available_languages() or ["ru", "en"]
        ttk.Combobox(row, textvariable=self._lang, values=langs, state="readonly",
                     width=10).grid(row=1, column=0, padx=(0, 24), pady=4)

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
        ttk.Button(footer, text=t("common.apply"), style="Accent.TButton",
                   command=self._apply).pack(side="left")
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
                   command=lambda: self._browse(var)).grid(row=1, column=1, padx=(8, 0))
        status = ttk.Label(block, text="", style="CardMuted.TLabel")
        status.grid(row=2, column=0, sticky="w")
        self._status_labels[key] = status

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

    def _browse(self, var: tk.StringVar) -> None:
        path = filedialog.askdirectory(initialdir=var.get() or None)
        if path:
            var.set(path)
            self._refresh_status()

    def _refresh_status(self) -> None:
        probe = Settings(
            game_path=self._vars["game_path"].get(),
            local_mods_path=self._vars["local_mods_path"].get(),
            workshop_mods_path=self._vars["workshop_mods_path"].get(),
        )
        validity = probe.validate_paths()
        for key, label in self._status_labels.items():
            value = self._vars[key].get()
            if not value:
                label.configure(text="")
                continue
            ok = validity[key]
            label.configure(
                text=("✔ " + self.app.t("settings.path_ok")) if ok
                else ("✕ " + self.app.t("settings.path_invalid")),
                foreground=self.app.palette.text_muted if ok else self.app.palette.danger,
            )

    def _theme_label(self, code: str) -> str:
        return self.app.t("settings.theme.light") if code == "light" else self.app.t("settings.theme.dark")

    def _persist(self) -> None:
        """Save the form into settings.json (no navigation/rebuild)."""
        self.app.settings.update(
            game_path=self._vars["game_path"].get().strip(),
            local_mods_path=self._vars["local_mods_path"].get().strip(),
            workshop_mods_path=self._vars["workshop_mods_path"].get().strip(),
            language=self._lang.get(),
            theme=self._theme_label_map.get(self._theme.get(), "dark"),
            openai_api_key=self._vars["openai_api_key"].get().strip(),
            claude_api_key=self._vars["claude_api_key"].get().strip(),
        )

    def _apply(self) -> None:
        self._persist()
        # Settings change triggers app re-theme/re-language; rebuild this screen.
        self.app.show_settings()

    def on_leave(self) -> None:
        """Auto-save settings when navigating away (no rebuild)."""
        self._persist()
