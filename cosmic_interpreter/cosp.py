from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?\Z")
_MANIFEST = "cosp.toml"
_LOCK = "cosp.lock"
_MAX_ARCHIVE_MEMBERS = 10000
_MAX_ARCHIVE_SIZE = 256 * 1024 * 1024
_BUILTIN_TEMPLATES = {
    "app": {
        "main.cos": 'var greeting = {"Hello from Cosmic ASM"}\nprint (<{greeting}>)\n',
        "README.md": "# ${project_name}\n\nA Cosmic ASM project.\n",
    },
    "library": {
        "lib.cos": "# ${project_name}\n",
        "README.md": "# ${project_name}\n\nA reusable Cosmic ASM package.\n",
    },
}


class CospError(Exception):
    pass


def _strip_toml_comment(line: str) -> str:
    quoted = False
    escaped = False
    for index, character in enumerate(line):
        if escaped:
            escaped = False
        elif character == "\\" and quoted:
            escaped = True
        elif character == '"':
            quoted = not quoted
        elif character == "#" and not quoted:
            return line[:index]
    return line


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _parse_manifest(path: Path) -> dict[str, object]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise CospError(f"cannot read {path}: {error}") from error

    section = ""
    package: dict[str, str] = {}
    dependencies: dict[str, str] = {}
    for line_number, raw in enumerate(lines, 1):
        line = _strip_toml_comment(raw).strip()
        if not line:
            continue
        section_match = re.fullmatch(r"\[([a-z]+)\]", line)
        if section_match:
            section = section_match.group(1)
            if section not in {"package", "dependencies"}:
                raise CospError(f"{path}:{line_number}: unsupported section [{section}]")
            continue
        match = re.fullmatch(r'([A-Za-z][A-Za-z0-9_-]*)\s*=\s*"((?:[^"\\]|\\.)*)"', line)
        if not match:
            raise CospError(f"{path}:{line_number}: expected key = \"string\"")
        key, encoded_value = match.groups()
        try:
            value = json.loads(f'"{encoded_value}"')
        except json.JSONDecodeError as error:
            raise CospError(f"{path}:{line_number}: invalid string value") from error
        target = package if section == "package" else dependencies if section == "dependencies" else None
        if target is None:
            raise CospError(f"{path}:{line_number}: key outside a section")
        if key in target:
            raise CospError(f"{path}:{line_number}: duplicate key '{key}'")
        target[key] = value

    name = package.get("name", "")
    version = package.get("version", "")
    entry = package.get("entry", "main.cos")
    if not _NAME_RE.fullmatch(name):
        raise CospError("package.name must start with a letter and contain only letters, digits, '_' or '-'")
    if not _VERSION_RE.fullmatch(version):
        raise CospError("package.version must be a semantic version such as 0.1.0")
    entry_path = Path(entry)
    if entry_path.is_absolute() or ".." in entry_path.parts:
        raise CospError("package.entry must be a project-relative path")
    for dependency in dependencies:
        if not _NAME_RE.fullmatch(dependency):
            raise CospError(f"invalid dependency name '{dependency}'")
    return {"package": {"name": name, "version": version, "entry": entry}, "dependencies": dependencies}


def _write_manifest(root: Path, manifest: dict[str, object]) -> None:
    package = manifest["package"]
    dependencies = manifest["dependencies"]
    if not isinstance(package, dict) or not isinstance(dependencies, dict):
        raise CospError("invalid project manifest structure")
    lines = ["[package]"]
    for key in ("name", "version", "entry"):
        if key in package:
            lines.append(f"{key} = {_quote(str(package[key]))}")
    lines.extend(["", "[dependencies]"])
    for name, location in sorted(dependencies.items()):
        lines.append(f"{name} = {_quote(str(location))}")
    manifest_path = root / _MANIFEST
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", dir=root,
        prefix=".cosp.toml-", delete=False,
    ) as temporary:
        temporary.write("\n".join(lines) + "\n")
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, manifest_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _content_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative_path = path.relative_to(root)
        if any(part in {".git", ".cosp", "__pycache__"} for part in relative_path.parts):
            continue
        relative = relative_path.as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _safe_extract(archive: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if len(members) > _MAX_ARCHIVE_MEMBERS or sum(member.file_size for member in members) > _MAX_ARCHIVE_SIZE:
            raise CospError("archive exceeds the extraction limits")
        for member in members:
            path = PurePosixPath(member.filename)
            if path.is_absolute() or any(part in {"..", ""} for part in path.parts):
                raise CospError(f"unsafe archive member: {member.filename}")
            target = (destination / Path(*path.parts)).resolve()
            if not target.is_relative_to(destination_resolved):
                raise CospError(f"unsafe archive member: {member.filename}")
        source.extractall(destination)


def _dependency_source(root: Path, value: str) -> tuple[Path, bool]:
    source = (root / value).resolve()
    if not source.exists():
        raise CospError(f"dependency source does not exist: {value}")
    if source.is_dir():
        return source, True
    if source.is_file() and zipfile.is_zipfile(source):
        return source, False
    raise CospError(f"dependency must be a local directory or ZIP archive: {value}")


def _install(root: Path, manifest: dict[str, object], *, refresh: bool = False) -> None:
    dependencies = manifest["dependencies"]
    if not isinstance(dependencies, dict):
        raise CospError("invalid project manifest dependencies")
    cosp_root = root / ".cosp"
    cosp_root.mkdir(parents=True, exist_ok=True)
    package_root = cosp_root / "packages"
    existing_lock: dict[str, object] = {}
    lock_path = root / _LOCK
    if lock_path.exists():
        try:
            existing_lock = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CospError(f"cannot read {_LOCK}: {error}") from error
    previous_packages = existing_lock.get("packages", {})
    if not isinstance(previous_packages, dict):
        raise CospError(f"invalid {_LOCK}: packages must be an object")

    locked: dict[str, dict[str, str]] = {}
    with tempfile.TemporaryDirectory(prefix="stage-", dir=cosp_root) as temporary:
        staged_root = Path(temporary)
        staged_packages = staged_root / "packages"
        staged_packages.mkdir()
        for name, value in sorted(dependencies.items()):
            source, is_directory = _dependency_source(root, str(value))
            target = staged_packages / name
            target.mkdir()
            if is_directory:
                shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", ".cosp", "__pycache__"))
            else:
                _safe_extract(source, target)
                entries = list(target.iterdir())
                if len(entries) == 1 and entries[0].is_dir():
                    nested = entries[0]
                    for child in list(nested.iterdir()):
                        shutil.move(str(child), target / child.name)
                    nested.rmdir()
            package_manifest_path = target / _MANIFEST
            if package_manifest_path.exists():
                package_manifest = _parse_manifest(package_manifest_path)
                package_info = package_manifest["package"]
                if not isinstance(package_info, dict):
                    raise CospError(f"invalid package manifest for '{name}'")
                if package_info["name"] != name:
                    raise CospError(f"dependency '{name}' declares package name '{package_info['name']}'")
                version = str(package_info["version"])
            else:
                version = "0.0.0"
            package_lock = {"source": str(value), "version": version, "sha256": _content_hash(target)}
            old_lock = previous_packages.get(name)
            if not refresh and isinstance(old_lock, dict) and old_lock != package_lock:
                raise CospError(
                    f"dependency '{name}' differs from {_LOCK}; use cosp add to refresh its lock"
                )
            locked[name] = package_lock

        new_lock = {"lock_version": 1, "packages": locked}
        staged_lock = staged_root / _LOCK
        staged_lock.write_text(json.dumps(new_lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        backup = Path(tempfile.mkdtemp(prefix="packages-backup-", dir=cosp_root))
        backup.rmdir()
        if package_root.exists():
            os.replace(package_root, backup)
        try:
            os.replace(staged_packages, package_root)
            os.replace(staged_lock, lock_path)
        except OSError:
            if package_root.exists():
                shutil.rmtree(package_root)
            if backup.exists():
                os.replace(backup, package_root)
            raise
        if backup.exists():
            shutil.rmtree(backup)


def _render_template(name: str, target: Path, template: str | None) -> None:
    if template is None or template in _BUILTIN_TEMPLATES:
        files = _BUILTIN_TEMPLATES[template or "app"]
    else:
        template_root = Path(template).resolve()
        config_path = template_root / "template.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            files = config["files"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise CospError(f"invalid custom template '{template}': {error}") from error
        if not isinstance(files, list):
            raise CospError("custom template files must be a list of relative file paths")
        rendered: dict[str, str] = {}
        for relative in files:
            relative_path = Path(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise CospError(f"unsafe template file path: {relative}")
            source = (template_root / "files" / relative_path).resolve()
            if not source.is_relative_to((template_root / "files").resolve()) or not source.is_file():
                raise CospError(f"template file is missing or unsafe: {relative}")
            rendered[relative_path.as_posix()] = source.read_text(encoding="utf-8")
        files = rendered
    outputs: list[tuple[Path, str]] = []
    for relative, content in files.items():
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise CospError(f"unsafe template file path: {relative}")
        output = target / relative_path
        if output.exists():
            raise CospError(f"template would overwrite an existing file: {output}")
        outputs.append((output, str(content).replace("${project_name}", name)))
    if "cosp.toml" not in files:
        if (target / _MANIFEST).exists():
            raise CospError(f"template would overwrite an existing file: {target / _MANIFEST}")
        outputs.append(
            (
                target / _MANIFEST,
                "[package]\n"
                f"name = {_quote(name)}\n"
                'version = "0.1.0"\n'
                f'entry = {_quote("main.cos" if "main.cos" in files else "lib.cos")}\n'
                "\n[dependencies]\n",
            )
        )
    for output, content in outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")


def _project_root() -> Path:
    return Path.cwd()


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cosp", description="Cosmic Package Manager")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init", help="initialize a project in the current directory")
    init_parser.add_argument("--name", default=Path.cwd().name)
    new_parser = subparsers.add_parser("new", help="create a project from a scaffold")
    new_parser.add_argument("name")
    new_parser.add_argument("--template", default="app", help="built-in template name or local template directory")
    add_parser = subparsers.add_parser("add", help="add a local directory or ZIP dependency")
    add_parser.add_argument("name")
    add_parser.add_argument("path", type=Path)
    remove_parser = subparsers.add_parser("remove", help="remove a dependency")
    remove_parser.add_argument("name")
    subparsers.add_parser("install", help="install local dependencies and update cosp.lock")
    build_parser = subparsers.add_parser("build", help="compile the project's entry file")
    build_parser.add_argument("-o", "--output", type=Path)
    run_parser = subparsers.add_parser("run", help="interpret the project's entry file")
    run_parser.add_argument("args", nargs=argparse.REMAINDER)
    test_parser = subparsers.add_parser("test", help="run repository tests")
    test_parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    root = _project_root()
    try:
        if args.command == "new":
            if not _NAME_RE.fullmatch(args.name):
                raise CospError("project name must start with a letter and contain only letters, digits, '_' or '-'")
            target = root / args.name
            if target.exists():
                raise CospError(f"target already exists: {target}")
            target.mkdir(parents=True)
            _render_template(args.name, target, args.template)
            print(f"Created Cosmic project: {target}")
            return 0
        if args.command == "init":
            manifest_path = root / _MANIFEST
            if manifest_path.exists():
                raise CospError(f"{_MANIFEST} already exists")
            if not _NAME_RE.fullmatch(args.name):
                raise CospError("invalid project name")
            _render_template(args.name, root, "app")
            print(f"Initialized Cosmic project: {root}")
            return 0

        manifest = _parse_manifest(root / _MANIFEST)
        dependencies = manifest["dependencies"]
        if not isinstance(dependencies, dict):
            raise CospError("invalid project manifest dependencies")
        if args.command == "add":
            if not _NAME_RE.fullmatch(args.name):
                raise CospError("invalid dependency name")
            source = args.path.resolve()
            if not source.exists() or not (source.is_dir() or zipfile.is_zipfile(source)):
                raise CospError("dependency must be an existing local directory or ZIP archive")
            try:
                location = os.path.relpath(source, root)
            except ValueError as error:
                raise CospError("dependency must be on the same filesystem volume") from error
            dependencies[args.name] = Path(location).as_posix()
            _install(root, manifest, refresh=True)
            _write_manifest(root, manifest)
            return 0
        if args.command == "remove":
            if args.name not in dependencies:
                raise CospError(f"dependency '{args.name}' is not declared")
            del dependencies[args.name]
            _install(root, manifest, refresh=True)
            _write_manifest(root, manifest)
            shutil.rmtree(root / ".cosp" / "packages" / args.name, ignore_errors=True)
            return 0
        if args.command == "install":
            _install(root, manifest)
            print(f"Installed {len(dependencies)} local package(s)")
            return 0
        package_info = manifest["package"]
        if not isinstance(package_info, dict):
            raise CospError("invalid project manifest package section")
        entry = root / str(package_info["entry"])
        if args.command == "build":
            from .compiler import compile_source

            _, output = compile_source(entry, output_path=args.output)
            print(f"Executable: {output}")
            return 0
        if args.command == "run":
            from .cli import main as interpreter_main

            return interpreter_main([str(entry), *args.args])
        if args.command == "test":
            result = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", *args.args],
                cwd=root,
                check=False,
            )
            return result.returncode
    except (CospError, OSError, shutil.Error, zipfile.BadZipFile) as error:
        print(f"cosp: {error}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    return _main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
