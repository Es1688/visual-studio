"""project.py: scaffold, active-project marker, name sanitization, input guards."""

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import project  # noqa: E402
import studio_common as sc  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture()
def staged(tmp_path, monkeypatch):
    """Temporary repo root: real schema/ (validate_manifest needs it)."""
    shutil.copytree(REPO / "schema", tmp_path / "schema")
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    return tmp_path


def test_new_scaffolds_and_activates(staged):
    assert project.main(["new", "alpha"]) == 0
    root = staged / "projects" / "alpha"
    for sub in ("references", "generated", "working", "exports", "templates"):
        assert (root / sub).is_dir(), sub
    assert (root / "brief.md").is_file()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest == {"project": "alpha", "entries": []}
    assert sc.active_project() == "alpha"


def test_new_rejects_existing_project(staged, capsys):
    project.main(["new", "alpha"])
    assert project.main(["new", "alpha"]) == 2
    assert "already exists" in capsys.readouterr().err


@pytest.mark.parametrize("bad_name", ["../evil", "a/b", "..", ".hidden", "a b"])
def test_new_rejects_bad_names(staged, bad_name):
    assert project.main(["new", bad_name]) == 2
    assert not (staged / "projects" / "evil").exists()


def test_use_unknown_project_fails(staged, capsys):
    assert project.main(["use", "ghost"]) == 2
    assert sc.active_project() is None


def test_use_activates_existing(staged):
    (staged / "projects" / "beta").mkdir(parents=True)
    assert project.main(["use", "beta"]) == 0
    assert sc.active_project() == "beta"


def test_off_clears_marker(staged):
    project.main(["new", "alpha"])
    assert project.main(["off"]) == 0
    assert sc.active_project() is None
    assert project.main(["status"]) == 0


def test_status_without_marker(staged, capsys):
    project.main(["status"])
    assert "no active project" in capsys.readouterr().out


def test_project_dir_rejects_traversal(staged):
    (staged / "projects" / "alpha").mkdir(parents=True)
    with pytest.raises(SystemExit):
        sc.project_dir("../evil")
    with pytest.raises(SystemExit):
        sc.project_dir("/etc")


def test_ensure_project_input_containment(staged):
    alpha = staged / "projects" / "alpha"
    for sub in ("references", "templates"):
        sub_dir = alpha / sub
        sub_dir.mkdir(parents=True)
        (sub_dir / "f.png").touch()
    (staged / "projects" / "beta").mkdir()
    (staged / "projects" / "beta" / "f.png").touch()
    (staged / "templates").mkdir()
    (staged / "templates" / "banner.html").touch()

    ok = sc.ensure_project_input("alpha", "projects/alpha/references/f.png", "--mask")
    assert ok == (alpha / "references" / "f.png").resolve()

    with pytest.raises(SystemExit):
        sc.ensure_project_input("alpha", "projects/beta/f.png", "--mask")
    with pytest.raises(SystemExit):
        sc.ensure_project_input("alpha", str(staged / "projects" / "beta" / "f.png"), "--data")
    with pytest.raises(SystemExit):
        sc.ensure_project_input("alpha", "../beta/f.png", "--csv")

    shared = sc.ensure_project_input(
        "alpha", "templates/banner.html", "--template", shared=("templates",)
    )
    assert shared == (staged / "templates" / "banner.html").resolve()
    with pytest.raises(SystemExit):  # shared root is not writable storage for data
        sc.ensure_project_input("alpha", "templates/banner.html", "--data")
