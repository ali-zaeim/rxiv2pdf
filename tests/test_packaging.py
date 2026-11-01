"""Packaging metadata stays consistent with the code."""

import tomllib
from pathlib import Path

import rxiv2pdf

ROOT = Path(__file__).parent.parent
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_version_comes_from_the_installed_metadata():
    assert rxiv2pdf.__version__ == PROJECT["version"]


def test_license_files_cover_the_bundled_fonts():
    files = [p for pattern in PROJECT["license-files"] for p in ROOT.glob(pattern)]
    assert {p.name for p in files} == {"LICENSE", "OFL-SourceSans3.md", "OFL-SourceSerif4.md"}
    fonts = {p.stem.split("-")[0] for p in (ROOT / "src/rxiv2pdf/fonts").glob("*.ttf")}
    assert fonts == {"SourceSans3", "SourceSerif4SmText"}  # every bundled family has its OFL text
    assert "OFL-1.1" in PROJECT["license"]
