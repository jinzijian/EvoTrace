#!/bin/sh
set -eu

EVOTRACE_SOURCE=${EVOTRACE_INSTALL_SOURCE:-https://github.com/jinzijian/EvoTrace.git}
EVOTRACE_INSTALL_ROOT=${EVOTRACE_INSTALL_ROOT:-"$HOME/.local/share/evotrace/app"}
EVOTRACE_BIN_DIR=${EVOTRACE_BIN_DIR:-"$HOME/.local/bin"}

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "EvoTrace needs $2. Install it, then run this installer again." >&2
    exit 1
  fi
}

need git Git
need node "Node.js 22.19+ or 24+"
need python3 "Python 3.9+"

if ! node -e '
  const [major, minor] = process.versions.node.split(".").map(Number)
  process.exit(major >= 24 || (major === 22 && minor >= 19) ? 0 : 1)
'; then
  echo "EvoTrace needs Node.js 22.19+ or 24+. Found $(node --version)." >&2
  exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "EvoTrace needs Python 3.9 or newer." >&2
  exit 1
fi

echo "Installing the EvoTrace DeepSeek Harness distribution..."

if [ -d "$EVOTRACE_INSTALL_ROOT/.git" ]; then
  git -C "$EVOTRACE_INSTALL_ROOT" pull --ff-only
elif [ -e "$EVOTRACE_INSTALL_ROOT" ]; then
  echo "$EVOTRACE_INSTALL_ROOT exists but is not an EvoTrace Git checkout." >&2
  echo "Choose an empty EVOTRACE_INSTALL_ROOT and run the installer again." >&2
  exit 1
else
  mkdir -p "$(dirname "$EVOTRACE_INSTALL_ROOT")"
  git clone --depth 1 "$EVOTRACE_SOURCE" "$EVOTRACE_INSTALL_ROOT"
fi

for required in package.json pnpm-lock.yaml harness/launcher.mjs; do
  if [ ! -f "$EVOTRACE_INSTALL_ROOT/$required" ]; then
    echo "EvoTrace release payload is incomplete: missing $required." >&2
    echo "The GitHub revision must include the Harness application before one-line install can work." >&2
    exit 1
  fi
done

python3 -m venv "$EVOTRACE_INSTALL_ROOT/.venv"
"$EVOTRACE_INSTALL_ROOT/.venv/bin/python" -m pip install --quiet --upgrade "$EVOTRACE_INSTALL_ROOT"

if command -v pnpm >/dev/null 2>&1; then
  (cd "$EVOTRACE_INSTALL_ROOT" && pnpm install --prod --frozen-lockfile)
elif command -v corepack >/dev/null 2>&1; then
  (cd "$EVOTRACE_INSTALL_ROOT" && corepack pnpm install --prod --frozen-lockfile)
elif command -v npx >/dev/null 2>&1; then
  (cd "$EVOTRACE_INSTALL_ROOT" && npx --yes pnpm@10.30.1 install --prod --frozen-lockfile)
else
  echo "EvoTrace needs pnpm, Corepack, or npx to install DeepSeek Harness." >&2
  exit 1
fi

mkdir -p "$EVOTRACE_BIN_DIR"
ln -sfn "$EVOTRACE_INSTALL_ROOT/harness/launcher.mjs" "$EVOTRACE_BIN_DIR/evotrace"
ln -sfn "$EVOTRACE_INSTALL_ROOT/harness/launcher.mjs" "$EVOTRACE_BIN_DIR/et"
node "$EVOTRACE_INSTALL_ROOT/harness/launcher.mjs" setup

case ":$PATH:" in
  *":$EVOTRACE_BIN_DIR:"*) ;;
  *) echo "Add $EVOTRACE_BIN_DIR to PATH to use EvoTrace in a new shell." ;;
esac

echo
echo "EvoTrace installed. Launch the Harness app with:"
echo "  evotrace"
echo
echo "Inside the app, type / and run /init when you are ready to index local history."
echo "No session data was read during installation."
