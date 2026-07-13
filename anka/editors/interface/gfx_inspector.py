"""Schema-driven inspector for one ``.gfx`` sprite entry.

The form is rebuilt on every ``show()`` from the sprite kind's `AttrSpec`
table, so every parameter of every kind is editable without per-kind UI code.
Unknown keys are listed and preserved; ``animation = { ... }`` blocks get a
small list UI backed by the script dialog. The texture preview shows the whole
strip with frame separators when ``noOfFrames > 1``.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

from ...config.constants import (
    DECISION_ICON_SIZE,
    EVENT_PICTURE_SIZE,
    FOCUS_ICON_SIZE,
    IDEA_ICON_SIZE,
    LEADER_PORTRAIT_SIZE,
    SUPPORTED_IMPORT_FORMATS,
)
from ...core.images.converter import ImageConverter
from ...core.pdx import Block, dumps
from ...core.pdx import parse as pdx_parse
from ...core.guitypes.schema import AttrKind, AttrSpec
from ..common import InspectorBase, PdxPreviewDialog, ScriptEditorDialog

_PREVIEW_MAX = (400, 180)

# Named target sizes a texture can be fitted to (loc-key suffix, (w, h)).
# Mirrors the canonical asset dimensions in config.constants used by IconService.
_RESIZE_PRESETS: tuple[tuple[str, tuple[int, int]], ...] = (
    ("focus_icon", FOCUS_ICON_SIZE),
    ("idea_icon", IDEA_ICON_SIZE),
    ("decision_icon", DECISION_ICON_SIZE),
    ("leader_portrait", LEADER_PORTRAIT_SIZE),
    ("event_picture", EVENT_PICTURE_SIZE),
)


class SpriteInspector(InspectorBase):
    def __init__(self, master, owner):
        super().__init__(master, owner)
        self.doc = None
        self.view = None
        self._preview_photo: ImageTk.PhotoImage | None = None

    # -------------------------------------------------------------------- show
    def show(self, doc, view, editable: bool) -> None:
        self.flush_pending()
        self._loading = True
        self.doc, self.view, self._editable = doc, view, editable
        for child in self.body.winfo_children():
            child.destroy()
        if view is not None:
            self._build_form()
            self._set_state_all(editable)
        self._loading = False

    def _build_form(self) -> None:
        view, b = self.view, self.body
        r = 0
        title = ttk.Label(b, text=(view.name or view.type_key)
                          + ("  🔒" if not self._editable else ""),
                          style="Heading.TLabel")
        title.grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1
        ttk.Label(b, text=f"{view.type_key} · {self.doc.ref.rel_file}",
                  style="CardMuted.TLabel", wraplength=430).grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(0, 6))
        r += 1

        self._preview_label = ttk.Label(b, style="Card.TLabel")
        self._preview_label.grid(row=r, column=0, columnspan=2, pady=(0, 2))
        r += 1
        self._preview_info = ttk.Label(b, text="", style="CardMuted.TLabel")
        self._preview_info.grid(row=r, column=0, columnspan=2, pady=(0, 8))
        r += 1
        self._refresh_preview()

        if view.spec is not None:
            for attr in view.spec.attrs:
                r = self._attr_row(r, attr)
        extras = self._unknown_keys()
        if extras:
            ttk.Label(b, text=self.t("interface.other_attrs") + ": "
                      + ", ".join(extras),
                      style="CardMuted.TLabel", wraplength=430,
                      justify="left").grid(row=r, column=0, columnspan=2,
                                           sticky="w", pady=(6, 0))
            r += 1
        if view.spec is not None and view.spec.has_animations:
            r = self._animations_section(r)

        ttk.Separator(b).grid(row=r, column=0, columnspan=2, sticky="ew", pady=8)
        r += 1
        actions = ttk.Frame(b, style="Card.TFrame")
        actions.grid(row=r, column=0, columnspan=2, sticky="ew")
        ttk.Button(actions, text="👁 " + self.t("focuses.preview"),
                   command=self._preview_pdx).pack(side="left")
        self._delete_btn = ttk.Button(
            actions, text="🗑 " + self.t("interface.gfx.delete"),
            command=self._delete)
        self._delete_btn.pack(side="right")

    # ------------------------------------------------------------- schema rows
    def _attr_row(self, row: int, attr: AttrSpec) -> int:
        view = self.view
        kind = attr.kind
        label = attr.name
        b = self.body

        def commit_scalar(var: tk.StringVar, name: str = attr.name,
                          refresh: bool = False) -> None:
            if not self._guard():
                return
            view.set_attr(name, var.get())
            self._mark_dirty()
            if refresh:
                self._refresh_preview()

        if kind in (AttrKind.SIZE, AttrKind.POSITION):
            ttk.Label(b, text=label, style="CardMuted.TLabel").grid(
                row=row, column=0, sticky="w", pady=3)
            cell = ttk.Frame(b, style="Card.TFrame")
            cell.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
            x_var, y_var = tk.StringVar(), tk.StringVar()
            x0, y0 = view.get_xy(attr.name)
            x_var.set(x0)
            y_var.set(y0)

            def commit_xy(name: str = attr.name) -> None:
                if not self._guard():
                    return
                view.set_xy(name, x_var.get(), y_var.get())
                self._mark_dirty()

            for var, tag in ((x_var, "x"), (y_var, "y")):
                ttk.Label(cell, text=tag, style="CardMuted.TLabel").pack(
                    side="left", padx=(0 if tag == "x" else 8, 2))
                e = ttk.Entry(cell, textvariable=var, width=7)
                e.pack(side="left")
                var.trace_add("write",
                              lambda *_a, k=attr.name: self._debounce(k, commit_xy))
                e.bind("<FocusOut>", lambda _e: commit_xy())
            return row + 1

        if kind in (AttrKind.COLOR, AttrKind.SCRIPT):
            current = view.get_script(attr.name)
            var = tk.StringVar(value=current)

            def commit_script(name: str = attr.name, v: tk.StringVar = None) -> None:
                if not self._guard():
                    return
                try:
                    view.set_script(name, var.get())
                except Exception:
                    return
                self._mark_dirty()

            ttk.Label(b, text=label, style="CardMuted.TLabel").grid(
                row=row, column=0, sticky="w", pady=3)
            e = ttk.Entry(b, textvariable=var, width=26)
            e.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
            var.trace_add("write",
                          lambda *_a, k=attr.name: self._debounce(k, commit_script))
            e.bind("<FocusOut>", lambda _e: commit_script())
            return row + 1

        current = view.get_attr(attr.name)
        var = tk.StringVar(value=current)
        ttk.Label(b, text=label, style="CardMuted.TLabel").grid(
            row=row, column=0, sticky="w", pady=3)

        if kind == AttrKind.BOOL:
            box = ttk.Combobox(b, textvariable=var, width=8, state="readonly",
                               values=("", "yes", "no"))
            box.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
            box.bind("<<ComboboxSelected>>", lambda _e: commit_scalar(var))
        elif kind == AttrKind.ENUM:
            box = ttk.Combobox(b, textvariable=var, width=18, state="readonly",
                               values=("",) + attr.enum_values)
            box.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
            box.bind("<<ComboboxSelected>>", lambda _e: commit_scalar(var))
        elif kind == AttrKind.TEXTURE:
            cell = ttk.Frame(b, style="Card.TFrame")
            cell.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=3)
            cell.columnconfigure(0, weight=1)
            e = ttk.Entry(cell, textvariable=var, width=30)
            e.grid(row=0, column=0, sticky="ew")
            ttk.Button(cell, text="⇪", width=3,
                       command=lambda n=attr.name, v=var: self._import_texture(n, v)
                       ).grid(row=0, column=1, padx=(4, 0))
            # "Resize to" appears on the next row once a file is set.
            sync_resize = self._resize_row(row + 1, attr.name, var)

            def commit_texture(v: tk.StringVar = var, k: str = attr.name) -> None:
                commit_scalar(v, k, True)
                sync_resize()

            var.trace_add("write", lambda *_a, k=attr.name, v=var: (
                sync_resize(), self._debounce(k, lambda: commit_scalar(v, k, True))))
            e.bind("<FocusOut>", lambda _e: commit_texture())
            return row + 2
        else:
            width = 8 if kind in (AttrKind.INT, AttrKind.FLOAT) else 26
            refresh = attr.name.lower() == "noofframes"
            e = ttk.Entry(b, textvariable=var, width=width)
            e.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
            var.trace_add("write", lambda *_a, k=attr.name, rf=refresh:
                          self._debounce(k, lambda: commit_scalar(var, k, rf)))
            e.bind("<FocusOut>",
                   lambda _e, rf=refresh: commit_scalar(var, attr.name, rf))
        return row + 1

    # ------------------------------------------------------------- animations
    def _animations_section(self, row: int) -> int:
        b = self.body
        ttk.Separator(b).grid(row=row, column=0, columnspan=2, sticky="ew",
                              pady=(8, 4))
        row += 1
        head = ttk.Frame(b, style="Card.TFrame")
        head.grid(row=row, column=0, columnspan=2, sticky="ew")
        ttk.Label(head, text=self.t("interface.gfx.animations"),
                  style="CardMuted.TLabel").pack(side="left")
        ttk.Button(head, text="➕", width=3,
                   command=self._add_animation).pack(side="right")
        row += 1
        for i, anim in enumerate(self.view.animations()):
            line = ttk.Frame(b, style="Card.TFrame")
            line.grid(row=row, column=0, columnspan=2, sticky="ew", pady=1)
            kind = anim.get_scalar_ci("animationtype") or "?"
            ttk.Label(line, text=f"animation #{i + 1} · {kind}",
                      style="CardMuted.TLabel").pack(side="left")
            ttk.Button(line, text="🗑", width=3,
                       command=lambda a=anim: self._remove_animation(a)).pack(
                side="right")
            ttk.Button(line, text="✎", width=3,
                       command=lambda a=anim, n=i: self._edit_animation(a, n)).pack(
                side="right", padx=(0, 2))
            row += 1
        return row

    def _add_animation(self) -> None:
        if not self._editable or self.view is None:
            return
        self.view.add_animation()
        self._mark_dirty()
        self.show(self.doc, self.view, self._editable)

    def _remove_animation(self, anim: Block) -> None:
        if not self._editable or self.view is None:
            return
        self.view.remove_animation(anim)
        self._mark_dirty()
        self.show(self.doc, self.view, self._editable)

    def _edit_animation(self, anim: Block, index: int) -> None:
        text = dumps(anim, top_level=False)

        def submitted(new_text: str) -> None:
            try:
                parsed = pdx_parse(new_text, recover=False)
            except Exception:
                return
            anim.items = parsed.items
            self._mark_dirty()
            self.show(self.doc, self.view, self._editable)

        ScriptEditorDialog(self, self.owner, f"animation #{index + 1}", text,
                           submitted if self._editable else (lambda t: None),
                           ("effect", "trigger"),
                           self.view.name if self.view else "")

    # ---------------------------------------------------------------- preview
    def _texture_path(self):
        view, doc = self.view, self.doc
        if view is None or doc is None:
            return None
        tex = view.texture
        if not tex:
            return None
        rel = tex.replace("\\", "/").replace("//", "/")
        ctx = self.owner.context
        roots = [doc.ref.source_root, ctx.mod.path, *ctx.dependency_paths,
                 ctx.game_path]
        for root in roots:
            path = root / rel
            if path.exists():
                return path
        return None

    def _refresh_preview(self) -> None:
        path = self._texture_path()
        img = None
        if path is not None:
            try:
                with Image.open(path) as im:
                    img = im.convert("RGBA")
            except Exception:
                img = None
        if img is None:
            self._preview_photo = None
            self._preview_label.configure(image="")
            self._preview_info.configure(
                text=self.t("interface.gfx.no_texture"))
            return
        w, h = img.size
        frames = self.view.no_of_frames()
        info = f"{w}×{h}"
        if frames > 1:
            fw = w // frames
            info += f" · {frames} × {fw}×{h}"
            draw = ImageDraw.Draw(img)
            for i in range(1, frames):
                draw.line([(i * fw, 0), (i * fw, h)], fill=(255, 64, 64, 200))
        scale = min(1.0, _PREVIEW_MAX[0] / w, _PREVIEW_MAX[1] / h)
        if scale < 1.0:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                             Image.LANCZOS)
        self._preview_photo = ImageTk.PhotoImage(img)
        self._preview_label.configure(image=self._preview_photo)
        self._preview_info.configure(text=info)

    def _import_texture(self, attr_name: str, var: tk.StringVar) -> None:
        if not self._editable:
            return
        patterns = " ".join(f"*{ext}" for ext in SUPPORTED_IMPORT_FORMATS)
        src = filedialog.askopenfilename(
            title=self.t("interface.gfx.import_texture"),
            filetypes=[("Images", patterns), ("All files", "*.*")])
        if not src:
            return
        source = Path(src)
        current = (var.get() or "").replace("\\", "/")
        if current and current.lower().endswith((".dds", ".tga", ".png")):
            dest_rel = str(Path(current).parent / (source.stem + ".dds"))
        else:
            dest_rel = f"gfx/interface/{source.stem}.dds"
        dest = self.owner.context.mod.path / dest_rel
        try:
            ImageConverter.convert(source, dest)
        except Exception as exc:                          # noqa: BLE001
            messagebox.showerror("ANKA", str(exc))
            return
        var.set(Path(dest_rel).as_posix())
        self.view.set_attr(attr_name, Path(dest_rel).as_posix())
        self._mark_dirty()
        if self.view.name:
            self.owner.resolver.add(self.view.name, dest)
        self._refresh_preview()

    # ------------------------------------------------------------------- resize
    def _resize_row(self, row: int, attr_name: str, tex_var: tk.StringVar):
        """Build the "Resize to" menu for a texture attr and return a callback
        that shows/hides it depending on whether a file is set. Picking a preset
        resizes the referenced image on disk; "Custom" reveals width/height fields."""
        b = self.body
        frame = ttk.Frame(b, style="Card.TFrame")
        frame.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=(0, 3))

        ttk.Label(frame, text=self.t("interface.gfx.resize_to"),
                  style="CardMuted.TLabel").grid(row=0, column=0, sticky="w")
        labels = [self.t("interface.gfx.resize.none")]
        labels += [f"{self.t('interface.gfx.resize.' + key)}  ·  {w}×{h}"
                   for key, (w, h) in _RESIZE_PRESETS]
        labels.append(self.t("interface.gfx.resize.custom"))
        box = ttk.Combobox(frame, width=24, state="readonly", values=tuple(labels))
        box.current(0)
        box.grid(row=0, column=1, sticky="w", padx=(8, 0))

        custom = ttk.Frame(frame, style="Card.TFrame")
        custom.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        w_var, h_var = tk.StringVar(), tk.StringVar()
        ttk.Label(custom, text=self.t("interface.gfx.resize.width"),
                  style="CardMuted.TLabel").pack(side="left")
        ttk.Entry(custom, textvariable=w_var, width=6).pack(side="left", padx=(3, 8))
        ttk.Label(custom, text=self.t("interface.gfx.resize.height"),
                  style="CardMuted.TLabel").pack(side="left")
        ttk.Entry(custom, textvariable=h_var, width=6).pack(side="left", padx=(3, 8))
        ttk.Button(custom, text=self.t("interface.gfx.resize.apply"),
                   command=lambda: self._resize_custom(attr_name, tex_var,
                                                       w_var, h_var)).pack(side="left")
        custom.grid_remove()

        def on_select(_e=None) -> None:
            idx = box.current()
            if idx == len(labels) - 1:            # Custom
                custom.grid()
                return
            custom.grid_remove()
            if idx > 0:                           # a named preset
                self._resize_texture(attr_name, tex_var, _RESIZE_PRESETS[idx - 1][1])
                box.current(0)                    # it's an action, reset to None
        box.bind("<<ComboboxSelected>>", on_select)

        def sync() -> None:
            if (tex_var.get() or "").strip():
                frame.grid()
            else:
                box.current(0)
                custom.grid_remove()
                frame.grid_remove()
        sync()
        return sync

    def _resize_custom(self, attr_name: str, tex_var: tk.StringVar,
                       w_var: tk.StringVar, h_var: tk.StringVar) -> None:
        try:
            w, h = int(float(w_var.get())), int(float(h_var.get()))
        except (ValueError, TypeError):
            w = h = 0
        if w <= 0 or h <= 0:
            messagebox.showerror("ANKA", self.t("interface.gfx.resize.bad_size"))
            return
        self._resize_texture(attr_name, tex_var, (w, h))

    def _resize_texture(self, attr_name: str, tex_var: tk.StringVar,
                        size: tuple[int, int]) -> None:
        """Resize the texture referenced by ``attr_name`` to ``size`` in place,
        writing the result into the mod. Game/dependency files are read as the
        source but never overwritten — the resized copy lands in the mod."""
        if not self._editable:
            return
        rel = (tex_var.get() or "").strip().replace("\\", "/")
        if not rel:
            return
        dest = self.owner.context.mod.path / rel
        src = self._resolve_texture(rel) or (dest if dest.exists() else None)
        if src is None:
            messagebox.showerror("ANKA", self.t("interface.gfx.resize.missing"))
            return
        try:
            ImageConverter.convert(src, dest, size=size)
        except Exception as exc:                          # noqa: BLE001
            messagebox.showerror("ANKA", str(exc))
            return
        if self.view is not None and self.view.name:
            self.owner.resolver.add(self.view.name, dest)
        self._refresh_preview()

    def _resolve_texture(self, rel: str) -> Path | None:
        """First existing file for ``rel`` across the content roots (mod first)."""
        if self.doc is None:
            return None
        ctx = self.owner.context
        for root in (ctx.mod.path, self.doc.ref.source_root,
                     *ctx.dependency_paths, ctx.game_path):
            path = root / rel
            if path.exists():
                return path
        return None

    # ----------------------------------------------------------------- actions
    def _unknown_keys(self) -> list[str]:
        view = self.view
        if view.spec is None:
            return []
        known = {a.name.lower() for a in view.spec.attrs}
        known.add("animation")
        out: list[str] = []
        for pair in view.block.pairs():
            low = pair.key.lower()
            if low not in known and pair.key not in out:
                out.append(pair.key)
        return out

    def _mark_dirty(self) -> None:
        self.owner.mark_dirty(self.doc)

    def _preview_pdx(self) -> None:
        if self.view is not None:
            PdxPreviewDialog(self, self.owner, self.view.name or "sprite",
                             Block([self.view.pair]))

    def _delete(self) -> None:
        if self.view is None or not self._editable:
            return
        if not messagebox.askyesno("ANKA", self.t("interface.gfx.confirm_delete",
                                                  name=self.view.name)):
            return
        self.owner.delete_sprite(self.doc, self.view)
