#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUTPUT=${1:-"$PROJECT_ROOT/assets/evotrace-demo.gif"}
URL=${EVOTRACE_DEMO_URL:-http://127.0.0.1:5678/}
FRAMES=$(mktemp -d)
trap 'rm -rf "$FRAMES"' EXIT HUP INT TERM

node "$PROJECT_ROOT/scripts/capture-harness-demo.mjs" "$URL" "$FRAMES" >/dev/null
cp "$FRAMES/02-home.png" "$PROJECT_ROOT/assets/evotrace-harness.png"

ffmpeg -hide_banner -loglevel error -y \
  -loop 1 -t 2.6 -i "$FRAMES/01-provider.png" \
  -loop 1 -t 3.2 -i "$FRAMES/02-home.png" \
  -loop 1 -t 3.2 -i "$FRAMES/03-roles.png" \
  -filter_complex "
    [0:v]scale=1200:-1:flags=lanczos,setsar=1[provider];
    [1:v]scale=1200:-1:flags=lanczos,setsar=1[home];
    [2:v]scale=1200:-1:flags=lanczos,setsar=1[roles];
    [provider][home][roles]concat=n=3:v=1:a=0,fps=10,
    split[frames][palette_input];
    [palette_input]palettegen=max_colors=128:stats_mode=diff[palette];
    [frames][palette]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle[out]
  " \
  -map "[out]" -loop 0 "$OUTPUT"

echo "Rendered $OUTPUT and assets/evotrace-harness.png"
