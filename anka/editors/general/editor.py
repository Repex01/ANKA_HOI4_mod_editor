"""General editor: the default landing tab for a mod.

Shows the mod's descriptor metadata and a quick content summary (how many
countries, ideas, events, ... the mod defines) so the user has an at-a-glance
overview before diving into a specific editor. Read-only; cheap counts come from
each service's lightweight listing.
"""
from __future__ import annotations

from tkinter import ttk

from ...ui.widgets import ScrollableFrame
from ..base import EditorModule, EditorRegistry


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
