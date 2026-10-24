#!/usr/bin/env python3
"""ps5-media-check: scan a media library for files the PS5 Media Player may choke on.

Runs ffprobe on every video file and applies a small, editable rule set.
Stdlib only, Python 3.8+. Findings are heuristics, not Sony's official verdict:
FAIL = likely broken (pink/green screen, crash, no picture),
WARN = may be silent, stutter, or be rejected.
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
GOOD_CONTAINERS = {".mkv", ".mp4", ".m4v", ".mov", ".ts", ".m2ts"}
GOOD_AUDIO = {"aac", "ac3", "eac3", "mp3", "pcm_s16le", "pcm_s24le", "flac", "opus", "vorbis"}
# Common audio that the PS5 player typically cannot decode (video plays silent or fails).
BAD_AUDIO = {"dts", "truehd", "mlp"}
# ffprobe "level" is level*30 for HEVC (150 = 5.0), level*10 for H.264 (42 = 4.2).
H264_MAX_LEVEL = 42
HEVC_MAX_LEVEL = 153
FAIL, WARN, OK = "FAIL", "WARN", "OK"
ORDER = {FAIL: 0, WARN: 1, OK: 2}


def evaluate(probe, ext):
    """Return a list of (severity, message) for one ffprobe JSON result."""
    issues = []
    streams = probe.get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video"
             and not (s.get("disposition") or {}).get("attached_pic")]
    audio = [s for s in streams if s.get("codec_type") == "audio"]

    if ext not in GOOD_CONTAINERS:
        issues.append((WARN, "container %s is poorly supported" % ext))
    if not video:
        return issues + [(FAIL, "no video stream")]

    v = video[0]
    codec = v.get("codec_name", "?")
    profile = v.get("profile", "")
    pix = v.get("pix_fmt", "")
    level = v.get("level") or 0
    ten_bit = "10" in pix or "12" in pix

    # Dolby Vision RPU in side data (needs ffprobe >= 4.4 to be visible).
    dv_profile = None
    for sd in v.get("side_data_list") or []:
        if "DOVI" in str(sd.get("side_data_type", "")):
            dv_profile = sd.get("dv_profile")
            compat = sd.get("dv_bl_signal_compatibility_id")
            if dv_profile == 5 or compat == 0:
                issues.append((FAIL, "Dolby Vision profile %s with no HDR10/SDR fallback "
                                     "(pink/purple picture)" % dv_profile))
            elif compat not in (1, 2):  # 1 = HDR10 fallback, 2 = SDR fallback: plays as base layer
                issues.append((WARN, "Dolby Vision profile %s (compat %s); "
                                     "verify playback" % (dv_profile, compat)))

    if codec == "h264":
        if ten_bit or "10" in profile:
            issues.append((FAIL, "H.264 10-bit (Hi10P) is unsupported (pink/green screen)"))
        elif "444" in pix or "422" in pix:
            issues.append((FAIL, "H.264 %s chroma is unsupported" % pix))
        elif level > H264_MAX_LEVEL:
            issues.append((WARN, "H.264 level %.1f exceeds 4.2" % (level / 10.0)))
    elif codec == "hevc":
        if profile and profile not in ("Main", "Main 10"):
            issues.append((FAIL, "HEVC profile '%s' unsupported (Main/Main 10 only)" % profile))
        elif "444" in pix or "422" in pix or "12" in pix:
            issues.append((FAIL, "HEVC %s unsupported" % pix))
        elif level > HEVC_MAX_LEVEL:
            issues.append((WARN, "HEVC level %.1f exceeds 5.1" % (level / 30.0)))
    elif codec in ("av1", "vp9"):
        issues.append((WARN, "%s decode is not supported by the PS5 media player" % codec))
    elif codec in ("mpeg2video", "mpeg4", "msmpeg4v3"):
        pass  # legacy codecs: plain playback usually works; container check above covers .avi
    else:
        issues.append((FAIL, "video codec '%s' unsupported" % codec))

    w, h = v.get("width") or 0, v.get("height") or 0
    if w > 4096 or h > 2304:
        issues.append((WARN, "resolution %dx%d above 4K" % (w, h)))

    if audio:
        names = {a.get("codec_name") for a in audio}
        if not names & GOOD_AUDIO:
            bad = sorted(n for n in names if n)
            issues.append((WARN, "no PS5-playable audio track (%s)" % ", ".join(bad)))
        elif names & BAD_AUDIO and len(names & GOOD_AUDIO) == 0:
            issues.append((WARN, "only unsupported audio"))
    else:
        issues.append((WARN, "no audio stream"))
    return issues


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


def check_file(path, cmd, timeout, cache):
    st = os.stat(path)
    key = "%s|%d|%d" % (path, st.st_size, int(st.st_mtime))
    if key in cache:
        return cache[key], key
    probe, err = run_probe(cmd, path, timeout)
    if err:
        res = {"path": path, "severity": FAIL, "issues": ["unreadable: " + err], "info": ""}
    else:
        issues = evaluate(probe, os.path.splitext(path)[1].lower())
        sev = min((s for s, _ in issues), key=lambda x: ORDER[x], default=OK)
        res = {"path": path, "severity": sev, "issues": [m for _, m in issues],
               "info": summarize(probe)}
    return res, key


def main(argv=None):
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    p.add_argument("root", help="directory to scan")
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
    cache = {}
    if args.cache and os.path.exists(args.cache):
        with open(args.cache) as fh:
            cache = json.load(fh)
    paths = sorted(walk(args.root))
    results, new_cache = [], {}
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        for i, (res, key) in enumerate(ex.map(lambda x: check_file(x, cmd, args.timeout, cache), paths), 1):
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
    return 1 if any(r["severity"] == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
