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

| Severity | Condition |
| --- | --- |
| FAIL | H.264 10-bit (Hi10P) or 4:2:2/4:4:4; HEVC outside Main/Main 10; Dolby Vision profile 5 (no fallback); unreadable file; unknown codec |
| WARN | Dolby Vision 7/8 without HDR10/SDR fallback id; H.264 above level 4.2; HEVC above 5.1; AV1/VP9; DTS/TrueHD-only audio; AVI and other poorly supported containers |

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
