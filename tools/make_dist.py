"""Build the v5 distribution archives under dist/.

Usage:  python tools/make_dist.py 20261031-v5
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
SPEC = Path(r"D:\ASM\宇宙语.txt")

SOURCE_EXCLUDED_SUFFIXES = {".bak", ".pyc", ".c", ".exe", ".o", ".zip", ".msi"}


def copy_tree(source: Path, destination: Path, pattern: str) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for item in sorted(source.glob(pattern)):
        if not item.is_file():
            continue
        if item.suffix.lower() in SOURCE_EXCLUDED_SUFFIXES:
            continue
        shutil.copy2(item, destination / item.name)


def build_source_archive(tag: str, stage: Path) -> Path:
    base = stage / "CosmicInterpreter-source"
    base.mkdir(parents=True)
    shutil.copy2(ROOT / "README.md", base / "README.md")
    shutil.copy2(ROOT / "requirements-build.txt", base / "requirements-build.txt")
    if not SPEC.is_file():
        raise SystemExit(f"language spec not found: {SPEC}")
    shutil.copy2(SPEC, base / "CosmicASM-language-spec.txt")
    copy_tree(ROOT / "cosmic_interpreter", base / "cosmic_interpreter", "*.py")
    copy_tree(ROOT / "examples", base / "examples", "*.cos")
    copy_tree(ROOT / "packaging", base / "packaging", "*")
    copy_tree(ROOT / "tests", base / "tests", "*.py")
    archive = DIST / f"CosmicInterpreter-source-{tag}.zip"
    write_archive(base, archive)
    return archive


def build_binary_archive(tag: str, stage: Path) -> Path:
    base = stage / "CosmicASM-Windows-x64"
    base.mkdir(parents=True)
    shutil.copy2(ROOT / "README.md", base / "README.md")
    shutil.copy2(SPEC, base / "CosmicASM-language-spec.txt")
    for name in ("CosmicInterpreter.exe", "CosmicCompiler.exe", "CosmicAsm.msi"):
        candidate = DIST / name
        if not candidate.is_file():
            raise SystemExit(f"missing build output: {candidate}")
        shutil.copy2(candidate, base / name)
    archive = DIST / f"CosmicInterpreter-Windows-x64-{tag}.zip"
    write_archive(base, archive)
    return archive


def write_archive(base: Path, archive: Path) -> None:
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(base.rglob("*")):
            if path.is_file():
                handle.write(path, path.relative_to(base.parent).as_posix())
    print(f"wrote {archive.name} ({archive.stat().st_size / (1024 * 1024):.2f} MiB)")


def main() -> None:
    tag = sys.argv[1] if len(sys.argv) > 1 else "20261031-v5"
    with tempfile.TemporaryDirectory() as temporary:
        stage = Path(temporary)
        source = build_source_archive(tag, stage)
        binary = build_binary_archive(tag, stage)
    for archive in (source, binary):
        with zipfile.ZipFile(archive) as handle:
            print(f"  {archive.name}: {len(handle.namelist())} entries")


if __name__ == "__main__":
    main()
