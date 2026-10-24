import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "arr"))
from add_movie import parse_query, rank, is_confident  # noqa: E402


def m(title, year, tmdb=1):
    return {"title": title, "year": year, "tmdbId": tmdb}


def test_parse_query_year():
    assert parse_query("Dune (2021)") == ("Dune", 2021)
    assert parse_query("Blade Runner 2049") == ("Blade Runner 2049", None)
    assert parse_query("Heat") == ("Heat", None)


def test_rank_prefers_exact_title_and_year():
    res = [m("Dune: Part Two", 2024), m("Dune", 1984), m("Dune", 2021)]
    assert rank(res, "Dune", 2021)[0]["year"] == 2021


def test_confident_with_year():
    res = rank([m("Dune", 1984), m("Dune", 2021)], "Dune", 2021)
    assert is_confident(res, "Dune", 2021)


def test_ambiguous_without_year():
    res = rank([m("Dune", 1984), m("Dune", 2021)], "Dune", None)
    assert not is_confident(res, "Dune", None)


def test_unique_exact_title_is_confident():
    res = rank([m("The Matrix", 1999), m("The Matrix Reloaded", 2003)], "The Matrix", None)
    assert is_confident(res, "The Matrix", None)


# --- fallback ---------------------------------------------------------------------------

from add_movie import fallback_candidates  # noqa: E402

SCORES = {"LQ": -10000, "LQ (Release Title)": -10000, "Upscaled": -10000, "MULTi": -10000,
          "PS5 - x264 10bit (Hi10P)": -10000, "PS5 - XviD/DivX": -10000, "DD": 20}


def rel(title, quality, res, cfs=(), rejections=("1.5 GB is smaller than minimum allowed 1.6 GB",),
        seeders=10, rejected=True):
    return {"title": title, "rejected": rejected, "rejections": list(rejections), "seeders": seeders,
            "protocol": "torrent", "customFormats": [{"name": c} for c in cfs],
            "quality": {"quality": {"name": quality, "resolution": res}}}


def titles(rs):
    return [r["title"] for r in fallback_candidates(rs, SCORES)]


def test_halloweentown_prefers_real_1080p_over_upscale_and_dvd():
    rs = [rel("upscaled", "HDTV-1080p", 1080, ["Upscaled", "DD"],
              ["Custom Formats DD, Upscaled have score -9980 below Movie's profile minimum"]),
          rel("lama", "Bluray-1080p", 1080, ["LQ"]),
          rel("dvd", "DVD", 480, [], ["1.8 GB is larger than maximum allowed 84.0 MB",
                                      "DVD is not wanted in profile"])]
    assert titles(rs) == ["lama", "dvd", "upscaled"]


def test_never_falls_back_to_ps5_breaking_formats():
    rs = [rel("hi10p", "Bluray-1080p", 1080, ["PS5 - x264 10bit (Hi10P)"]),
          rel("multi", "Bluray-1080p", 1080, ["MULTi"]),
          rel("xvid", "DVD", 480, ["PS5 - XviD/DivX"])]
    assert titles(rs) == []


def test_never_falls_back_to_cam_unknown_or_dead_torrents():
    rs = [rel("cam", "CAM", 0, [], ["CAM is not wanted in profile"]),
          rel("unknown", "Bluray-1080p", 1080, [], ["Unknown Movie. Unable to match"]),
          rel("dead", "Bluray-1080p", 1080, ["LQ"], seeders=0),
          rel("blocked", "Bluray-1080p", 1080, [], ["Release is blocklisted"])]
    assert titles(rs) == []


def test_accepted_releases_are_not_fallbacks():
    assert titles([rel("ok", "Bluray-1080p", 1080, rejected=False, rejections=())]) == []


def test_never_falls_back_to_remux_or_huge_files():
    rs = [rel("remux", "Remux-2160p", 2160, ["LQ"], ["59.5 GB is larger than maximum allowed 8.8 GB",
                                                    "Remux-2160p is not wanted in profile"]),
          rel("ok720", "HDTV-720p", 720, [], ["HDTV-720p is not wanted in profile"])]
    assert titles(rs) == ["ok720"]


def test_clean_1080p_beats_low_quality_4k():
    rs = [rel("lq4k", "Bluray-2160p", 2160, ["LQ"]), rel("clean1080", "Bluray-1080p", 1080)]
    assert titles(rs) == ["clean1080", "lq4k"]
