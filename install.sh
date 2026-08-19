#!/bin/sh
set -eu

EVOTRACE_SOURCE=${EVOTRACE_INSTALL_SOURCE:-git+https://github.com/jinzijian/evotrace.git}

if ! command -v git >/dev/null 2>&1; then
  echo "EvoTrace needs Git. Install Git, then run this installer again." >&2
  exit 1
fi

echo "Installing EvoTrace..."

if command -v uv >/dev/null 2>&1; then
  uv tool install --force "$EVOTRACE_SOURCE"
elif command -v pipx >/dev/null 2>&1; then
  pipx install --force "$EVOTRACE_SOURCE"
elif command -v python3 >/dev/null 2>&1; then
  if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
    echo "EvoTrace needs Python 3.9 or newer." >&2
    exit 1
  fi
  EVOTRACE_INSTALL_ROOT=${EVOTRACE_INSTALL_ROOT:-"$HOME/.local/share/evotrace"}
  EVOTRACE_BIN_DIR=${EVOTRACE_BIN_DIR:-"$HOME/.local/bin"}
  python3 -m venv "$EVOTRACE_INSTALL_ROOT/venv"
  "$EVOTRACE_INSTALL_ROOT/venv/bin/python" -m pip install --upgrade "$EVOTRACE_SOURCE"
  mkdir -p "$EVOTRACE_BIN_DIR"
  ln -sfn "$EVOTRACE_INSTALL_ROOT/venv/bin/evotrace" "$EVOTRACE_BIN_DIR/evotrace"
  ln -sfn "$EVOTRACE_INSTALL_ROOT/venv/bin/et" "$EVOTRACE_BIN_DIR/et"
  case ":$PATH:" in
    *":$EVOTRACE_BIN_DIR:"*) ;;
    *) echo "Add $EVOTRACE_BIN_DIR to PATH to use EvoTrace in a new shell." ;;
  esac
else
  echo "Install uv, pipx, or Python 3.9+, then run this installer again." >&2
  exit 1
fi

echo
echo "EvoTrace installed. Build your local asset index with:"
echo "  evotrace init"
echo
echo "No session data was read during installation."
