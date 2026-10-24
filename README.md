# ps5-media-check

Scan a video library and flag files the PlayStation 5 Media Player is likely to choke on
(pink/green screen, crash, no sound). Uses `ffprobe`; stdlib-only Python 3.8+.

```
python3 ps5_media_check.py /path/to/Movies                 # text report, WARN and FAIL
python3 ps5_media_check.py /path/to/Movies --format csv --cache .ps5cache.json
python3 ps5_media_check.py /path/to/Movies --all --format json
```

Exit code is 1 if any file is FAIL.

## What it flags

| Area | Condition | Severity | Likely symptom |
| --- | --- | --- | --- |
| Video | H.264 10-bit (Hi10P) | FAIL | pink/green screen, crash |
| Video | H.264 4:2:2 / 4:4:4 chroma | FAIL | no picture |
| Video | HEVC profile other than Main / Main 10, or 4:2:2 / 4:4:4 / 12-bit | FAIL | no picture |
| Video | Dolby Vision profile 5, or no HDR10/SDR fallback | FAIL | pink/purple picture |
| Video | Dolby Vision 7/8 with other fallback ids | WARN | wrong colors on some files |
| Video | H.264 above level 4.2, HEVC above level 5.1 | WARN | stutter, refusal to play |
| Video | AV1, VP9 | WARN | unsupported decode |
| Video | Resolution above 4096x2304 | WARN | refusal to play |
| Video | Unknown codec, or no video stream | FAIL | cannot play |
| Audio | Only DTS / TrueHD / unsupported tracks | WARN | silent playback |
| Audio | No audio stream | WARN | silent playback |
| Container | Anything besides MKV, MP4, M4V, MOV, TS, M2TS (e.g. AVI) | WARN | poor seek/metadata support |
| File | ffprobe cannot read it | FAIL | corrupt or truncated |

Dolby Vision profile 8 with an HDR10 or SDR fallback is not flagged; it plays as the base layer.

These rules are heuristics from community reports and Sony's published format list, not a
guarantee. Edit the constants at the top of `ps5_media_check.py` to tune them.
Dolby Vision detection needs ffprobe 4.4 or newer.

## Options

`--ffprobe CMD` ffprobe command or wrapper (default `ffprobe`) · `--jobs N` · `--timeout S` ·
`--cache FILE` skip unchanged files on reruns · `--min-severity {WARN,FAIL}` · `--format {text,csv,json}` · `--all`

## No ffprobe on the host

If your system has ffmpeg but no ffprobe, drop a static build next to the script and point at it:

```
curl -LO https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
tar -xJf ffmpeg-release-amd64-static.tar.xz --strip-components=1 --wildcards '*/ffprobe'
python3 ps5_media_check.py /path/to/Movies --ffprobe ./ffprobe
```

`examples/ffprobe-docker.sh` wraps a Docker image instead, but container start-up cost
~24 s per file on a low-power CPU, so prefer the static binary.

## Tests

`pip install pytest && pytest`
