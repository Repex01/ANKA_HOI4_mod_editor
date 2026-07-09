"""General editor: the default landing tab for a mod.

Shows the mod's descriptor metadata, a quick content summary (how many countries,
ideas, events, ... the mod defines) and the mod's **dependencies** — the sub-mods it
is layered on top of. Dependencies can be added from / removed against the list of
installed mods; changes are written straight back into the ``.mod`` descriptor.

Load order is game → dependencies (in listed order) → this mod, with later layers
overriding earlier ones, so an edited file in this mod always wins over the same file
in a dependency, which in turn wins over the base game.
"""
from __future__ import annotations

from tkinter import ttk

from ...services.mod_repository import ModRepository
from ...ui.widgets import ScrollableFrame
from ..base import EditorModule, EditorRegistry
from ..common.dialogs import MultiPickDialog


@EditorRegistry.register
class GeneralEditor(EditorModule):
    id = "general"
    name_key = "editors.general.name"
    desc_key = "editors.general.desc"
    order = 0

    def build(self, parent) -> ttk.Widget:
        outer = ttk.Frame(parent, style="TFrame")
        scroll = ScrollableFrame(outer, bg=self.palette.surface)
        scroll.pack(fill="both", expand=True)
        body = scroll.body
        body.configure(style="Card.TFrame", padding=20)
        body.columnconfigure(1, weight=1)
        t = self.t
        mod = self.context.mod
        r = 0

        ttk.Label(body, text=mod.name, style="CardTitle.TLabel").grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(0, 2)); r += 1
        ttk.Label(body, text=t("general.subtitle"), style="CardMuted.TLabel").grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(0, 14)); r += 1

        # --- descriptor metadata -------------------------------------------
        ttk.Label(body, text=t("general.section.mod"), style="Heading.TLabel").grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(0, 6)); r += 1
        rows = [
            (t("general.path"), str(mod.path)),
            (t("general.version"), mod.version or "—"),
            (t("general.supported"), mod.supported_version or "—"),
            (t("general.source"), t("general.local") if mod.is_local
             else t("general.workshop")),
            (t("general.tags"), ", ".join(mod.tags) if mod.tags else "—"),
        ]
        for label, value in rows:
            ttk.Label(body, text=label, style="CardMuted.TLabel").grid(
                row=r, column=0, sticky="nw", pady=2)
            ttk.Label(body, text=value, style="Card.TLabel", wraplength=520,
                      justify="left").grid(row=r, column=1, sticky="w", padx=(12, 0),
                                           pady=2); r += 1

        # --- dependencies ---------------------------------------------------
        ttk.Separator(body).grid(row=r, column=0, columnspan=2, sticky="ew",
                                 pady=12); r += 1
        ttk.Label(body, text=t("general.section.dependencies"),
                  style="Heading.TLabel").grid(row=r, column=0, columnspan=2,
                                               sticky="w", pady=(0, 2)); r += 1
        ttk.Label(body, text=t("general.deps.hint"), style="CardMuted.TLabel",
                  wraplength=560, justify="left").grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(0, 6)); r += 1
        self._deps_box = ttk.Frame(body, style="Card.TFrame")
        self._deps_box.grid(row=r, column=0, columnspan=2, sticky="ew"); r += 1
        self._deps_error = ttk.Label(body, text="", style="CardMuted.TLabel",
                                     wraplength=560, justify="left")
        self._deps_error.grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
        self._render_deps()

        # --- content summary ------------------------------------------------
        ttk.Separator(body).grid(row=r, column=0, columnspan=2, sticky="ew",
                                 pady=12); r += 1
        ttk.Label(body, text=t("general.section.content"),
                  style="Heading.TLabel").grid(row=r, column=0, columnspan=2,
                                               sticky="w", pady=(0, 6)); r += 1
        self._summary = ttk.Label(body, text=t("general.counting"),
                                  style="CardMuted.TLabel", justify="left")
        self._summary.grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
        # defer the (cheap but disk-touching) counts so the tab paints instantly
        outer.after(50, self._fill_summary)
        return outer

    # --- dependencies ------------------------------------------------------
    def _repo(self) -> ModRepository:
        return ModRepository(self.services.settings.current)

    def _render_deps(self) -> None:
        for w in self._deps_box.winfo_children():
            w.destroy()
        t = self.t
        deps = self.context.mod.dependencies
        installed = {m.name for m in self._repo().list_mods()}
        if not deps:
            ttk.Label(self._deps_box, text=t("general.deps.none"),
                      style="CardMuted.TLabel").pack(anchor="w", pady=(0, 4))
        for name in deps:
            row = ttk.Frame(self._deps_box, style="Card.TFrame")
            row.pack(fill="x", anchor="w", pady=1)
            ttk.Button(row, text="✕", width=2,
                       command=lambda n=name: self._remove_dep(n)).pack(side="left")
            ttk.Label(row, text=name, style="Card.TLabel").pack(side="left", padx=8)
            if name not in installed:
                ttk.Label(row, text=t("general.deps.missing"),
                          style="CardMuted.TLabel").pack(side="left", padx=4)
        ttk.Button(self._deps_box, text="＋ " + t("general.deps.add"),
                   command=self._add_deps).pack(anchor="w", pady=(6, 0))

    def _add_deps(self) -> None:
        mod = self.context.mod
        current = set(mod.dependencies)
        candidates = sorted({
            m.name for m in self._repo().list_mods()
            if m.name and m.id != mod.id and m.name != mod.name
            and m.name not in current
        })
        if not candidates:
            self._deps_error.configure(text=self.t("general.deps.no_candidates"))
            return
        self._deps_error.configure(text="")
        MultiPickDialog(self._deps_box, self, self.t("general.deps.pick"),
                        candidates, self._on_added)

    def _on_added(self, selected: list[str]) -> None:
        if selected:
            self._save_deps(self.context.mod.dependencies + list(selected))

    def _remove_dep(self, name: str) -> None:
        self._save_deps([d for d in self.context.mod.dependencies if d != name])

    def _save_deps(self, names: list[str]) -> None:
        # De-duplicate while preserving load order.
        ordered = list(dict.fromkeys(names))
        repo = self._repo()
        try:
            repo.save_dependencies(self.context.mod, ordered)
        except (OSError, FileNotFoundError):
            self._deps_error.configure(text=self.t("general.deps.save_failed"))
            return
        # Re-resolve so newly opened editor tabs layer the dependency content in.
        self.context.dependencies = repo.resolve_dependencies(self.context.mod)
        self._deps_error.configure(text=self.t("general.deps.reopen_note"))
        self._render_deps()

    def _fill_summary(self) -> None:
        t = self.t
        lines: list[str] = []
        try:
            from ...services.country_service import CountryService
            n = len(CountryService(self.context).list_tags(include_vanilla=False))
            lines.append(t("general.count.countries", count=n))
        except Exception:
            pass
        try:
            from ...services.idea_service import IdeaService
            svc = IdeaService(self.context)
            n = sum(len(v) for ref in svc.list_docs(False)
                    for v in ref.categories.values())
            lines.append(t("general.count.ideas", count=n))
        except Exception:
            pass
        try:
            from ...services.event_service import EventService
            n = sum(len(ref.events) for ref in EventService(self.context).list_docs(False))
            lines.append(t("general.count.events", count=n))
        except Exception:
            pass
        try:
            from ...services.decision_service import DecisionService
            n = sum(len(ids) for ref in DecisionService(self.context).list_docs(False, "decisions")
                    for ids in ref.categories.values())
            lines.append(t("general.count.decisions", count=n))
        except Exception:
            pass
        self._summary.configure(text="\n".join(lines) or t("common.none"))
