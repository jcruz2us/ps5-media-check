# ps5-media-check

Keep a movie library playable on a PlayStation 5:

1. **`ps5_media_check.py`** scans files with `ffprobe` and flags the ones a PS5 will not play
   cleanly (pink/green screen, crash, no picture).
2. **`arr/`** keeps new downloads clean: a [Recyclarr](https://recyclarr.dev) config that
   builds a `PS5-Friendly` quality profile in Radarr and Sonarr, and `add_movie.py`, which
   adds a movie by name with that profile.

Stdlib-only Python 3.8+.

```
python3 ps5_media_check.py /path/to/Movies                  # Plex for PS5 rules (default)
python3 ps5_media_check.py /path/to/Movies --target media-gallery
python3 ps5_media_check.py /path/to/Movies --strict --format csv --cache .ps5cache.json
```

Exit code is 1 if any file is FAIL (or WARN, with `--strict`).

## Targets

The PS5 has two ways to play a file, with different limits.

- **`plex`** (default): the Plex for PlayStation 5 app. It direct-streams the video (remuxed
  into DASH/MP4) and asks the server to transcode audio it cannot pass through. Plex server
  logs confirm this: HEVC Main 10 HDR10 4K with TrueHD plays as `video=copy, audio=transcode`.
  So the container and the audio codec don't matter; the video codec does.
- **`media-gallery`**: the built-in player for USB drives. It follows Sony's
  [published list](https://www.playstation.com/en-us/support/hardware/play-video-music-discs-usb-drives/)
  exactly: MKV with H.264 up to level 4.2 and AAC, MP4 with H.264 up to level 5.2 and AAC,
  WebM with VP9 and Opus, and a maximum of 3840x2160. HEVC is not on that list.

## What it flags

| Target | Condition | Severity | Likely symptom |
| --- | --- | --- | --- |
| both | H.264 10-bit (Hi10P), or H.264 4:2:2 / 4:4:4 | FAIL | pink/green screen, crash |
| both | HEVC outside Main / Main 10, or 4:2:2 / 4:4:4 / 12-bit | FAIL | no picture |
| both | Dolby Vision profile 5, or any DV with no HDR10/SDR fallback | FAIL | pink/purple picture |
| both | Dolby Vision profile 7 (dual layer) | WARN | plays the HDR10 base layer; verify |
| both | H.264 above level 5.2, HEVC above level 5.1, above 3840x2160 | WARN | stutter or refusal |
| both | Unknown codec, no video stream, ffprobe cannot read the file | FAIL | cannot play |
| plex | AV1, VP9, VC-1, MPEG-2, MPEG-4 ASP (XviD/DivX) | WARN | the server must transcode the video |
| plex | Forced image subtitles (PGS, VobSub) | WARN | burned in, which forces a video transcode |
| plex | No audio stream | WARN | silent |
| media-gallery | Codec, container or level outside Sony's list | FAIL | refuses to play |
| media-gallery | No AAC (MKV/MP4) or Opus (WebM) audio track | WARN | plays silent |

Not flagged: Dolby Vision profile 8 with an HDR10, SDR or HLG fallback (it plays as the base
layer); TrueHD, DTS and other lossless audio on the `plex` target (Plex transcodes it).

These rules come from Sony's format list, the PS5's hardware decoders, and Plex's own playback
decisions. They are not an official compatibility list. The constants at the top of the script
hold the limits. Dolby Vision detection needs ffprobe 4.4 or newer.

## Options

`--target {plex,media-gallery}` · `--strict` (WARN counts as failure) ·
`--audio-lang eng` (WARN when the default audio track is another language) · `--ffprobe CMD` ·
`--jobs N` · `--timeout S` · `--cache FILE` (skip unchanged files) ·
`--min-severity {WARN,FAIL}` · `--format {text,csv,json}` · `--all`

### No ffprobe on the host

If your system has ffmpeg but no ffprobe, drop a static build next to the script:

```
curl -LO https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
tar -xJf ffmpeg-release-amd64-static.tar.xz --strip-components=1 --wildcards '*/ffprobe'
python3 ps5_media_check.py /path/to/Movies --ffprobe ./ffprobe
```

`examples/ffprobe-docker.sh` wraps a Docker image instead, but container start-up costs
~24 s per file on a low-power CPU, so prefer the static binary.

## Radarr and Sonarr (`arr/`)

`arr/recyclarr.yml` creates a **PS5-Friendly** quality profile. Recyclarr re-applies it on a
schedule, so manual edits in the UI are overwritten.

| Score | Custom formats |
| --- | --- |
| -10000 (rejected) | Language: Not English, MULTi, Bad Dual Groups, Dual Audio\*, DV (w/o HDR fallback), AV1, VP9, VC-1, MPEG2, x264 10bit (Hi10P)\*, XviD/DivX\*, BR-DISK, 3D, Extras, LQ, LQ (Release Title), Upscaled |
| +30 | DD+, DD+ ATMOS |
| +20 | DD, AAC |
| -20 | TrueHD, TrueHD ATMOS, DTS-HD MA, DTS X |
| -10 | DTS |

\* local formats in `arr/custom-formats/`; the rest are [TRaSH Guides](https://trash-guides.info) formats.

- **Qualities:** Bluray and WEB at 2160p, 1080p and 720p (plus HDTV for Sonarr). Remux and
  BR-DISK are excluded.
- **Upgrades are off,** so assigning the profile to an existing library does not trigger
  re-downloads.
- **English first.** Plex plays the file's default audio track, and multi-language releases
  often put another language first. Release titles can't show track order, so MULTi and dual-audio
  releases are rejected; scan with `--audio-lang eng` to catch the rest.
- **min_format_score is -100:** -10000 formats are rejected, small audio penalties are not.
- **Audio scores only break ties.** Lossless tracks play fine through Plex; the lighter
  tracks are preferred because they avoid a transcode.

Release titles can't reveal everything (a WEB-DL may be DV profile 5 without saying so), so
scan what lands with `ps5_media_check.py`.

### Run it

```
cd arr && mkdir -p config
cp secrets.example.yml config/secrets.yml          # add your URLs and API keys
cp -r recyclarr.yml settings.yml custom-formats config/
docker compose run --rm recyclarr sync              # once
docker compose up -d                                # then daily
```

### Add a movie by name

```
export RADARR_URL=http://radarr:7878 RADARR_API_KEY=...
python3 arr/add_movie.py "Paddington 2"
python3 arr/add_movie.py "Dune (2021)"     # a year in parentheses pins the match
python3 arr/add_movie.py "Dune" --pick 2   # choose from the printed candidates
python3 arr/add_movie.py "Heat" --dry-run
```

- The script adds the movie with the `PS5-Friendly` profile, monitors it and starts a search.
- If the title is ambiguous, it lists the candidates and exits with code 2.
- If the movie is already in Radarr, it switches it to the profile and searches if there is no
  file yet.
- **Fallbacks.** If Radarr's search grabs nothing within `--fallback-wait` seconds (default 90),
  the script runs an interactive search and grabs, in order:
  1. the first release Radarr accepts (interactive search sees releases the automatic one can miss);
  2. otherwise the best release rejected only for soft reasons: size limits, `LQ` / `Upscaled`
     formats, or an SD/HDTV quality the profile leaves out. HD beats SD, a clean release beats a
     low-quality one, then higher resolution and more seeders win; upscales come last.

  Never used as a fallback: any other -10000 format (Hi10P, DV without fallback, AV1, MULTi,
  not English...), Remux/BR-DISK/CAM, files over 40 GB, dead torrents, blocklisted or unmatched
  releases. If a grab fails (dead indexer link) it tries the next one. `--no-fallback` turns this off.
- `--wait` blocks until Radarr imports a file; `--on-import CMD` then runs a hook (e.g. copy the
  folder to another machine) with `MOVIE_TITLE`, `MOVIE_YEAR`, `MOVIE_PATH`, `MOVIE_FOLDER` and
  `MOVIE_FILE` set. Run it under `nohup` or a scheduler for long downloads.

## Tests

`pip install pytest && pytest`
