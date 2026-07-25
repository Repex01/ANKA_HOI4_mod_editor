"""Minimize-safety for modal dialogs.

On Windows, a ``transient`` Toplevel holding a ``grab_set()`` breaks the main
window's taskbar restore: minimizing the parent withdraws the owned dialog,
and clicking the taskbar button then tries to activate that hidden, grabbing
popup — the app appears dead / never comes back.

`guard_modal` binds the parent's <Unmap>/<Map>: while the parent is iconified
the dialog releases its grab and hides; on restore it reappears on top and
re-grabs. Nested dialogs each install their own guard — <Map> handlers fire
in creation order, so the innermost dialog grabs last and modality stacking
is preserved.
"""
from __future__ import annotations

import tkinter as tk


def guard_modal(dialog: tk.Toplevel, top: tk.Misc | None = None) -> None:
    """Call right after ``transient(top)`` + ``grab_set()`` on `dialog`."""
    if top is None:
        top = dialog.master.winfo_toplevel()

    def on_unmap(event) -> None:
        # The window manager hides transient children on its own — don't
        # withdraw() here (racing the WM leaves the dialog stuck withdrawn);
        # dropping the grab is what keeps the taskbar restore alive. Release
        # the ACTUAL grab owner: with nested dialogs the innermost one holds
        # it, and its own guard may see the minimize event later (its parent's
        # <Unmap> can lag behind the root's).
        if event.widget is not top:
            return
        try:
            current = dialog.grab_current()
            if current is not None:
                current.grab_release()
        except tk.TclError:
            pass

    def on_map(event) -> None:
        if event.widget is not top:
            return

        def restore() -> None:
            try:
                if not dialog.winfo_exists():
                    return
                dialog.deiconify()
                dialog.lift()
                dialog.focus_set()
                dialog.grab_set()
            except tk.TclError:
                pass

        # Deferred: let the WM finish re-showing the window family first,
        # then re-assert visibility + modality on top of the settled state.
        try:
            dialog.after(60, restore)
        except tk.TclError:
            pass

    unmap_id = top.bind("<Unmap>", on_unmap, add="+")
    map_id = top.bind("<Map>", on_map, add="+")

    def cleanup(event) -> None:
        if event.widget is not dialog:
            return
        for seq, fid in (("<Unmap>", unmap_id), ("<Map>", map_id)):
            _unbind_one(top, seq, fid)

    dialog.bind("<Destroy>", cleanup, add="+")


def _unbind_one(widget: tk.Misc, seq: str, funcid: str) -> None:
    """Remove ONE handler from a bind sequence. `Misc.unbind(seq, funcid)`
    wipes the whole binding script — including other dialogs' guards added
    with ``add="+"`` — so the script is filtered line-by-line instead."""
    try:
        script = widget.bind(seq)
        kept = "\n".join(line for line in script.split("\n")
                         if funcid not in line)
        widget.tk.call("bind", widget._w, seq, kept)
        widget.deletecommand(funcid)
    except tk.TclError:
        pass


def fit_to_content(dialog: tk.Toplevel, top: tk.Misc, base_size) -> None:
    """Grow a modal so UI-scaled content is never clipped, then re-center.

    Call at the end of a dialog's __init__ (after its widgets are built). The
    window is sized to the larger of its hardcoded size and the content's
    requested size, clamped to the screen so it can never exceed the display.
    """
    def _fit() -> None:
        try:
            dialog.update_idletasks()
            base_w, base_h = base_size
            w = max(base_w, dialog.winfo_reqwidth())
            h = max(base_h, dialog.winfo_reqheight())
            w = min(w, int(dialog.winfo_screenwidth() * 0.95))
            h = min(h, int(dialog.winfo_screenheight() * 0.92))
            x = top.winfo_rootx() + (top.winfo_width() - w) // 2
            y = top.winfo_rooty() + (top.winfo_height() - h) // 2
            dialog.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass
    try:
        dialog.after_idle(_fit)
    except tk.TclError:
        pass
