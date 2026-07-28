"""Bookmarks editor — the country-selection screen, laid out visually.

A bookmark decides which countries the player can pick at game start: the ones
without ``minor = yes`` are the big portraits on top, the rest sit in the list
below, and the file order is the display order. Editing that by hand means
counting braces in a 2000-line file, so this editor shows the two rows as
draggable cards: drag inside a lane to reorder, drag across to promote a minor
into the featured row (or demote it), and edit the selected country's ideology,
description key and preview focuses on the right.

Files are read across the layers and written only into the edited mod — picking a
vanilla bookmark and saving copies it into the mod first.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ...services.bookmark_service import (
    BookmarkCountryEntry,
    BookmarkDocument,
    BookmarkEntry,
    BookmarkService,
)
from ..base import EditorModule, EditorRegistry
from ..common import TextPromptDialog

_CARD_W = 92
_CARD_H = 62
_GAP = 8
_LANE_PAD = 26


@EditorRegistry.register
class BookmarksEditor(EditorModule):
    id = "bookmarks"
    name_key = "editors.bookmarks.name"
    desc_key = "editors.bookmarks.desc"
    order = 55

    def __init__(self, context, services):
        super().__init__(context, services)
        self.service = BookmarkService(context)
        self._doc: BookmarkDocument | None = None
        self._entry: BookmarkEntry | None = None
        self._selected: str = ""      # uid (tag#index)
        self._cards: dict[str, dict] = {}      # tag -> {id, lane, index}
        self._drag: dict | None = None
        self._dirty = False

    # ------------------------------------------------------------------ build
    def build(self, parent) -> ttk.Widget:
        root = ttk.Frame(parent, style="TFrame")
        root.columnconfigure(0, weight=3)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)

        bar = ttk.Frame(root, style="TFrame")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(bar, text=self.t("bookmarks.file"), style="Muted.TLabel").pack(side="left")
        self._file_var = tk.StringVar()
        self._file_combo = ttk.Combobox(bar, textvariable=self._file_var,
                                        state="readonly", width=34)
        self._file_combo.pack(side="left", padx=6)
        self._file_combo.bind("<<ComboboxSelected>>", lambda e: self._load_selected_file())

        ttk.Label(bar, text=self.t("bookmarks.scenario"),
                  style="Muted.TLabel").pack(side="left", padx=(12, 0))
        self._bm_var = tk.StringVar()
        self._bm_combo = ttk.Combobox(bar, textvariable=self._bm_var,
                                      state="readonly", width=28)
        self._bm_combo.pack(side="left", padx=6)
        self._bm_combo.bind("<<ComboboxSelected>>", lambda e: self._show_bookmark())

        ttk.Button(bar, text="➕ " + self.t("bookmarks.add_country"),
                   command=self._add_country).pack(side="left", padx=(12, 2))
        ttk.Button(bar, text="🗑 " + self.t("bookmarks.remove_country"),
                   command=self._remove_country).pack(side="left", padx=2)
        ttk.Button(bar, text="💾 " + self.t("common.save"), style="Accent.TButton",
                   command=self._save).pack(side="right")

        board = ttk.Frame(root, style="Card.TFrame", padding=8)
        board.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        board.rowconfigure(0, weight=1)
        board.columnconfigure(0, weight=1)
        self._canvas = tk.Canvas(board, bg=self.palette.surface, highlightthickness=0)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(board, orient="vertical", command=self._canvas.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self._canvas.configure(yscrollcommand=sb.set)
        self._canvas.bind("<Button-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self._canvas.bind("<Configure>", lambda e: self._redraw())

        self._status = ttk.Label(root, text="", style="Muted.TLabel")
        self._status.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self._build_inspector(root)
        self._reload_files()
        return root

    def _build_inspector(self, root) -> None:
        box = ttk.Frame(root, style="Card.TFrame", padding=12)
        box.grid(row=1, column=1, sticky="nsew")
        box.columnconfigure(1, weight=1)
        r = 0
        self._insp_title = ttk.Label(box, text=self.t("bookmarks.inspector.empty"),
                                     style="CardTitle.TLabel")
        self._insp_title.grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 10)); r += 1

        def row(label_key: str) -> tk.StringVar:
            nonlocal r
            ttk.Label(box, text=self.t(label_key), style="CardMuted.TLabel").grid(
                row=r, column=0, sticky="w", pady=3)
            var = tk.StringVar()
            ttk.Entry(box, textvariable=var).grid(row=r, column=1, sticky="ew",
                                                  padx=(8, 0), pady=3)
            r += 1
            return var

        self._v_ideology = row("bookmarks.ideology")
        self._v_history = row("bookmarks.history")
        self._v_focuses = row("bookmarks.focuses")
        self._v_ideas = row("bookmarks.ideas")

        self._v_featured = tk.BooleanVar()
        ttk.Checkbutton(box, text=self.t("bookmarks.featured"),
                        variable=self._v_featured,
                        command=self._toggle_featured).grid(row=r, column=0,
                                                            columnspan=2,
                                                            sticky="w", pady=(8, 0))
        r += 1
        ttk.Button(box, text=self.t("bookmarks.apply"), style="Accent.TButton",
                   command=self._apply_inspector).grid(row=r, column=0, columnspan=2,
                                                       sticky="ew", pady=(12, 0))
        r += 1
        ttk.Label(box, text=self.t("bookmarks.hint"), style="CardMuted.TLabel",
                  wraplength=250, justify="left").grid(row=r, column=0, columnspan=2,
                                                       sticky="w", pady=(14, 0))

    # ------------------------------------------------------------------ data
    def _reload_files(self) -> None:
        self._refs = self.service.list_docs(True)
        labels = [f"{r.name}{'' if not r.is_vanilla else '  (' + self.t('bookmarks.vanilla') + ')'}"
                  for r in self._refs]
        self._file_combo.configure(values=labels)
        if labels:
            self._file_combo.current(0)
            self._load_selected_file()

    def _load_selected_file(self) -> None:
        idx = self._file_combo.current()
        if idx < 0 or idx >= len(self._refs):
            return
        try:
            self._doc = self.service.load(self._refs[idx])
        except Exception as exc:
            self._fail(str(exc))
            return
        self._dirty = False
        names = [b.name or self.t("bookmarks.unnamed") for b in self._doc.bookmarks]
        self._bm_combo.configure(values=names)
        if names:
            self._bm_combo.current(0)
        self._show_bookmark()

    def _show_bookmark(self) -> None:
        if self._doc is None:
            return
        idx = max(0, self._bm_combo.current())
        self._entry = (self._doc.bookmarks[idx]
                       if idx < len(self._doc.bookmarks) else None)
        self._selected = ""
        self._redraw()
        self._fill_inspector()

    def _countries(self) -> list[BookmarkCountryEntry]:
        return self._entry.countries() if self._entry is not None else []

    def _find(self, uid: str) -> BookmarkCountryEntry | None:
        return next((c for c in self._countries() if c.uid == uid), None)

    # ------------------------------------------------------------------ draw
    def _redraw(self) -> None:
        c = self._canvas
        c.delete("all")
        self._cards.clear()
        if self._entry is None:
            return
        width = max(c.winfo_width(), 400)
        per_row = max(1, (width - 2 * _LANE_PAD) // (_CARD_W + _GAP))

        featured = [x for x in self._countries() if not x.minor]
        minors = [x for x in self._countries() if x.minor]
        y = 10
        y = self._draw_lane(self.t("bookmarks.lane.featured"), featured, "featured",
                            y, per_row)
        y = self._draw_lane(self.t("bookmarks.lane.minor"), minors, "minor",
                            y + 14, per_row)
        c.configure(scrollregion=(0, 0, width, y + 20))

    def _draw_lane(self, title: str, entries, lane: str, y: int, per_row: int) -> int:
        c = self._canvas
        c.create_text(_LANE_PAD, y, text=f"{title}  ({len(entries)})", anchor="w",
                      fill=self.palette.text, font=("", 10, "bold"))
        y += 20
        rows = max(1, (len(entries) + per_row - 1) // per_row)
        c.create_rectangle(_LANE_PAD - 8, y - 6,
                           _LANE_PAD + per_row * (_CARD_W + _GAP),
                           y + rows * (_CARD_H + _GAP) + 2,
                           outline=self.palette.border, width=1,
                           tags=(f"lane::{lane}",))
        for i, entry in enumerate(entries):
            col, row = i % per_row, i // per_row
            x = _LANE_PAD + col * (_CARD_W + _GAP)
            yy = y + row * (_CARD_H + _GAP)
            self._draw_card(entry, x, yy, lane, i)
        return y + rows * (_CARD_H + _GAP) + 10

    def _draw_card(self, entry: BookmarkCountryEntry, x: int, y: int,
                   lane: str, index: int) -> None:
        c = self._canvas
        selected = entry.uid == self._selected
        fill = self.palette.accent if selected else self.palette.surface_alt
        text_col = self.palette.accent_text if selected else self.palette.text
        rect = c.create_rectangle(x, y, x + _CARD_W, y + _CARD_H,
                                  fill=fill, outline=self.palette.border,
                                  width=2 if selected else 1,
                                  tags=("card", f"uid::{entry.uid}"))
        c.create_text(x + _CARD_W / 2, y + _CARD_H / 2 - 8, text=entry.tag,
                      fill=text_col, font=("", 13, "bold"),
                      tags=("card", f"uid::{entry.uid}"))
        sub = entry.ideology or "—"
        c.create_text(x + _CARD_W / 2, y + _CARD_H / 2 + 12, text=sub[:12],
                      fill=text_col, font=("", 8),
                      tags=("card", f"uid::{entry.uid}"))
        self._cards[entry.uid] = {"id": rect, "lane": lane, "index": index,
                                  "x": x, "y": y}

    # ------------------------------------------------------------------ drag
    def _tag_at(self, x: int, y: int) -> str:
        for item in self._canvas.find_overlapping(x, y, x, y):
            for tag in self._canvas.gettags(item):
                if tag.startswith("uid::"):
                    return tag.split("::", 1)[1]
        return ""

    def _on_press(self, event) -> None:
        x = self._canvas.canvasx(event.x)
        y = self._canvas.canvasy(event.y)
        tag = self._tag_at(x, y)
        if not tag:
            return
        self._selected = tag
        self._drag = {"tag": tag, "x": x, "y": y, "moved": False}
        self._fill_inspector()
        self._redraw()

    def _on_drag(self, event) -> None:
        if not self._drag:
            return
        x = self._canvas.canvasx(event.x)
        y = self._canvas.canvasy(event.y)
        if abs(x - self._drag["x"]) + abs(y - self._drag["y"]) > 6:
            self._drag["moved"] = True
            self._canvas.configure(cursor="fleur")

    def _on_release(self, event) -> None:
        self._canvas.configure(cursor="")
        drag = self._drag
        self._drag = None
        if not drag or not drag["moved"] or self._entry is None:
            return
        x = self._canvas.canvasx(event.x)
        y = self._canvas.canvasy(event.y)
        moved = self._find(drag["tag"])
        if moved is None:
            return

        target_lane = self._lane_at(y)
        featured = [c.uid for c in self._countries() if not c.minor]
        minors = [c.uid for c in self._countries() if c.minor]
        source = featured if not moved.minor else minors
        if moved.uid in source:
            source.remove(moved.uid)
        target = featured if target_lane == "featured" else minors
        target.insert(self._drop_index(x, y, target), moved.uid)
        moved.set_minor(target_lane != "featured")

        self._entry.set_entry_order(featured + minors)
        self._dirty = True
        self._redraw()
        self._fill_inspector()
        self._status.configure(text=self.t("bookmarks.moved", tag=moved.tag),
                               foreground=self.palette.text_muted)

    def _lane_at(self, y: float) -> str:
        """Which lane a drop y-coordinate falls into (nearest lane wins)."""
        best, best_dist = "featured", None
        for lane in ("featured", "minor"):
            items = self._canvas.find_withtag(f"lane::{lane}")
            if not items:
                continue
            x0, y0, x1, y1 = self._canvas.coords(items[0])
            if y0 <= y <= y1:
                return lane
            dist = min(abs(y - y0), abs(y - y1))
            if best_dist is None or dist < best_dist:
                best, best_dist = lane, dist
        return best

    def _drop_index(self, x: float, y: float, lane_uids: list[str]) -> int:
        """Insertion index from the drop position, by nearest card centre."""
        best_idx, best_dist = len(lane_uids), None
        for i, tag in enumerate(lane_uids):
            card = self._cards.get(tag)
            if not card:
                continue
            cx = card["x"] + _CARD_W / 2
            cy = card["y"] + _CARD_H / 2
            dist = abs(x - cx) + abs(y - cy) * 2
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best_idx = i if x < cx else i + 1
        return max(0, min(best_idx, len(lane_uids)))

    # ------------------------------------------------------------- inspector
    def _fill_inspector(self) -> None:
        entry = self._find(self._selected) if self._selected else None
        if entry is None:
            self._insp_title.configure(text=self.t("bookmarks.inspector.empty"))
            for var in (self._v_ideology, self._v_history,
                        self._v_focuses, self._v_ideas):
                var.set("")
            self._v_featured.set(False)
            return
        self._insp_title.configure(text=entry.tag)
        self._v_ideology.set(entry.ideology)
        self._v_history.set(entry.history)
        self._v_focuses.set(" ".join(entry.focuses))
        self._v_ideas.set(" ".join(entry.ideas))
        self._v_featured.set(not entry.minor)

    def _apply_inspector(self) -> None:
        entry = self._find(self._selected) if self._selected else None
        if entry is None:
            return
        entry.set_ideology(self._v_ideology.get().strip())
        entry.set_history(self._v_history.get().strip())
        entry.set_focuses(self._v_focuses.get().split())
        entry.set_ideas(self._v_ideas.get().split())
        self._dirty = True
        self._redraw()
        self._status.configure(text=self.t("bookmarks.applied", tag=entry.tag),
                               foreground=self.palette.text_muted)

    def _toggle_featured(self) -> None:
        entry = self._find(self._selected) if self._selected else None
        if entry is None or self._entry is None:
            return
        entry.set_minor(not self._v_featured.get())
        featured = [c.uid for c in self._countries() if not c.minor]
        minors = [c.uid for c in self._countries() if c.minor]
        self._entry.set_entry_order(featured + minors)
        self._dirty = True
        self._redraw()

    # ---------------------------------------------------------------- actions
    def _add_country(self) -> None:
        if self._entry is None:
            return

        def submit(tag: str) -> None:
            tag = tag.strip().upper()
            if len(tag) != 3 or not tag.isalnum():
                self._fail(self.t("bookmarks.err.tag"))
                return
            added = self._entry.add_country(tag, minor=True)
            self._selected = added.uid
            self._dirty = True
            self._redraw()
            self._fill_inspector()

        TextPromptDialog(self._canvas, self, self.t("bookmarks.add_country"),
                         "", submit)

    def _remove_country(self) -> None:
        if self._entry is None or not self._selected:
            return
        entry = self._find(self._selected)
        if entry is None:
            return
        if not messagebox.askyesno("ANKA", self.t("bookmarks.confirm_remove",
                                                  tag=entry.tag)):
            return
        self._entry.remove_entry(entry)
        self._selected = ""
        self._dirty = True
        self._redraw()
        self._fill_inspector()

    def _save(self) -> None:
        if self._doc is None:
            return
        try:
            path = self.service.save_doc(self._doc)
        except Exception as exc:
            self._fail(str(exc))
            return
        self._dirty = False
        self._reload_files()
        self._status.configure(text=self.t("bookmarks.saved", path=str(path)),
                               foreground=self.palette.text_muted)

    def on_leave(self) -> None:
        if self._dirty and self._doc is not None:
            try:
                self.service.save_doc(self._doc)
            except Exception:
                pass

    def _fail(self, msg: str) -> None:
        self._status.configure(text=msg, foreground=self.palette.danger)
