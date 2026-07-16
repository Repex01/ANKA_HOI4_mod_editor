"""Break the tkinter Variable-trace leak when a screen is torn down.

``Variable.trace_add`` registers its callback as a command on the Tcl
interpreter. The interpreter's command table is a GC root the Python collector
cannot see through, and trace callbacks close over the widgets they update — so
after a screen is destroyed the chain ``interpreter → callback → widget tree →
Variable`` keeps the whole screen (and every service it holds) alive forever:
``Variable.__del__``, the only thing that would delete the command, can never
run on a reachable object. ANKA has hundreds of ``trace_add`` call sites, so
instead of pairing each with an explicit ``trace_remove`` the app sweeps once
per screen swap: every trace whose callback references only *destroyed* widgets
is removed, which lets the ordinary GC free the screen's object graph.
"""
from __future__ import annotations

import gc
import re
import tkinter
import types

# Tcl command names minted by Variable._register: repr(id(bound __call__)) plus
# the callback's __name__ when it has one (e.g. "2068029523904<lambda>").
_CBNAME_ID = re.compile(r"^(\d+)")


def _widgets_of(func, depth: int = 0) -> list[tkinter.Misc]:
    """Widgets a callback references: bound ``self``, closure cells, and — for
    non-widget closure objects such as editor modules — their instance attributes
    (one level deep, no recursion into arbitrary graphs)."""
    widgets: list[tkinter.Misc] = []
    if func is None or depth > 3:
        return widgets
    bound_self = getattr(func, "__self__", None)
    if isinstance(bound_self, tkinter.Misc):
        widgets.append(bound_self)
    inner = getattr(func, "__func__", func)
    for cell in getattr(inner, "__closure__", None) or ():
        try:
            value = cell.cell_contents
        except ValueError:                      # empty cell
            continue
        if isinstance(value, tkinter.Misc):
            widgets.append(value)
        elif isinstance(value, (types.FunctionType, types.MethodType)):
            widgets.extend(_widgets_of(value, depth + 1))
        elif hasattr(value, "__dict__"):        # e.g. an EditorModule holding widgets
            widgets.extend(v for v in vars(value).values()
                           if isinstance(v, tkinter.Misc))
    return widgets


def _is_dead(widget: tkinter.Misc) -> bool:
    try:
        return not int(widget.tk.call("winfo", "exists", widget._w))
    except tkinter.TclError:
        return True


def purge_orphan_variable_traces() -> int:
    """Remove variable traces whose callbacks only reference destroyed widgets.

    Conservative on purpose: a trace is dropped only when its callback demonstrably
    references at least one widget and *every* referenced widget is destroyed —
    traces of any live screen keep working untouched. Returns the number removed.
    """
    variables: list[tkinter.Variable] = []
    methods: list[types.MethodType] = []
    for obj in gc.get_objects():
        if isinstance(obj, tkinter.Variable):
            if obj._tclCommands:
                variables.append(obj)
        elif (isinstance(obj, types.MethodType)
              and isinstance(obj.__self__, tkinter.CallWrapper)):
            methods.append(obj)

    # cbname -> (owning Variable, full command name), keyed by the id prefix.
    owners: dict[int, tuple[tkinter.Variable, str]] = {}
    for var in variables:
        for name in var._tclCommands:
            match = _CBNAME_ID.match(str(name))
            if match:
                owners[int(match.group(1))] = (var, str(name))
    removed = 0
    for method in methods:
        entry = owners.get(id(method))
        if entry is None:                        # a widget binding, not a var trace
            continue
        var, cbname = entry
        widgets = _widgets_of(method.__self__.func)
        if not widgets or not all(_is_dead(w) for w in widgets):
            continue
        try:
            info = var.trace_info()
        except tkinter.TclError:
            info = []
        for modes, name in info:
            if name == cbname:
                mode = modes[0] if isinstance(modes, (tuple, list)) else modes
                try:
                    var.trace_remove(mode, cbname)
                    removed += 1
                except tkinter.TclError:
                    pass
                break
    return removed
