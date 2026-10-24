#!/usr/bin/env python3
"""add_movie: look a movie up in Radarr and add it with a PS5-safe quality profile.

    add_movie.py "The Matrix"            # best match, add, start a search
    add_movie.py "Dune (2021)"           # a year in parentheses pins the match
    add_movie.py "Dune" --pick 2         # choose from the printed candidates
    add_movie.py "Dune" --dry-run        # show what would happen
    add_movie.py "Heat (1995)" --wait --on-import ./copy.sh
                                         # block until Radarr imports a file, then run a hook

Config: RADARR_URL and RADARR_API_KEY from the environment or --env-file (KEY=VALUE lines).
If the movie is already in Radarr, it is switched to the profile, monitored, and searched
if it has no file. If Radarr's search grabs nothing within --fallback-wait seconds, the best
release that was rejected only for soft reasons (size limits, LQ/Upscaled, SD quality) is
grabbed; anything that breaks PS5 playback or English-first audio is never a fallback. Exit codes: 0 added/updated, 2 ambiguous or not found, 3 wait timed out, 1 error;
with --on-import, the hook's exit code.
Stdlib only, Python 3.8+.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_PROFILE = "PS5-Friendly"


def load_env(path):
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class Radarr:
    def __init__(self, url, key):
        self.base = url.rstrip("/") + "/api/v3"
        self.key = key

    def call(self, path, method="GET", body=None, timeout=60, retries=1) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(retries + 1):
            req = urllib.request.Request(self.base + path, data=data, method=method, headers={
                "X-Api-Key": self.key, "Content-Type": "application/json"})
            try:
                raw = urllib.request.urlopen(req, timeout=timeout).read()
                return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                # 5xx from a reverse proxy (slow indexer search) is worth one more try
                if e.code >= 500 and attempt < retries:
                    time.sleep(5)
                    continue
                raise SystemExit("Radarr %s %s -> HTTP %s: %s" % (
                    method, path, e.code, e.read().decode("utf-8", "replace")[:300]))


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def parse_query(q):
    m = re.match(r"^(.*?)\s*[\(\[]((?:18|19|20)\d\d)[\)\]]\s*$", q.strip())
    if m and m.group(1):
        return m.group(1), int(m.group(2))
    return q.strip(), None


def rank(results, title, year):
    """Sort candidates: exact title, matching year, then Radarr's popularity order."""
    t = norm(title)

    def key(item):
        i, r = item
        exact = norm(r.get("title")) == t or norm(r.get("originalTitle")) == t
        return (not exact, year is not None and r.get("year") != year, i)
    return [r for _, r in sorted(enumerate(results), key=key)]


def is_confident(ranked, title, year):
    if not ranked:
        return False
    top = ranked[0]
    exact = norm(top.get("title")) == norm(title) or norm(top.get("originalTitle")) == norm(title)
    if year is not None:
        return exact and top.get("year") == year
    same = [r for r in ranked if norm(r.get("title")) == norm(title)]
    return exact and len(same) == 1


# --- fallback: grab the best release the profile rejected only for "soft" reasons ----------

# Custom formats that mark a release as lower quality but still playable on the PS5.
SOFT_FORMATS = {"LQ", "LQ (Release Title)", "Upscaled"}
# Qualities outside a typical profile that are still fine as a last resort: SD sources and TV
# captures. Remux, BR-DISK, Raw-HD and pre-release sources (CAM, TELESYNC...) are never used.
FALLBACK_QUALITIES = {"DVD", "DVD-R", "SDTV", "WEBDL-480p", "WEBRip-480p", "Bluray-480p",
                      "Bluray-576p", "HDTV-720p", "HDTV-1080p", "HDTV-2160p"}
MAX_FALLBACK_BYTES = 40 * 1024 ** 3
SOFT_REJECTION = re.compile(
    r"is (smaller|larger) than (minimum|maximum) allowed|not wanted in profile|"
    r"Custom Formats .* below|below Movie's profile minimum", re.I)


def fallback_candidates(releases, format_scores, hard_score=-10000):
    """Releases Radarr rejected only for soft reasons, best first.

    Hard (never grabbed): any custom format scored <= hard_score that is not in SOFT_FORMATS
    (Hi10P, DV without fallback, MULTi, not English, ...), qualities outside the profile's
    allowance and FALLBACK_QUALITIES (CAM, TELESYNC, Remux...), files over 40 GB, unknown movie, blocklisted, already queued,
    torrents with no seeders. Soft: size limits, LQ/Upscaled formats, an SD quality.
    """
    out = []
    for r in releases:
        if not r.get("rejected"):
            continue
        cfs = {c.get("name") for c in r.get("customFormats") or []}
        hard_cfs = {c for c in cfs if format_scores.get(c, 0) <= hard_score and c not in SOFT_FORMATS}
        if hard_cfs:
            continue
        if any(not SOFT_REJECTION.search(x) for x in r.get("rejections") or []):
            continue
        q = ((r.get("quality") or {}).get("quality") or {})
        rej = " ".join(r.get("rejections") or [])
        if "not wanted in profile" in rej and q.get("name") not in FALLBACK_QUALITIES:
            continue
        if q.get("modifier") in ("Remux", "Brdisk", "Rawhd") or "remux" in (q.get("name") or "").lower():
            continue
        if (r.get("size") or 0) > MAX_FALLBACK_BYTES:
            continue
        if r.get("protocol") == "torrent" and not r.get("seeders"):
            continue
        out.append(r)

    def key(r):
        q = r["quality"]["quality"]
        cfs = {c.get("name") for c in r.get("customFormats") or []}
        res = q.get("resolution") or 0
        # HD beats SD; within HD a clean release beats a low-quality one before 4K beats 1080p.
        return ("Upscaled" in cfs, -min(res, 1080), bool(cfs & SOFT_FORMATS), -res,
                -(r.get("seeders") or 0))
    return sorted(out, key=key)


def why_soft(r):
    reasons = [x for x in r.get("rejections") or []]
    return "; ".join(x[:70] for x in reasons) or "rejected"


def grabbed_since(radarr, movie_id, since):
    for h in radarr.call("/history/movie?movieId=%d" % movie_id) or []:
        if h.get("eventType") == "grabbed" and h.get("date", "") >= since:
            return h
    return None


def fallback(radarr, movie_id, pid, a):
    """Wait for Radarr's own search; if it grabbed nothing, take the best soft-rejected release."""
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 5))
    deadline = time.time() + a.fallback_wait
    while time.time() < deadline:
        time.sleep(min(15, max(1, deadline - time.time())))
        h = grabbed_since(radarr, movie_id, since)
        if h:
            print("Grabbed: %s" % h.get("sourceTitle"), flush=True)
            return
    profile = radarr.call("/qualityprofile/%d" % pid)
    scores = {f["name"]: f["score"] for f in profile.get("formatItems", [])}
    releases = radarr.call("/release?movieId=%d" % movie_id, timeout=300) or []
    if grabbed_since(radarr, movie_id, since):
        print("Grabbed by Radarr", flush=True)
        return
    # Interactive search can see releases the automatic search missed; Radarr returns them in
    # its own preference order, so the first accepted one is what Radarr would pick.
    accepted = [r for r in releases if not r.get("rejected") and r.get("downloadAllowed", True)]
    for r in accepted[:3]:
        if try_grab(radarr, r, "matches %s" % a.profile):
            return
    cands = fallback_candidates(releases, scores)
    for r in cands[:3]:
        if try_grab(radarr, r, "fallback outside %s: %s" % (a.profile, why_soft(r))):
            return
    print("No PS5-safe release could be grabbed (%d checked); Radarr keeps watching for new ones"
          % len(releases), flush=True)


def try_grab(radarr, r, why):
    """Push one search result to the download client; False if the indexer link is dead."""
    print("Grabbing %s [%s, %s seeders] (%s)" % (r["title"], r["quality"]["quality"]["name"],
                                                 r.get("seeders"), why), flush=True)
    try:
        radarr.call("/release", "POST", {"guid": r["guid"], "indexerId": r["indexerId"]},
                    timeout=120, retries=0)
        return True
    except SystemExit as e:
        print("  failed: %s" % str(e).splitlines()[0][:150], flush=True)
        return False


def describe(r):
    return "%s (%s)  tmdb:%s%s" % (r.get("title"), r.get("year"), r.get("tmdbId"),
                                   "  [in library]" if r.get("id") else "")


def main(argv=None):
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    p.add_argument("query", help='movie title, optionally with a year: "Heat (1995)"')
    p.add_argument("--profile", default=DEFAULT_PROFILE, help="quality profile name")
    p.add_argument("--root", help="root folder path (default: Radarr's first root folder)")
    p.add_argument("--pick", type=int, help="choose candidate N from the list (1-based)")
    p.add_argument("--no-search", action="store_true", help="add without searching")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--env-file", help="file with RADARR_URL / RADARR_API_KEY lines")
    p.add_argument("--wait", action="store_true",
                   help="block until Radarr has imported a file for the movie")
    p.add_argument("--wait-hours", type=float, default=48, help="give up waiting after this (default 48)")
    p.add_argument("--poll", type=int, default=300, help="seconds between checks while waiting")
    p.add_argument("--no-fallback", action="store_true",
                   help="never grab a release outside the profile")
    p.add_argument("--fallback-wait", type=int, default=90,
                   help="seconds to let Radarr's own search grab before falling back (default 90)")
    p.add_argument("--on-import", metavar="CMD",
                   help="shell command run once the movie has a file (implies --wait); gets "
                        "MOVIE_TITLE, MOVIE_YEAR, MOVIE_PATH, MOVIE_FOLDER, MOVIE_FILE in its env")
    a = p.parse_args(argv)

    if a.env_file:
        load_env(a.env_file)
    url, key = os.environ.get("RADARR_URL"), os.environ.get("RADARR_API_KEY")
    if not url or not key:
        p.error("set RADARR_URL and RADARR_API_KEY (or pass --env-file)")
    radarr = Radarr(url, key)

    profiles = {q["name"]: q["id"] for q in radarr.call("/qualityprofile")}
    if a.profile not in profiles:
        raise SystemExit("quality profile %r not found; have: %s" % (a.profile, ", ".join(profiles)))
    pid = profiles[a.profile]

    title, year = parse_query(a.query)
    results = radarr.call("/movie/lookup?term=" + urllib.parse.quote(title))
    ranked = rank(results or [], title, year)[:8]
    if not ranked:
        print("No match for %r" % a.query)
        return 2
    if a.pick:
        if not 1 <= a.pick <= len(ranked):
            raise SystemExit("--pick must be 1..%d" % len(ranked))
        movie = ranked[a.pick - 1]
    elif is_confident(ranked, title, year):
        movie = ranked[0]
    else:
        print("Several matches for %r; rerun with --pick N or add the year:" % a.query)
        for i, r in enumerate(ranked, 1):
            print("  %d. %s" % (i, describe(r)))
        return 2

    if movie.get("id"):  # already in the library
        current = radarr.call("/movie/%d" % movie["id"])
        changes = []
        if current["qualityProfileId"] != pid:
            changes.append("profile -> %s" % a.profile)
        if not current["monitored"]:
            changes.append("monitored")
        search = not current.get("hasFile") and not a.no_search
        print("%s is already in Radarr%s%s" % (describe(movie), "; " + ", ".join(changes) if changes else "",
                                              "; searching" if search else ""))
        if a.dry_run:
            return 0
        if changes:
            current["qualityProfileId"], current["monitored"] = pid, True
            radarr.call("/movie/%d" % movie["id"], "PUT", current)
        if search:
            radarr.call("/command", "POST", {"name": "MoviesSearch", "movieIds": [movie["id"]]})
            if not a.no_fallback:
                fallback(radarr, movie["id"], pid, a)
        return after(radarr, movie["id"], a)

    roots = radarr.call("/rootfolder")
    root = a.root or (roots[0]["path"] if roots else None)
    if not root:
        raise SystemExit("no root folder configured in Radarr")
    body = dict(movie)
    body.update({"qualityProfileId": pid, "rootFolderPath": root, "monitored": True,
                 "minimumAvailability": "released",
                 "addOptions": {"searchForMovie": not a.no_search, "monitor": "movieOnly"}})
    print("Adding %s with profile %s to %s%s" % (describe(movie), a.profile, root,
                                                 "" if a.no_search else ", searching"))
    if a.dry_run:
        return 0
    added = radarr.call("/movie", "POST", body)
    print("Added: id %s, path %s" % (added.get("id"), added.get("path")), flush=True)
    if not a.no_search and not a.no_fallback:
        fallback(radarr, added["id"], pid, a)
    return after(radarr, added["id"], a)


def after(radarr, movie_id, a):
    """Optionally wait for the import and run the --on-import hook."""
    if not (a.wait or a.on_import):
        return 0
    deadline = time.time() + a.wait_hours * 3600
    while True:
        m = radarr.call("/movie/%d" % movie_id)
        if m.get("hasFile"):
            break
        if time.time() >= deadline:
            print("Gave up: no file imported for %s after %.0f h" % (m.get("title"), a.wait_hours))
            return 3
        time.sleep(a.poll)
    mf = m.get("movieFile") or {}
    print("Imported: %s" % (mf.get("relativePath") or m.get("path")), flush=True)
    if not a.on_import:
        return 0
    env = dict(os.environ, MOVIE_TITLE=str(m.get("title")), MOVIE_YEAR=str(m.get("year")),
               MOVIE_PATH=m.get("path", ""), MOVIE_FOLDER=os.path.basename(m.get("path", "").rstrip("/")),
               MOVIE_FILE=mf.get("relativePath", ""))
    rc = subprocess.call(a.on_import, shell=True, env=env)
    print("on-import exited %d" % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
