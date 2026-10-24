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
