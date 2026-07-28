#!/usr/bin/env bash
# Install or update Maintain on macOS or Linux.
#
# Installs into a private environment under ~/.local/share/maintain and links
# the command into ~/.local/bin, so `maintain` works in any project. Safe to
# run repeatedly: re-running is how you update.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/maintain"
VENV="$INSTALL_ROOT/venv"
BIN_DIR="$HOME/.local/bin"

echo
echo "{ MAINTAIN }  INSTALL OR UPDATE"
echo

command -v git >/dev/null 2>&1 || { echo "error: Git is required." >&2; exit 1; }

PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys, venv; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null
    then
        PYTHON="$(command -v "$candidate")"
        break
    fi
done
[ -n "$PYTHON" ] || { echo "error: Python 3.9 or later is required." >&2; exit 1; }
echo "Python: $PYTHON"

if [ -d "$REPO_ROOT/.git" ]; then
    if [ -z "$(git -C "$REPO_ROOT" status --porcelain)" ]; then
        echo "Updating the source clone..."
        git -C "$REPO_ROOT" pull --ff-only --quiet ||
            echo "warning: could not fast-forward; installing the current checkout." >&2
    else
        echo "warning: uncommitted changes present; installing them without pulling." >&2
    fi
    echo "Source: $REPO_ROOT ($(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD) at $(git -C "$REPO_ROOT" rev-parse --short HEAD))"
else
    echo "Source: $REPO_ROOT (not a Git clone; installing it as-is)"
fi

if [ ! -x "$VENV/bin/python" ]; then
    echo "Creating the private Maintain environment..."
    rm -rf "$VENV"
    mkdir -p "$INSTALL_ROOT"
    "$PYTHON" -m venv "$VENV"
fi

echo "Installing Maintain..."
"$VENV/bin/python" -m pip install --disable-pip-version-check --quiet --upgrade pip || true
"$VENV/bin/python" -m pip install --disable-pip-version-check --quiet \
    --upgrade --force-reinstall "$REPO_ROOT"

mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/maintain" "$BIN_DIR/maintain"

echo
echo "Installed: $INSTALL_ROOT"
echo "Runtime: $("$VENV/bin/maintain" --version)"
echo "Command: $BIN_DIR/maintain"

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        echo
        echo "warning: $BIN_DIR is not on your PATH." >&2
        echo "Add this to your shell profile, then reopen the terminal:" >&2
        echo "    export PATH=\"\$HOME/.local/bin:\$PATH\"" >&2
        ;;
esac

if ! command -v repomix >/dev/null 2>&1; then
    echo
    echo "warning: Repomix was not found. Maintain needs it to build handoff packages." >&2
    echo "Install Node.js, then run: npm install -g repomix" >&2
else
    echo "Repomix: $(command -v repomix)"
fi

echo
echo "Run this script again whenever you want to update."
