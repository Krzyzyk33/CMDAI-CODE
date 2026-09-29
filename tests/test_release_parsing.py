"""Group A - version tag parsing and ordering.

The whole in-app changelog rests on these three functions, so the odd tags get
pinned here rather than discovered later against a live GitHub response.
"""

import pytest

from cmdai.core.releases import (
    _suffix_rank,
    _tag_sort_key,
    is_included,
    is_newer,
    parse_tag,
)


@pytest.mark.parametrize("tag,expected", [
    ("v3.0.3-alpha", (3, 0, 3, "alpha")),
    ("v3.0-alpha", (3, 0, 0, "alpha")),
    ("v3.0.0-alpha", (3, 0, 0, "alpha")),
    ("v3.0", (3, 0, 0, "")),
    ("3.1.4", (3, 1, 4, "")),
    ("v10.0.0-rc1", (10, 0, 0, "rc1")),
    ("V3.0.2-Alpha", (3, 0, 2, "alpha")),
    ("v3.0.0-alpha.2", (3, 0, 0, "alpha.2")),
    ("garbage", (0, 0, 0, "")),
    ("", (0, 0, 0, "")),
])
def test_parse_tag(tag, expected):
    assert parse_tag(tag) == expected


def test_parse_tag_tolerates_whitespace():
    assert parse_tag("  v3.0.3-alpha  ") == (3, 0, 3, "alpha")


@pytest.mark.parametrize("suffix,rank", [
    ("alpha", 1),
    ("beta", 2),
    ("rc", 3),
    ("rc1", 3),
    ("", 99),
    ("final", 99),
    ("stable", 99),
    ("somethingelse", 0),
])
def test_suffix_rank(suffix, rank):
    assert _suffix_rank(suffix) == rank


def test_unsuffixed_tag_outranks_prerelease_of_the_same_version():
    """A plain v3.0.0 must count as newer than v3.0.0-alpha.

    This is the case that decides whether a final release appears in the list
    at all, and it is the one place a naive string compare gets it backwards.
    """
    assert _suffix_rank("") > _suffix_rank("alpha")


@pytest.mark.parametrize("tag,included", [
    ("v2.9.9", False),
    ("v2.9.9-alpha", False),
    ("v3.0-alpha", True),
    ("v3.0.0-alpha", True),
    ("v3.0.1-alpha", True),
    ("v3.0.3-alpha", True),
    ("v3.0-beta", True),
    ("v3.0.0-rc1", True),
    # A bare v3.0 parses to (3, 0, 0, "") and the empty suffix ranks as a
    # final release, so it belongs in the list.
    ("v3.0", True),
    ("v3.1.0", True),
    ("v10.0.0", True),
    # Same numeric version, suffix nobody recognises ranks below alpha.
    ("v3.0.0-zzz", False),
    ("v2.0.0", False),
    ("garbage", False),
])
def test_is_included(tag, included):
    assert is_included(tag) is included


@pytest.mark.parametrize("a,b,expected", [
    # The trap: string compare puts v3.0.10 before v3.0.9.
    ("v3.0.10", "v3.0.9", True),
    ("v3.0.9", "v3.0.10", False),
    ("v3.1.0", "v3.0.99", True),
    ("v3.0.3-alpha", "v3.0.2-alpha", True),
    ("v3.0.3-alpha", "v3.0.3-alpha", False),
    ("v3.0.3", "v3.0.3-alpha", True),
    ("v3.0.3-alpha", "v3.0.3", False),
    ("v3.0.3-beta", "v3.0.3-alpha", True),
    # An unknown tag must never win over a real one.
    ("v3.0.3", "v3.0.3-zzz", True),
    # Missing side: a real tag beats nothing, nothing never beats a real tag.
    ("v3.0.3", "", True),
    ("", "v3.0.3", False),
    ("", "", False),
])
def test_is_newer(a, b, expected):
    assert is_newer(a, b) is expected


def test_is_newer_agrees_with_sort_order():
    """is_newer must be exactly the sort key, so callers cannot disagree."""
    tags = ["v3.0.0-zzz", "v3.0.10", "v3.0.9", "v3.0.3-alpha", "v3.0.3", "v3.1.0"]
    ordered = sorted(tags, key=_tag_sort_key)
    # Numeric before the suffix rank: 3.0.3 sorts below 3.0.9 because 3 < 9,
    # and 3.0.10 above it because 10 > 9, which is the whole point.
    assert ordered == [
        "v3.0.0-zzz", "v3.0.3-alpha", "v3.0.3", "v3.0.9", "v3.0.10", "v3.1.0",
    ]

    # Every pair, in both directions, must agree with the sort key. Picking
    # between the bundled release and a freshly fetched one is exactly this
    # comparison, and the two must never disagree.
    for a in tags:
        for b in tags:
            if a == b:
                continue
            assert is_newer(a, b) is (_tag_sort_key(a) > _tag_sort_key(b)), (a, b)
