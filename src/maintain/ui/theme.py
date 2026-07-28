"""Palette and application stylesheet, light and dark, matching the mockup."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    window: str
    surface: str
    bar: str
    edge: str
    ink: str
    dim: str
    faint: str
    accent: str
    accent_ink: str
    accent_soft: str
    ok: str
    ok_soft: str
    bad: str
    bad_soft: str
    warn: str
    warn_soft: str
    chip: str
    chip_edge: str
    code_bg: str
    code_ink: str
    drop: str


LIGHT = Palette(
    window="#dfe6ef", surface="#f8fafc", bar="#eef3f9", edge="#c9d5e3",
    ink="#1b2534", dim="#5c6c81", faint="#8ba0b6",
    accent="#0b6fb8", accent_ink="#ffffff", accent_soft="#e3eef7",
    ok="#177245", ok_soft="#e2efe8", bad="#b23a2f", bad_soft="#f6e6e4",
    warn="#8a6100", warn_soft="#f4ecd8",
    chip="#edf2f8", chip_edge="#d8e2ee", code_bg="#101826", code_ink="#d5e3f2",
    drop="#f2f7fc",
)

DARK = Palette(
    window="#10151f", surface="#1b2230", bar="#212a3c", edge="#32405a",
    ink="#e6edf6", dim="#9aa9bd", faint="#6d7d92",
    accent="#4cbdff", accent_ink="#06263c", accent_soft="#1d3247",
    ok="#3bd694", ok_soft="#173328", bad="#ff8577", bad_soft="#3a2320",
    warn="#f0c052", warn_soft="#3a3320",
    chip="#242e42", chip_edge="#313c54", code_bg="#0d1422", code_ink="#cfdff2",
    drop="#1f2839",
)


def stylesheet(palette: Palette) -> str:
    p = palette
    return f"""
QMainWindow, QDialog {{ background: {p.window}; }}
QWidget {{ color: {p.ink}; font-size: 13px; }}
#Screen {{ background: {p.surface}; }}
#StageBar {{ background: {p.surface}; }}
#FootBar {{ background: {p.bar}; border-top: 1px solid {p.edge}; }}
#FootLabel {{ color: {p.dim}; font-size: 11px; }}

QLabel#Title {{ font-size: 19px; font-weight: 600; }}
QLabel#Eyebrow {{ color: {p.accent}; font-size: 11px; font-weight: 700;
                  letter-spacing: 1px; }}
QLabel#Lead {{ color: {p.dim}; font-size: 13px; }}
QLabel#Hint {{ color: {p.faint}; font-size: 11px; }}
QLabel#Ok {{ color: {p.ok}; font-weight: 600; }}
QLabel#Bad {{ color: {p.bad}; font-weight: 600; }}
QLabel#Mono {{ font-family: "Cascadia Mono", Consolas, monospace; }}

QPushButton {{ border: 1px solid transparent; border-radius: 6px;
               padding: 8px 16px; font-weight: 600; background: transparent; }}
QPushButton:focus {{ outline: none; border: 1px solid {p.accent}; }}
QPushButton#Primary {{ background: {p.accent}; color: {p.accent_ink}; }}
QPushButton#Primary:disabled {{ background: {p.chip}; color: {p.faint}; }}
QPushButton#Secondary {{ border: 1px solid {p.edge}; background: {p.surface}; }}
QPushButton#Secondary:hover {{ border-color: {p.accent}; background: {p.accent_soft}; }}
QPushButton#Ghost {{ color: {p.dim}; }}
QPushButton#Ghost:hover {{ color: {p.accent}; }}
QPushButton#Danger {{ color: {p.dim}; }}
QPushButton#Danger:hover {{ color: {p.bad}; background: {p.bad_soft}; }}

QPushButton#Choice {{ border: 1px solid {p.edge}; border-radius: 9px;
                      background: {p.surface}; padding: 14px 16px; text-align: left;
                      font-size: 14px; }}
QPushButton#Choice:hover, QPushButton#Choice:focus {{
    border-color: {p.accent}; background: {p.accent_soft}; }}

QFrame#Card {{ border: 1px solid {p.edge}; border-radius: 9px; background: {p.surface}; }}
QFrame#PacketCard {{ border: 1px solid {p.accent}; border-radius: 10px;
                     background: {p.accent_soft}; }}
QFrame#Finding {{ border: 1px solid {p.edge}; border-left: 3px solid {p.warn};
                  border-radius: 8px; background: {p.surface}; }}

QFrame#DropZone {{ border: 2px dashed {p.accent}; border-radius: 10px;
                   background: {p.drop}; }}
QFrame#DropZone[active="true"] {{ background: {p.accent_soft}; }}

QLineEdit, QPlainTextEdit, QSpinBox {{
    border: 1px solid {p.edge}; border-radius: 7px; padding: 7px 10px;
    background: {p.surface}; selection-background-color: {p.accent};
    selection-color: {p.accent_ink}; }}
QPlainTextEdit#Code {{ background: {p.code_bg}; color: {p.code_ink};
                       font-family: "Cascadia Mono", Consolas, monospace;
                       font-size: 11px; }}

QListWidget {{ border: none; background: transparent; }}

QLabel#Chip {{ background: {p.chip}; border: 1px solid {p.chip_edge};
               border-radius: 7px; padding: 5px 9px; font-size: 11px;
               font-family: "Cascadia Mono", Consolas, monospace; }}
QLabel#StatePass {{ background: {p.ok_soft}; color: {p.ok}; border-radius: 5px;
                    padding: 2px 8px; font-size: 10px; font-weight: 700; }}
QLabel#StateFail {{ background: {p.bad_soft}; color: {p.bad}; border-radius: 5px;
                    padding: 2px 8px; font-size: 10px; font-weight: 700; }}
QLabel#StateWait {{ background: {p.chip}; color: {p.faint}; border-radius: 5px;
                    padding: 2px 8px; font-size: 10px; font-weight: 700; }}
QLabel#StateWarn {{ background: {p.warn_soft}; color: {p.warn}; border-radius: 5px;
                    padding: 2px 8px; font-size: 10px; font-weight: 700; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
"""


def palette_for(dark: bool) -> Palette:
    return DARK if dark else LIGHT
