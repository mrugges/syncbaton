from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from syncbaton.config import FolderConfig
from syncbaton.projects import (
    list_project_names,
    matches_any,
    paths_under,
    resolve_project,
    scan_conflicts,
)


@pytest.fixture
def folder(tmp_path: Path) -> FolderConfig:
    (tmp_path / "Song A.logicx" / "Alternatives").mkdir(parents=True)
    (tmp_path / "Song B.logicx").mkdir()
    (tmp_path / "Loop.wav").write_text("x")
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "Demo.logicx").write_text("a file project")
    (tmp_path / "Song A.sync-conflict-20260102-030405-ABCDEFG.logicx").mkdir()
    return FolderConfig(id="music", path=tmp_path, patterns=("*.logicx",))


def test_lists_matching_top_level_entries_sorted_without_conflict_copies(folder):
    assert list_project_names(folder) == ["Demo.logicx", "Song A.logicx", "Song B.logicx"]


def test_does_not_look_inside_subfolders(folder):
    (folder.path / "Archive").mkdir()
    (folder.path / "Archive" / "Old.logicx").mkdir()
    assert "Old.logicx" not in list_project_names(folder)


def test_multiple_patterns(folder):
    two = FolderConfig(id="m", path=folder.path, patterns=("*.logicx", "*.wav"))
    assert "Loop.wav" in list_project_names(two)


@pytest.mark.parametrize(
    "name",
    [
        "..",
        ".",
        "",
        "../etc",
        "a/b",
        "Song A.logicx/Alternatives",
        "/etc/passwd",
        "nope.logicx",
        "notes.txt",
        "Song\0A.logicx",
        "Song A.sync-conflict-20260102-030405-ABCDEFG.logicx",
    ],
)
def test_resolve_rejects(folder, name):
    assert resolve_project(folder, name) is None


def test_resolve_accepts_existing_match(folder):
    assert resolve_project(folder, "Song A.logicx") == "Song A.logicx"


def test_resolve_missing_folder_dir_is_none(tmp_path):
    gone = FolderConfig(id="x", path=tmp_path / "gone", patterns=("*",))
    assert resolve_project(gone, "a") is None
    assert list_project_names(gone) == []


@given(st.text(max_size=30))
def test_property_resolved_names_are_single_listed_segments(tmp_path_factory, name):
    root = tmp_path_factory.mktemp("f")
    (root / "P.logicx").mkdir(exist_ok=True)
    cfg = FolderConfig(id="m", path=root, patterns=("*.logicx",))
    got = resolve_project(cfg, name)
    if got is not None:
        assert got == name
        assert "/" not in got
        assert got in list_project_names(cfg)


@given(st.sampled_from(["../", "/", "a/../", "./"]), st.text(max_size=10))
def test_property_traversal_shapes_never_resolve(tmp_path_factory, prefix, tail):
    root = tmp_path_factory.mktemp("f")
    (root / "P.logicx").mkdir(exist_ok=True)
    cfg = FolderConfig(id="m", path=root, patterns=("*",))
    assert resolve_project(cfg, prefix + tail + "/P.logicx") is None


def test_matches_any_is_case_sensitive_glob():
    assert matches_any("a.logicx", ("*.logicx",))
    assert not matches_any("a.LOGICX", ("*.logicx",))
    assert not matches_any("a.logicx.bak", ("*.logicx",))


NEED = [
    "Song A.logicx",
    "Song A.logicx/Alternatives/x",
    "Song A.logicx.bak",
    "Song AB.logicx/x",
    "Other/Song A.logicx/x",
    "song a.logicx/x",
]


def test_paths_under_matches_name_or_name_slash_prefix_only():
    assert paths_under(NEED, "Song A.logicx") == ["Song A.logicx", "Song A.logicx/Alternatives/x"]


PATH_TEXT = st.text(alphabet="ab/", min_size=1, max_size=6)


@given(st.lists(PATH_TEXT), PATH_TEXT)
def test_property_paths_under_is_exact_prefix_filter(paths, name):
    got = paths_under(paths, name)
    assert all(p == name or p.startswith(name + "/") for p in got)
    assert [p for p in paths if p == name or p.startswith(name + "/")] == got


def test_scan_conflicts_finds_nested_and_top_level(folder):
    nested = (
        folder.path / "Song A.logicx" / "Alternatives" / "x.sync-conflict-20260102-030405-AAA.wav"
    )
    nested.write_text("x")
    (folder.path / "Loop.sync-conflict-20260102-030405-AAA.wav").write_text("not a project")
    hits = scan_conflicts(folder)
    assert sorted((h.project, h.path) for h in hits) == [
        ("Song A.logicx", "Song A.logicx/Alternatives/x.sync-conflict-20260102-030405-AAA.wav"),
        ("Song A.logicx", "Song A.sync-conflict-20260102-030405-ABCDEFG.logicx"),
    ]


def test_scan_does_not_follow_symlinks(folder, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "z.sync-conflict-20260102-030405-AAA.wav").write_text("x")
    (folder.path / "Song B.logicx" / "link").symlink_to(outside)
    assert all("link" not in h.path for h in scan_conflicts(folder))
