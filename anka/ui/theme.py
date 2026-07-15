"""Theme manager: modern flat dark/light palettes applied to ttk + raw tk widgets.

Windows read `manager.palette` to style non-ttk widgets (Canvas, Listbox, Text) and use
named ttk styles ("Accent.TButton", "Card.TFrame", "Title.TLabel", "Sidebar.TButton")
for everything else. Switching theme restyles the shared `ttk.Style`, so all open
windows update at once.
"""
from __future__ import annotations

from dataclasses import dataclass
from tkinter import ttk


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str            # window background
    surface: str       # cards / panels
    surface_alt: str   # alternating rows / hover
    border: str
    text: str
    text_muted: str
    accent: str
    accent_text: str
    accent_hover: str
    danger: str

    @property
    def is_dark(self) -> bool:
        return self.name == "dark"


DARK = Palette(
    name="dark",
    bg="#1e1f26", surface="#282a36", surface_alt="#31333f", border="#3c3f4c",
    text="#e8e8ec", text_muted="#9aa0ad",
    accent="#6c7ae0", accent_text="#ffffff", accent_hover="#828fe8", danger="#e0556c",
)

LIGHT = Palette(
    name="light",
    bg="#f4f5f8", surface="#ffffff", surface_alt="#eceef3", border="#d4d8e0",
    text="#1c1e24", text_muted="#6b7280",
    accent="#5560d8", accent_text="#ffffff", accent_hover="#6a74e0", danger="#d63b54",
)

_PALETTES = {"dark": DARK, "light": LIGHT}

FONT = ("Segoe UI", 10)
FONT_TITLE = ("Segoe UI Semibold", 20)
FONT_HEADING = ("Segoe UI Semibold", 13)
FONT_SMALL = ("Segoe UI", 9)


class ThemeManager:
    def __init__(self, root):
        self._style = ttk.Style(root)
        self._style.theme_use("clam")
        self.palette: Palette = DARK

    def apply(self, name: str) -> Palette:
        self.palette = _PALETTES.get(name, DARK)
        self._configure(self.palette)
        return self.palette

    def _configure(self, p: Palette) -> None:
        s = self._style
        s.configure(".", background=p.bg, foreground=p.text, font=FONT,
                    bordercolor=p.border, focuscolor=p.accent)

        s.configure("TFrame", background=p.bg)
        s.configure("Card.TFrame", background=p.surface, relief="flat")
        s.configure("Sidebar.TFrame", background=p.surface)

        s.configure("TLabel", background=p.bg, foreground=p.text)
        s.configure("Card.TLabel", background=p.surface, foreground=p.text)
        s.configure("Title.TLabel", background=p.bg, foreground=p.text, font=FONT_TITLE)
        # Title on a card/panel surface (blends into Card.TFrame, no dark box).
        s.configure("CardTitle.TLabel", background=p.surface, foreground=p.text, font=FONT_TITLE)
        s.configure("Heading.TLabel", background=p.surface, foreground=p.text, font=FONT_HEADING)
        s.configure("Muted.TLabel", background=p.bg, foreground=p.text_muted, font=FONT_SMALL)
        s.configure("CardMuted.TLabel", background=p.surface, foreground=p.text_muted, font=FONT_SMALL)

        # Buttons
        s.configure("TButton", background=p.surface_alt, foreground=p.text,
                    bordercolor=p.border, focusthickness=0, relief="flat", padding=(12, 7))
        s.map("TButton",
              background=[("active", p.border), ("pressed", p.border)],
              foreground=[("disabled", p.text_muted)])

        s.configure("Accent.TButton", background=p.accent, foreground=p.accent_text,
                    relief="flat", padding=(14, 8), font=("Segoe UI Semibold", 10))
        s.map("Accent.TButton",
              background=[("active", p.accent_hover), ("pressed", p.accent_hover),
                         ("disabled", p.surface_alt)],
              foreground=[("disabled", p.text_muted)])

        # Big, flat sidebar / menu buttons
        s.configure("Sidebar.TButton", background=p.surface, foreground=p.text,
                    relief="flat", anchor="w", padding=(10, 8), font=FONT)
        s.map("Sidebar.TButton", background=[("active", p.surface_alt), ("pressed", p.surface_alt)])

        s.configure("Menu.TButton", background=p.surface, foreground=p.text,
                    relief="flat", padding=(18, 14), font=FONT_HEADING)
        s.map("Menu.TButton", background=[("active", p.surface_alt), ("pressed", p.accent)])

        # Inputs
        s.configure("TEntry", fieldbackground=p.surface, foreground=p.text,
                    bordercolor=p.border, insertcolor=p.text, padding=6)
        s.map("TEntry", bordercolor=[("focus", p.accent)])
        s.configure("TCombobox", fieldbackground=p.surface, background=p.surface_alt,
                    foreground=p.text, arrowcolor=p.text, bordercolor=p.border, padding=5)
        s.map("TCombobox", fieldbackground=[("readonly", p.surface)])
        s.configure("TSpinbox", fieldbackground=p.surface, background=p.surface_alt,
                    foreground=p.text, arrowcolor=p.text, bordercolor=p.border,
                    insertcolor=p.text, padding=5)
        s.map("TSpinbox", fieldbackground=[("readonly", p.surface)],
              foreground=[("disabled", p.text_muted)])

        s.configure("TCheckbutton", background=p.bg, foreground=p.text)
        s.map("TCheckbutton", background=[("active", p.bg)])
        s.configure("Card.TCheckbutton", background=p.surface, foreground=p.text)
        s.map("Card.TCheckbutton", background=[("active", p.surface)])

        # Treeview (used for mod/country lists)
        s.configure("Treeview", background=p.surface, fieldbackground=p.surface,
                    foreground=p.text, bordercolor=p.border, rowheight=26, relief="flat")
        s.configure("Treeview.Heading", background=p.surface_alt, foreground=p.text_muted,
                    relief="flat", padding=6)
        s.map("Treeview",
              background=[("selected", p.accent)],
              foreground=[("selected", p.accent_text)])
        s.map("Treeview.Heading", background=[("active", p.border)])

        s.configure("TNotebook", background=p.bg, bordercolor=p.border)
        s.configure("TNotebook.Tab", background=p.surface_alt, foreground=p.text_muted, padding=(14, 8))
        s.map("TNotebook.Tab",
              background=[("selected", p.surface)],
              foreground=[("selected", p.text)])

        s.configure("Vertical.TScrollbar", background=p.surface_alt, troughcolor=p.bg,
                    bordercolor=p.bg, arrowcolor=p.text_muted)
        # Sliders: dark trough (darker than the card surface) + accent handle.
        s.configure("Horizontal.TScale", background=p.accent, troughcolor=p.bg,
                    bordercolor=p.border, lightcolor=p.accent, darkcolor=p.accent)
        s.map("Horizontal.TScale",
              background=[("active", p.accent), ("disabled", p.surface_alt)])
        s.configure("Drop.TFrame", background=p.surface_alt)
