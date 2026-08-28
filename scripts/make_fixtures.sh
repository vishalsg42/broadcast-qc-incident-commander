#!/usr/bin/env bash
# Generate deterministic test media with KNOWN, verifiable properties.
#
# Synthetic rather than sourced footage, deliberately: the measurements are real
# (ffmpeg genuinely decodes and measures these files), but the fixtures are
# reproducible, so evaluation runs are comparable and the demo does not depend on
# a stock clip's licensing.
#
# Layout of each 45s fixture (25fps, 1280x720, 1125 frames):
#   00.0 - 10.0  head black + silence   LEGAL   (required by the profile)
#   10.0 - 43.0  programme body + tone
#   43.0 - 45.0  tail black + silence   LEGAL   (permitted by the profile)
#
# NOTE: built in a single ffmpeg pass using the concat *filter*. The concat
# *demuxer* with -c copy produced an 86s file from 45s of input (AAC edit-list
# accumulation across segments) - do not go back to it.
#
# Outputs:
#   master_good.mp4   body normalised toward -23 LUFS   -> should PASS
#   master_hot.mp4    body normalised toward -18 LUFS   -> source-out-of-spec case
#   body_black.mp4    good audio, ILLEGAL 2s black hole at 20-22s inside the body
set -euo pipefail
cd "$(dirname "$0")/.."

OUT=media; mkdir -p "$OUT"
FPS=25; W=1280; H=720
HEAD=10; BODY=33; TAIL=2

# -movflags +faststart is mandatory: without it a browser cannot seek or
# progressively play, and the demo LOOKS broken on camera.
gen () {                                    # gen <outfile> <target_lufs>
  local out="$1" lufs="$2"
  ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "color=c=black:s=${W}x${H}:r=${FPS}:d=${HEAD}" \
    -f lavfi -i "testsrc2=s=${W}x${H}:r=${FPS}:d=${BODY}" \
    -f lavfi -i "color=c=black:s=${W}x${H}:r=${FPS}:d=${TAIL}" \
    -f lavfi -i "anullsrc=r=48000:cl=stereo:d=${HEAD}" \
    -f lavfi -i "sine=frequency=440:sample_rate=48000:duration=${BODY}" \
    -f lavfi -i "anullsrc=r=48000:cl=stereo:d=${TAIL}" \
    -filter_complex "\
      [0:v][1:v][2:v]concat=n=3:v=1:a=0[v]; \
      [4:a]loudnorm=I=${lufs}:TP=-1.5:LRA=11,aformat=sample_rates=48000:channel_layouts=stereo[bod]; \
      [3:a][bod][5:a]concat=n=3:v=0:a=1[a]" \
    -map "[v]" -map "[a]" -c:v libx264 -pix_fmt yuv420p -c:a aac \
    -movflags +faststart "$out"
  echo "  wrote $out"
}

echo "generating fixtures..."
gen "$OUT/master_good.mp4" -23
gen "$OUT/master_hot.mp4"  -18

# Illegal black: a 2s hole at 20-22s, i.e. 10s into the programme body.
ffmpeg -hide_banner -loglevel error -y -i "$OUT/master_good.mp4" \
  -vf "drawbox=x=0:y=0:w=iw:h=ih:color=black@1.0:t=fill:enable='between(t,20,22)'" \
  -c:a copy -c:v libx264 -pix_fmt yuv420p -movflags +faststart "$OUT/body_black.mp4"
echo "  wrote $OUT/body_black.mp4"

echo
printf '%-20s %8s %8s %6s\n' FIXTURE DURATION FRAMES SIZE
for f in "$OUT"/*.mp4; do
  d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")
  n=$(ffprobe -v error -select_streams v -show_entries stream=nb_frames -of csv=p=0 "$f")
  s=$(du -h "$f" | cut -f1 | tr -d ' ')
  printf '%-20s %8.3f %8s %6s\n' "$(basename "$f")" "$d" "$n" "$s"
done
