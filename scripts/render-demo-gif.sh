#!/bin/sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUTPUT=${1:-"$PROJECT_ROOT/assets/evotrace-demo.gif"}
FONT=${EVOTRACE_DEMO_FONT:-/System/Library/Fonts/SFNSMono.ttf}

if [ ! -f "$FONT" ]; then
  if command -v fc-match >/dev/null 2>&1; then
    FONT=$(fc-match -f '%{file}' monospace | head -n 1)
  else
    echo "Set EVOTRACE_DEMO_FONT to a monospace .ttf file." >&2
    exit 1
  fi
fi

ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "color=c=0x0d1117:s=1000x500:r=15:d=9" \
  -filter_complex "
    [0:v]
    drawbox=x=0:y=0:w=1000:h=56:color=0x161b22:t=fill,
    drawbox=x=0:y=55:w=1000:h=1:color=0x30363d:t=fill,
    drawbox=x=0:y=0:w=1000:h=500:color=0x30363d:t=2,
    drawbox=x=23:y=21:w=14:h=14:color=0xff5f56:t=fill,
    drawbox=x=47:y=21:w=14:h=14:color=0xffbd2e:t=fill,
    drawbox=x=71:y=21:w=14:h=14:color=0x27c93f:t=fill,
    drawtext=fontfile='$FONT':text='EvoTrace — local asset compiler':fontcolor=0x8b949e:fontsize=16:x=(w-text_w)/2:y=20,
    drawtext=fontfile='$FONT':text='\$':fontcolor=0x58a6ff:fontsize=22:x=34:y=78:enable='gte(t,0.35)',
    drawtext=fontfile='$FONT':text='evo':fontcolor=0xf0f6fc:fontsize=22:x=61:y=78:enable='between(t,0.55,0.84)',
    drawtext=fontfile='$FONT':text='evotrace':fontcolor=0xf0f6fc:fontsize=22:x=61:y=78:enable='between(t,0.85,1.14)',
    drawtext=fontfile='$FONT':text='evotrace init':fontcolor=0xf0f6fc:fontsize=22:x=61:y=78:enable='gte(t,1.15)',
    drawtext=fontfile='$FONT':text='Scanning ~/.claude and ~/.codex':fontcolor=0x8b949e:fontsize=19:x=34:y=122:enable='between(t,1.8,2.19)',
    drawtext=fontfile='$FONT':text='Scanning ~/.claude and ~/.codex.':fontcolor=0x8b949e:fontsize=19:x=34:y=122:enable='between(t,2.2,2.49)',
    drawtext=fontfile='$FONT':text='Scanning ~/.claude and ~/.codex..':fontcolor=0x8b949e:fontsize=19:x=34:y=122:enable='between(t,2.5,2.79)',
    drawtext=fontfile='$FONT':text='Scanning ~/.claude and ~/.codex...':fontcolor=0x8b949e:fontsize=19:x=34:y=122:enable='gte(t,2.8)',
    drawtext=fontfile='$FONT':text='✓':fontcolor=0x3fb950:fontsize=22:x=34:y=172:enable='gte(t,3.25)',
    drawtext=fontfile='$FONT':text='184 sessions indexed':fontcolor=0xf0f6fc:fontsize=21:x=66:y=172:enable='gte(t,3.25)',
    drawtext=fontfile='$FONT':text='✓':fontcolor=0x3fb950:fontsize=22:x=34:y=210:enable='gte(t,3.85)',
    drawtext=fontfile='$FONT':text='73 reusable assets found':fontcolor=0xf0f6fc:fontsize=21:x=66:y=210:enable='gte(t,3.85)',
    drawtext=fontfile='$FONT':text='31':fontcolor=0xd2a8ff:fontsize=20:x=66:y=258:enable='gte(t,4.45)',
    drawtext=fontfile='$FONT':text='human-corrected':fontcolor=0xc9d1d9:fontsize=19:x=112:y=258:enable='gte(t,4.45)',
    drawtext=fontfile='$FONT':text='26':fontcolor=0xd2a8ff:fontsize=20:x=480:y=258:enable='gte(t,4.8)',
    drawtext=fontfile='$FONT':text='executable evals':fontcolor=0xc9d1d9:fontsize=19:x=526:y=258:enable='gte(t,4.8)',
    drawtext=fontfile='$FONT':text='19':fontcolor=0xd2a8ff:fontsize=20:x=66:y=296:enable='gte(t,5.15)',
    drawtext=fontfile='$FONT':text='preference candidates':fontcolor=0xc9d1d9:fontsize=19:x=112:y=296:enable='gte(t,5.15)',
    drawtext=fontfile='$FONT':text='14':fontcolor=0xd2a8ff:fontsize=20:x=480:y=296:enable='gte(t,5.5)',
    drawtext=fontfile='$FONT':text='recovery trajectories':fontcolor=0xc9d1d9:fontsize=19:x=526:y=296:enable='gte(t,5.5)',
    drawtext=fontfile='$FONT':text='Everything stayed local.':fontcolor=0x3fb950:fontsize=21:x=34:y=356:enable='gte(t,6.2)',
    drawtext=fontfile='$FONT':text='No model or data upload was used.':fontcolor=0x8b949e:fontsize=19:x=34:y=394:enable='gte(t,6.55)',
    drawtext=fontfile='$FONT':text='Next\:':fontcolor=0x58a6ff:fontsize=21:x=34:y=448:enable='gte(t,7.15)',
    drawtext=fontfile='$FONT':text='evotrace build':fontcolor=0xf0f6fc:fontsize=21:x=105:y=448:enable='gte(t,7.15)',
    fade=t=out:st=8.5:d=0.5,
    split[frames][palette_input];
    [palette_input]palettegen=max_colors=96:stats_mode=diff[palette];
    [frames][palette]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle[out]
  " \
  -map "[out]" -loop 0 "$OUTPUT"

echo "Rendered $OUTPUT"
