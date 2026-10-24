#!/usr/bin/env python3
"""add_movie: look a movie up in Radarr and add it with a PS5-safe quality profile.

    add_movie.py "The Matrix"            # best match, add, start a search
    add_movie.py "Dune (2021)"           # a year in parentheses pins the match
    add_movie.py "Dune" --pick 2         # choose from the printed candidates
    add_movie.py "Dune" --dry-run        # show what would happen

Config: RADARR_URL and RADARR_API_KEY from the environment or --env-file (KEY=VALUE lines).
If the movie is already in Radarr, it is switched to the profile, monitored, and searched
if it has no file. Exit codes: 0 added/updated, 2 ambiguous or not found, 1 error.
Stdlib only, Python 3.8+.
"""
import argparse
import json
import os
import re
import sys
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

    def call(self, path, method="GET", body=None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "X-Api-Key": self.key, "Content-Type": "application/json"})
        try:
            raw = urllib.request.urlopen(req, timeout=60).read()
        except urllib.error.HTTPError as e:
            raise SystemExit("Radarr %s %s -> HTTP %s: %s" % (
                method, path, e.code, e.read().decode("utf-8", "replace")[:300]))
        return json.loads(raw) if raw else None


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
        return 0

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
    print("Added: id %s, path %s" % (added.get("id"), added.get("path")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
