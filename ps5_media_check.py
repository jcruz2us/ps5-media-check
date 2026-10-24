#!/usr/bin/env python3
"""ps5-media-check: scan a media library for files a PlayStation 5 will not play cleanly.

Runs ffprobe on every video file and applies the rules for one playback target:
  plex           Plex for PlayStation 5 app (default). The app direct-streams the video
                 (remuxed into DASH/MP4) and lets the server transcode audio, so the video
                 codec is what matters.
  media-gallery  The PS5 Media Gallery (USB / built-in player), per Sony's published list.
Stdlib only, Python 3.8+.
FAIL = will not play or plays broken (pink/green picture, crash, no picture).
WARN = plays only if the server transcodes the video, or needs a manual check.
"""
import argparse
import csv
import json
import os
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".mov", ".avi", ".ts", ".m2ts", ".wmv", ".mpg", ".mpeg", ".webm"}
TARGETS = ("plex", "media-gallery")
# ffprobe "level" is level*10 for H.264 (52 = 5.2) and level*30 for HEVC (153 = 5.1).
H264_MAX_LEVEL = 52
HEVC_MAX_LEVEL = 153
# Codecs the PS5 cannot decode, so Plex must transcode the video on the server.
SERVER_TRANSCODE = {"av1", "vp9", "vc1", "mpeg2video", "mpeg4", "msmpeg4v3", "wmv3", "mpeg1video"}
IMAGE_SUBS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle"}
# Sony's Media Gallery list (playstation.com support: play video from USB on PS5).
GALLERY = {
    ".mkv": ("h264", 42, {"aac"}),
    ".mp4": ("h264", 52, {"aac"}),
    ".m4v": ("h264", 52, {"aac"}),
    ".webm": ("vp9", None, {"opus"}),
}
FAIL, WARN, OK = "FAIL", "WARN", "OK"
ORDER = {FAIL: 0, WARN: 1, OK: 2}


def _streams(probe, kind):
    return [s for s in probe.get("streams") or [] if s.get("codec_type") == kind
            and not (s.get("disposition") or {}).get("attached_pic")]


def _video_issues(v):
    """Decode-level problems that break the PS5 regardless of target."""
    issues = []
    codec, profile = v.get("codec_name", "?"), v.get("profile", "")
    pix, level = v.get("pix_fmt", ""), v.get("level") or 0
    for sd in v.get("side_data_list") or []:
        if "DOVI" in str(sd.get("side_data_type", "")):
            prof, compat = sd.get("dv_profile"), sd.get("dv_bl_signal_compatibility_id")
            if prof == 5 or compat == 0:
                issues.append((FAIL, "Dolby Vision profile %s with no HDR10/SDR fallback "
                                     "(pink/purple picture)" % prof))
            elif prof == 7:
                issues.append((WARN, "Dolby Vision profile 7 dual-layer; base layer is HDR10, "
                                     "verify playback"))
            elif compat not in (1, 2, 4, 6):
                issues.append((WARN, "Dolby Vision profile %s (compat %s); verify playback"
                               % (prof, compat)))
    if codec == "h264":
        if "10" in pix or "12" in pix or "10" in profile:
            issues.append((FAIL, "H.264 10-bit (Hi10P): no hardware decode (pink/green screen)"))
        elif "444" in pix or "422" in pix:
            issues.append((FAIL, "H.264 %s chroma: no hardware decode" % pix))
        elif level > H264_MAX_LEVEL:
            issues.append((WARN, "H.264 level %.1f above 5.2" % (level / 10.0)))
    elif codec == "hevc":
        if profile and profile not in ("Main", "Main 10"):
            issues.append((FAIL, "HEVC profile '%s' (Main/Main 10 only)" % profile))
        elif "444" in pix or "422" in pix or "12" in pix:
            issues.append((FAIL, "HEVC %s (4:2:0 8/10-bit only)" % pix))
        elif level > HEVC_MAX_LEVEL:
            issues.append((WARN, "HEVC level %.1f above 5.1" % (level / 30.0)))
    w, h = v.get("width") or 0, v.get("height") or 0
    if w > 3840 or h > 2160:
        issues.append((WARN, "resolution %dx%d above 3840x2160" % (w, h)))
    return issues


def _default_audio(audio):
    """The track a player starts with: the one flagged default, else the first."""
    for a in audio:
        if (a.get("disposition") or {}).get("default"):
            return a
    return audio[0] if audio else None


def evaluate(probe, ext, target="plex", audio_lang=None):
    """Return a list of (severity, message) for one ffprobe JSON result.

    audio_lang: optional set of ISO 639-2 codes (e.g. {"eng"}); WARN when the default audio
    track is tagged with another language. Untagged ("und") tracks are not flagged.
    """
    video, audio = _streams(probe, "video"), _streams(probe, "audio")
    subs = _streams(probe, "subtitle")
    if not video:
        return [(FAIL, "no video stream")]
    v = video[0]
    codec = v.get("codec_name", "?")
    issues = _video_issues(v)

    if target == "plex":
        if codec in SERVER_TRANSCODE:
            issues.append((WARN, "%s is not decoded by the PS5; the server must transcode "
                                 "the video" % codec))
        elif codec not in ("h264", "hevc"):
            issues.append((FAIL, "video codec '%s' unsupported" % codec))
        if not audio:
            issues.append((WARN, "no audio stream"))
        # Plex burns image subtitles into the video (full video transcode). Forced tracks are
        # turned on automatically, so only those are flagged; optional ones are opt-in.
        for s in subs:
            d = s.get("disposition") or {}
            if s.get("codec_name") in IMAGE_SUBS and d.get("forced"):
                issues.append((WARN, "forced %s subtitle forces a video transcode"
                               % s.get("codec_name")))
                break
    else:  # media-gallery: only what Sony lists
        rule = GALLERY.get(ext)
        if rule is None:
            issues.append((FAIL, "container %s not supported by Media Gallery" % ext))
        else:
            want, max_level, ok_audio = rule
            if codec != want:
                issues.append((FAIL, "Media Gallery plays %s in %s, not %s" % (want, ext, codec)))
            elif max_level and (v.get("level") or 0) > max_level:
                issues.append((FAIL, "H.264 level %.1f above %.1f for %s"
                               % ((v.get("level") or 0) / 10.0, max_level / 10.0, ext)))
            names = {a.get("codec_name") for a in audio}
            if not audio:
                issues.append((WARN, "no audio stream"))
            elif not names & ok_audio:
                issues.append((WARN, "no %s audio track (%s); plays silent"
                               % ("/".join(sorted(ok_audio)), ", ".join(sorted(n for n in names if n)))))
    if audio_lang and audio:
        first = _default_audio(audio)
        lang = ((first.get("tags") or {}).get("language") or "und").lower()
        if lang not in audio_lang and lang != "und":
            others = sorted({((a.get("tags") or {}).get("language") or "und").lower() for a in audio})
            issues.append((WARN, "default audio track is '%s', not %s (tracks: %s)"
                           % (lang, "/".join(sorted(audio_lang)), ", ".join(others))))
    # de-duplicate while keeping order
    seen, out = set(), []
    for i in issues:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def run_probe(cmd, path, timeout):
    full = cmd + ["-v", "error", "-print_format", "json", "-show_streams", "-show_format", path]
    try:
        out = subprocess.run(full, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, "ffprobe timed out"
    if out.returncode != 0:
        return None, (out.stderr.decode("utf-8", "replace").strip().splitlines() or ["ffprobe failed"])[-1]
    try:
        return json.loads(out.stdout.decode("utf-8", "replace")), None
    except ValueError:
        return None, "unparseable ffprobe output"


def summarize(probe):
    streams = probe.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = [s.get("codec_name", "?") for s in streams if s.get("codec_type") == "audio"]
    return "%s %s %s %sx%s | audio: %s" % (
        v.get("codec_name", "?"), v.get("profile", ""), v.get("pix_fmt", ""),
        v.get("width", "?"), v.get("height", "?"), ",".join(a) or "none")


def walk(root):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(("@", "."))]  # skip @eaDir etc.
        for f in files:
            if os.path.splitext(f)[1].lower() in VIDEO_EXTS and not f.startswith("."):
                yield os.path.join(dirpath, f)


def check_file(path, cmd, timeout, cache, target="plex", audio_lang=None):
    st = os.stat(path)
    key = "%s|%s|%s|%d|%d" % (target, ",".join(sorted(audio_lang or [])), path, st.st_size,
                              int(st.st_mtime))
    if key in cache:
        return cache[key], key
    probe, err = run_probe(cmd, path, timeout)
    if err:
        res = {"path": path, "severity": FAIL, "issues": ["unreadable: " + err], "info": ""}
    else:
        issues = evaluate(probe, os.path.splitext(path)[1].lower(), target, audio_lang)
        sev = min((s for s, _ in issues), key=lambda x: ORDER[x], default=OK)
        res = {"path": path, "severity": sev, "issues": [m for _, m in issues],
               "info": summarize(probe)}
    return res, key


def main(argv=None):
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    p.add_argument("root", help="directory to scan")
    p.add_argument("--target", choices=TARGETS, default="plex",
                   help="playback path: plex (Plex for PS5 app, default) or media-gallery (USB)")
    p.add_argument("--strict", action="store_true", help="treat WARN as FAIL for the exit code")
    p.add_argument("--audio-lang", metavar="CODES",
                   help="comma-separated ISO 639-2 codes (e.g. eng); WARN when the default audio "
                        "track is another language")
    p.add_argument("--ffprobe", default="ffprobe",
                   help="ffprobe command; may be a wrapper script or quoted command (default: ffprobe)")
    p.add_argument("--jobs", type=int, default=2, help="parallel probes (default 2)")
    p.add_argument("--timeout", type=int, default=120, help="per-file timeout seconds")
    p.add_argument("--cache", help="JSON cache file; unchanged files are not re-probed")
    p.add_argument("--min-severity", choices=[WARN, FAIL], default=WARN,
                   help="lowest severity to report (default WARN)")
    p.add_argument("--format", choices=["text", "csv", "json"], default="text")
    p.add_argument("--all", action="store_true", help="also list OK files")
    args = p.parse_args(argv)

    cmd = shlex.split(args.ffprobe)
    audio_lang = {c.strip().lower() for c in args.audio_lang.split(",")} if args.audio_lang else None
    cache = {}
    if args.cache and os.path.exists(args.cache):
        with open(args.cache) as fh:
            cache = json.load(fh)
    paths = sorted(walk(args.root))
    results, new_cache = [], {}
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        for i, (res, key) in enumerate(ex.map(lambda x: check_file(x, cmd, args.timeout, cache, args.target, audio_lang), paths), 1):
            results.append(res)
            new_cache[key] = res
            if sys.stderr.isatty():
                sys.stderr.write("\r%d/%d" % (i, len(paths)))
    if sys.stderr.isatty():
        sys.stderr.write("\n")
    if args.cache:
        with open(args.cache, "w") as fh:
            json.dump(new_cache, fh)

    shown = [r for r in results if args.all or ORDER[r["severity"]] <= ORDER[args.min_severity]]
    shown.sort(key=lambda r: (ORDER[r["severity"]], r["path"]))
    if args.format == "json":
        json.dump(shown, sys.stdout, indent=2)
        print()
    elif args.format == "csv":
        w = csv.writer(sys.stdout)
        w.writerow(["severity", "path", "issues", "info"])
        for r in shown:
            w.writerow([r["severity"], r["path"], "; ".join(r["issues"]), r["info"]])
    else:
        for r in shown:
            print("[%s] %s" % (r["severity"], r["path"]))
            for m in r["issues"]:
                print("    - " + m)
            if r["info"]:
                print("    " + r["info"])
        nf = sum(r["severity"] == FAIL for r in results)
        nw = sum(r["severity"] == WARN for r in results)
        print("\n%d files scanned: %d FAIL, %d WARN, %d OK" % (len(results), nf, nw, len(results) - nf - nw))
    worst = {FAIL} | ({WARN} if args.strict else set())
    return 1 if any(r["severity"] in worst for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
