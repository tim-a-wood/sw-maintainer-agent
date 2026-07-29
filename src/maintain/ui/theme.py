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
    accent_hover: str
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
    window="#dfe6ef", surface="#f8fafc", bar="#eef3f9", edge="#d3dde9",
    ink="#1b2534", dim="#5c6c81", faint="#8ba0b6",
    accent="#0b6fb8", accent_hover="#095e9d", accent_ink="#ffffff",
    accent_soft="#e3eef7",
    ok="#177245", ok_soft="#e2efe8", bad="#b23a2f", bad_soft="#f6e6e4",
    warn="#8a6100", warn_soft="#f4ecd8",
    chip="#edf2f8", chip_edge="#d8e2ee", code_bg="#101826", code_ink="#d5e3f2",
    drop="#f2f7fc",
)

DARK = Palette(
    window="#10151f", surface="#1b2230", bar="#212a3c", edge="#32405a",
    ink="#e6edf6", dim="#9aa9bd", faint="#6d7d92",
    accent="#4cbdff", accent_hover="#6fcaff", accent_ink="#06263c",
    accent_soft="#1d3247",
    ok="#3bd694", ok_soft="#173328", bad="#ff8577", bad_soft="#3a2320",
    warn="#f0c052", warn_soft="#3a3320",
    chip="#242e42", chip_edge="#313c54", code_bg="#0d1422", code_ink="#cfdff2",
    drop="#1f2839",
)

# The palette the painted widgets read. main() sets it before any widget exists.
ACTIVE: Palette = LIGHT


def set_active(palette: Palette) -> None:
    global ACTIVE
    ACTIVE = palette


def stylesheet(p: Palette) -> str:
    set_active(p)
    return f"""
QMainWindow, QDialog {{ background: {p.surface}; }}
QWidget {{ color: {p.ink}; font-size: 13px;
           font-family: "Segoe UI Variable Text", "Segoe UI", "DejaVu Sans",
                        system-ui, sans-serif; }}
#Screen {{ background: {p.surface}; }}
#StageBar {{ background: {p.surface}; border-bottom: 1px solid {p.bar}; }}
#FootBar {{ background: {p.bar}; border-top: 1px solid {p.edge}; }}
#FootLabel {{ color: {p.dim}; font-size: 11px; }}
QStatusBar {{ background: {p.bar}; color: {p.dim}; font-size: 11.5px; }}

QLabel#Title {{ font-size: 19px; font-weight: 600; }}
QLabel#Eyebrow {{ color: {p.accent}; font-size: 10px; font-weight: 700; }}
QLabel#Lead {{ color: {p.dim}; font-size: 13px; }}
QLabel#Hint {{ color: {p.faint}; font-size: 11px; }}
QLabel#Dim {{ color: {p.dim}; font-size: 12px; }}
QLabel#Ok {{ color: {p.ok}; font-weight: 600; }}
QLabel#Bad {{ color: {p.bad}; font-weight: 600; }}
QLabel#Mono {{ font-family: "Cascadia Mono", Consolas, "DejaVu Sans Mono",
               monospace; font-size: 12px; }}
QLabel#MonoHint {{ font-family: "Cascadia Mono", Consolas, "DejaVu Sans Mono",
                   monospace; font-size: 11px; color: {p.faint}; }}

QPushButton {{ border: 1px solid transparent; border-radius: 6px;
               padding: 8px 16px; font-weight: 600; background: transparent; }}
QPushButton:focus {{ border: 1px solid {p.accent}; outline: none; }}
QPushButton:disabled {{ color: {p.faint}; }}
QPushButton#Primary {{ background: {p.accent}; color: {p.accent_ink}; }}
QPushButton#Primary:hover {{ background: {p.accent_hover}; }}
QPushButton#Primary:pressed {{ background: {p.accent_hover};
                               padding-top: 9px; padding-bottom: 7px; }}
QPushButton#Primary:disabled {{ background: {p.chip}; color: {p.faint}; }}
QPushButton#Secondary {{ border: 1px solid {p.edge}; background: {p.surface}; }}
QPushButton#Secondary:hover {{ border-color: {p.accent}; background: {p.accent_soft}; }}
QPushButton#Secondary:pressed {{ border-color: {p.accent};
                                 background: {p.accent_soft};
                                 padding-top: 9px; padding-bottom: 7px; }}
QPushButton#Ghost {{ color: {p.dim}; padding: 8px 10px; }}
QPushButton#Ghost:hover {{ color: {p.accent}; background: {p.accent_soft};
                           border-radius: 6px; }}
QPushButton#Danger {{ color: {p.dim}; padding: 8px 10px; }}
QPushButton#Danger:hover {{ color: {p.bad}; background: {p.bad_soft}; }}

QMessageBox QPushButton, QInputDialog QPushButton, QFileDialog QPushButton {{
    border: 1px solid {p.edge}; background: {p.surface};
    padding: 6px 14px; min-width: 64px; }}
QMessageBox QPushButton:hover, QInputDialog QPushButton:hover,
QFileDialog QPushButton:hover {{
    border-color: {p.accent}; background: {p.accent_soft}; }}
QMessageBox QPushButton:default, QInputDialog QPushButton:default {{
    background: {p.accent}; color: {p.accent_ink}; border-color: {p.accent}; }}
QMessageBox QPushButton:default:hover, QInputDialog QPushButton:default:hover {{
    background: {p.accent_hover}; }}

QToolTip {{ background: {p.bar}; color: {p.ink}; border: 1px solid {p.edge};
            padding: 4px 8px; font-size: 11px; }}

QFrame#Choice {{ border: 1px solid {p.edge}; border-radius: 9px;
                 background: {p.surface}; }}
QFrame#Choice:hover, QFrame#Choice:focus {{
    border-color: {p.accent}; background: {p.accent_soft}; }}
QLabel#ChoiceTitle {{ font-size: 14px; font-weight: 600; }}
QLabel#ChoiceSub {{ font-size: 12px; color: {p.dim}; }}

QFrame#Card {{ border: 1px solid {p.edge}; border-radius: 9px;
               background: {p.surface}; }}
QFrame#Finding {{ border: 1px solid {p.edge}; border-left: 3px solid {p.warn};
                  border-radius: 8px; background: {p.surface}; }}
QFrame#PacketCard {{ border: 1px solid {p.accent}; border-radius: 10px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {p.accent_soft}, stop:1 {p.surface}); }}
QLabel#PacketName {{ font-family: "Cascadia Mono", Consolas, "DejaVu Sans Mono",
                     monospace; font-size: 12px; font-weight: 600; }}
QLabel#PacketGrip {{ color: {p.faint}; font-size: 14px; }}

QFrame#DropZone {{ border: 2px dashed {p.accent}; border-radius: 10px;
                   background: {p.drop}; }}
QFrame#DropZone[active="true"] {{ background: {p.accent_soft}; }}
QLabel#DropMain {{ font-weight: 600; font-size: 13px; }}
QLabel#DropSlim {{ color: {p.dim}; font-size: 12px; }}

QFrame#ChipFrame {{ background: {p.chip}; border: 1px solid {p.chip_edge};
                    border-radius: 7px; }}
QLabel#ChipName {{ font-family: "Cascadia Mono", Consolas, "DejaVu Sans Mono",
                   monospace; font-size: 11px; }}
QLabel#ChipSize {{ color: {p.faint}; font-size: 10.5px; }}
QPushButton#ChipRemove {{ color: {p.faint}; font-size: 12px; font-weight: 700;
                          border-radius: 4px; padding: 0; }}
QPushButton#ChipRemove:hover {{ color: {p.bad}; background: {p.bad_soft}; }}

QLineEdit, QPlainTextEdit, QSpinBox {{
    border: 1px solid {p.edge}; border-radius: 7px; padding: 7px 10px;
    background: {p.surface}; selection-background-color: {p.accent};
    selection-color: {p.accent_ink}; }}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {{
    border: 1px solid {p.accent}; }}
QPlainTextEdit#Code {{ background: {p.code_bg}; color: {p.code_ink};
                       font-family: "Cascadia Mono", Consolas,
                       "DejaVu Sans Mono", monospace; font-size: 11px;
                       border: none; border-radius: 8px; }}

QLabel#Chip {{ background: {p.chip}; border: 1px solid {p.chip_edge};
               border-radius: 7px; padding: 5px 9px; font-size: 11px; }}
QLabel#StatePass {{ background: {p.ok_soft}; color: {p.ok}; border-radius: 9px;
                    padding: 2px 9px; font-size: 9px; font-weight: 700; }}
QLabel#StateFail {{ background: {p.bad_soft}; color: {p.bad}; border-radius: 9px;
                    padding: 2px 9px; font-size: 9px; font-weight: 700; }}
QLabel#StateWait {{ background: {p.chip}; color: {p.faint}; border-radius: 9px;
                    padding: 2px 9px; font-size: 9px; font-weight: 700; }}
QLabel#StateWarn {{ background: {p.warn_soft}; color: {p.warn}; border-radius: 9px;
                    padding: 2px 9px; font-size: 9px; font-weight: 700; }}
QLabel#StateAccent {{ background: {p.accent_soft}; color: {p.accent};
                      border-radius: 9px; padding: 2px 9px; font-size: 9px;
                      font-weight: 700; }}
QLabel#NumberBadge {{ background: {p.accent_soft}; color: {p.accent};
                      border-radius: 12px; font-size: 11px; font-weight: 700; }}
QLabel#SevMinor {{ background: {p.warn_soft}; color: {p.warn}; border-radius: 9px;
                   padding: 2px 9px; font-size: 9px; font-weight: 700; }}
QLabel#SevMajor {{ background: {p.bad_soft}; color: {p.bad}; border-radius: 9px;
                   padding: 2px 9px; font-size: 9px; font-weight: 700; }}
QLabel#TagSuper {{ color: {p.faint}; border: 1px solid {p.chip_edge};
                   border-radius: 8px; padding: 1px 7px; font-size: 8.5px;
                   font-weight: 700; }}

QRadioButton {{ spacing: 9px; font-weight: 600; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 9px;
                           border: 2px solid {p.chip_edge};
                           background: {p.surface}; }}
QRadioButton::indicator:hover {{ border-color: {p.accent}; }}
QRadioButton::indicator:checked {{ border: 5px solid {p.accent};
                                   width: 8px; height: 8px; }}

QSpinBox::up-button, QSpinBox::down-button {{
    width: 18px; border-left: 1px solid {p.edge}; background: transparent; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background: {p.accent_soft}; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.chip_edge}; border-radius: 4px;
                               min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {p.faint}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p.chip_edge}; border-radius: 4px;
                                 min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
"""


def palette_for(dark: bool) -> Palette:
    return DARK if dark else LIGHT


def qt_palette(p: Palette):
    """A QPalette so native pieces (menus, dialogs, placeholders) match."""
    from PySide6.QtGui import QColor, QPalette
    qt = QPalette()
    roles = {
        QPalette.ColorRole.Window: p.window,
        QPalette.ColorRole.Base: p.surface,
        QPalette.ColorRole.AlternateBase: p.bar,
        QPalette.ColorRole.Text: p.ink,
        QPalette.ColorRole.WindowText: p.ink,
        QPalette.ColorRole.PlaceholderText: p.faint,
        QPalette.ColorRole.Button: p.surface,
        QPalette.ColorRole.ButtonText: p.ink,
        QPalette.ColorRole.Highlight: p.accent,
        QPalette.ColorRole.HighlightedText: p.accent_ink,
        QPalette.ColorRole.ToolTipBase: p.bar,
        QPalette.ColorRole.ToolTipText: p.ink,
        QPalette.ColorRole.Link: p.accent,
        QPalette.ColorRole.Mid: p.faint,
        QPalette.ColorRole.Dark: p.dim,
        QPalette.ColorRole.Light: p.edge,
    }
    for role, value in roles.items():
        qt.setColor(role, QColor(value))
    qt.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,
                QColor(p.faint))
    qt.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText,
                QColor(p.faint))
    return qt
