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
import time
import tomllib
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?\Z")
_MANIFEST = "cosp.toml"
_LOCK = "cosp.lock"
_MAX_ARCHIVE_MEMBERS = 10000
_MAX_ARCHIVE_SIZE = 256 * 1024 * 1024
_MAX_INDEX_SIZE = 4 * 1024 * 1024
# 网关/限流类瞬时错误重试，避免代理抖动直接失败。
_RETRY_ATTEMPTS = 3
_RETRY_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
_RETRY_DELAY_SECONDS = 0.5
# 默认远端来源：本项目仓库的索引 JSON 与源码归档。
_DEFAULT_INDEX_URL = (
    "https://raw.githubusercontent.com/HUOHONG-1234/Cosmic-ASM/main/index.json"
)
_DEFAULT_ARCHIVE_URL = (
    "https://github.com/HUOHONG-1234/Cosmic-ASM/archive/refs/heads/main.zip"
)
_DEPENDENCY_TABLE_KEYS = ("url", "subdir", "version", "registry")
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


def _safe_subdir(subdir: str, label: str) -> str:
    path = PurePosixPath(subdir)
    if not subdir or path.is_absolute() or any(part in {"..", ""} for part in path.parts):
        raise CospError(f"{label} must be a relative POSIX path without '..': {subdir}")
    return path.as_posix()


def _validate_dependency(name: str, location: object) -> str | dict[str, str]:
    """依赖值可以是本地路径、HTTPS ZIP 字符串，或 `{ url, subdir, version, registry }` 表。"""
    if isinstance(location, str):
        return location
    if not isinstance(location, dict):
        raise CospError(
            f"dependency '{name}' must be a string source or a table with url/subdir/version"
        )
    unknown = set(location) - set(_DEPENDENCY_TABLE_KEYS)
    if unknown:
        raise CospError(
            f"dependency '{name}' has unsupported key '{sorted(unknown)[0]}'; "
            f"supported keys are {', '.join(_DEPENDENCY_TABLE_KEYS)}"
        )
    url = location.get("url")
    if not isinstance(url, str) or not url:
        raise CospError(f"dependency '{name}' table requires a url")
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise CospError(
            f"dependency '{name}' url must be an HTTPS URL without embedded credentials"
        )
    resolved: dict[str, str] = {"url": url}
    subdir = location.get("subdir")
    if subdir is not None:
        if not isinstance(subdir, str):
            raise CospError(f"dependency '{name}' subdir must be a string")
        resolved["subdir"] = _safe_subdir(subdir, f"dependency '{name}' subdir")
    version = location.get("version")
    if version is not None:
        if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
            raise CospError(
                f"dependency '{name}' version must be a semantic version such as 1.0.0"
            )
        resolved["version"] = version
    registry = location.get("registry")
    if registry is not None:
        if not isinstance(registry, str) or not registry:
            raise CospError(f"dependency '{name}' registry must be a URL string")
        resolved["registry"] = registry
    return resolved


def _dependency_url(location: str | dict[str, str]) -> str:
    return location["url"] if isinstance(location, dict) else location


def _dependency_subdir(location: str | dict[str, str]) -> str | None:
    return location.get("subdir") if isinstance(location, dict) else None


def _dependency_version(location: str | dict[str, str]) -> str | None:
    return location.get("version") if isinstance(location, dict) else None


def _parse_manifest(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        document = tomllib.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise CospError(f"cannot read {path}: {error}") from error
    package_value = document.get("package")
    dependencies_value = document.get("dependencies", {})
    plugin_value = document.get("plugin", {})
    if not isinstance(package_value, dict) or not isinstance(dependencies_value, dict):
        raise CospError(f"{path}: [package] and [dependencies] must be TOML tables")
    if not isinstance(plugin_value, dict):
        raise CospError(f"{path}: [plugin] must be a TOML table")
    package = package_value
    dependencies = dependencies_value
    plugin = plugin_value

    name = package.get("name", "")
    version = package.get("version", "")
    entry = package.get("entry", "main.cos")
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise CospError("package.name must start with a letter and contain only letters, digits, '_' or '-'")
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise CospError("package.version must be a semantic version such as 0.1.0")
    if not isinstance(entry, str):
        raise CospError("package.entry must be a string")
    entry_path = Path(entry)
    if entry_path.is_absolute() or ".." in entry_path.parts:
        raise CospError("package.entry must be a project-relative path")
    for dependency, location in dependencies.items():
        if not isinstance(dependency, str) or not _NAME_RE.fullmatch(dependency):
            raise CospError(f"invalid dependency name '{dependency}'")
        dependencies[dependency] = _validate_dependency(dependency, location)
    if plugin:
        plugin_type = plugin.get("type", "library")
        if plugin_type not in {"library", "app", "board"}:
            raise CospError("plugin.type must be library, app, or board")
        features = plugin.get("features", [])
        requires = plugin.get("requires", [])
        exposes = plugin.get("exposes", {})
        if not isinstance(features, list) or not all(isinstance(item, str) for item in features):
            raise CospError("plugin.features must be an array of strings")
        if not isinstance(requires, list) or not all(
            isinstance(item, str) and _NAME_RE.fullmatch(item) for item in requires
        ):
            raise CospError("plugin.requires must be an array of package names")
        if not isinstance(exposes, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            and re.fullmatch(r"[A-Za-z_]\w*", key)
            and re.fullmatch(r"[A-Za-z_]\w*", value)
            for key, value in exposes.items()
        ):
            raise CospError("plugin.exposes must map identifiers to identifiers")
        plugin = {
            "type": plugin_type,
            "features": features,
            "requires": requires,
            "exposes": exposes,
        }
    return {
        "package": {"name": name, "version": version, "entry": entry},
        "dependencies": dependencies,
        "plugin": plugin,
    }


def _write_manifest(root: Path, manifest: dict[str, object]) -> None:
    package = manifest["package"]
    dependencies = manifest["dependencies"]
    plugin = manifest.get("plugin", {})
    if not isinstance(package, dict) or not isinstance(dependencies, dict) or not isinstance(plugin, dict):
        raise CospError("invalid project manifest structure")
    lines = ["[package]"]
    for key in ("name", "version", "entry"):
        if key in package:
            lines.append(f"{key} = {_quote(str(package[key]))}")
    lines.extend(["", "[dependencies]"])
    for name, location in sorted(dependencies.items()):
        if isinstance(location, dict):
            inline = ", ".join(
                f"{key} = {_quote(str(location[key]))}"
                for key in _DEPENDENCY_TABLE_KEYS
                if key in location
            )
            lines.append(f"{name} = {{ {inline} }}")
        else:
            lines.append(f"{name} = {_quote(str(location))}")
    if plugin:
        lines.extend(["", "[plugin]"])
        lines.append(f"type = {_quote(str(plugin.get('type', 'library')))}")
        for key in ("features", "requires"):
            values = plugin.get(key, [])
            if not isinstance(values, list):
                raise CospError(f"plugin.{key} must be an array")
            lines.append(f"{key} = [{', '.join(_quote(str(value)) for value in values)}]")
        exposes = plugin.get("exposes", {})
        if not isinstance(exposes, dict):
            raise CospError("plugin.exposes must be a table")
        if exposes:
            lines.extend(["", "[plugin.exposes]"])
            lines.extend(f"{name} = {_quote(str(symbol))}" for name, symbol in sorted(exposes.items()))
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


def _read_lock(path: Path) -> dict[str, object]:
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CospError(f"cannot read {_LOCK}: {error}") from error
    if (
        not isinstance(lock, dict)
        or type(lock.get("lock_version")) is not int
        or lock["lock_version"] != 1
    ):
        raise CospError(f"invalid {_LOCK}: unsupported or missing lock_version")
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        raise CospError(f"invalid {_LOCK}: packages must be an object")
    for name, metadata in packages.items():
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            raise CospError(f"invalid {_LOCK}: invalid package name '{name}'")
        if not isinstance(metadata, dict):
            raise CospError(f"invalid {_LOCK}: package '{name}' must be an object")
        source = metadata.get("source")
        version = metadata.get("version")
        digest = metadata.get("sha256")
        if not isinstance(source, str) or not source:
            raise CospError(f"invalid {_LOCK}: package '{name}' has an invalid source")
        if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
            raise CospError(f"invalid {_LOCK}: package '{name}' has an invalid version")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise CospError(f"invalid {_LOCK}: package '{name}' has an invalid SHA-256")
        subdir = metadata.get("subdir")
        if subdir is not None:
            if not isinstance(subdir, str):
                raise CospError(f"invalid {_LOCK}: package '{name}' subdir must be a string")
            _safe_subdir(subdir, f"invalid {_LOCK}: package '{name}' subdir")
    return lock


class _HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        parsed = urlsplit(new_url)
        if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise CospError("remote dependencies may only redirect to HTTPS URLs")
        return super().redirect_request(request, response, code, message, headers, new_url)


def _download_https_archive(url: str, destination: Path) -> None:
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise CospError("remote dependencies must use an HTTPS URL without embedded credentials")
    opener = urllib.request.build_opener(_HTTPSRedirectHandler)
    request = urllib.request.Request(url, headers={"User-Agent": "cosp/1"})
    for attempt in range(_RETRY_ATTEMPTS):
        total = 0
        try:
            with opener.open(request, timeout=30) as response, destination.open("wb") as output:
                final_url = urlsplit(response.geturl())
                if final_url.scheme.lower() != "https":
                    raise CospError("remote dependencies must remain on HTTPS")
                while chunk := response.read(64 * 1024):
                    total += len(chunk)
                    if total > _MAX_ARCHIVE_SIZE:
                        raise CospError("remote dependency archive exceeds the download limit")
                    output.write(chunk)
            break
        except CospError:
            raise
        except urllib.error.HTTPError as error:
            if error.code in _RETRY_STATUS_CODES and attempt + 1 < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_DELAY_SECONDS * (attempt + 1))
                continue
            raise CospError(f"cannot download remote dependency: {error}") from error
        except (OSError, urllib.error.URLError) as error:
            if attempt + 1 < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_DELAY_SECONDS * (attempt + 1))
                continue
            raise CospError(f"cannot download remote dependency: {error}") from error
    if not zipfile.is_zipfile(destination):
        raise CospError("remote dependency URL must serve a ZIP archive")


def _default_index_url() -> str:
    return os.environ.get("COSMIC_COSP_INDEX") or _DEFAULT_INDEX_URL


def _fetch_https_document(url: str, *, limit: int, label: str) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise CospError(f"{label} must use an HTTPS URL without embedded credentials")
    opener = urllib.request.build_opener(_HTTPSRedirectHandler)
    request = urllib.request.Request(url, headers={"User-Agent": "cosp/1"})
    for attempt in range(_RETRY_ATTEMPTS):
        total = 0
        try:
            with opener.open(request, timeout=30) as response:
                final_url = urlsplit(response.geturl())
                if final_url.scheme.lower() != "https":
                    raise CospError(f"{label} must remain on HTTPS")
                chunks: list[bytes] = []
                while chunk := response.read(64 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise CospError(f"{label} exceeds the download limit")
                    chunks.append(chunk)
            return b"".join(chunks)
        except CospError:
            raise
        except urllib.error.HTTPError as error:
            if error.code in _RETRY_STATUS_CODES and attempt + 1 < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_DELAY_SECONDS * (attempt + 1))
                continue
            raise CospError(f"cannot download {label}: {error}") from error
        except (OSError, urllib.error.URLError) as error:
            if attempt + 1 < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_DELAY_SECONDS * (attempt + 1))
                continue
            raise CospError(f"cannot download {label}: {error}") from error
    raise CospError(f"cannot download {label}")


def _parse_index(document: object, url: str) -> dict[str, object]:
    if not isinstance(document, dict):
        raise CospError(f"invalid package index {url}: root must be an object")
    if type(document.get("index_version")) is not int or document["index_version"] != 1:
        raise CospError(f"invalid package index {url}: unsupported or missing index_version")
    archive = document.get("archive", _DEFAULT_ARCHIVE_URL)
    if not isinstance(archive, str) or not archive:
        raise CospError(f"invalid package index {url}: archive must be an HTTPS URL")
    resolved_archive = urlsplit(archive)
    if resolved_archive.scheme.lower() != "https" or not resolved_archive.hostname:
        raise CospError(f"invalid package index {url}: archive must be an HTTPS URL")
    packages = document.get("packages")
    if not isinstance(packages, dict):
        raise CospError(f"invalid package index {url}: packages must be an object")
    entries: dict[str, dict[str, str]] = {}
    for name, entry in packages.items():
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            raise CospError(f"invalid package index {url}: invalid package name '{name}'")
        if not isinstance(entry, dict):
            raise CospError(f"invalid package index {url}: package '{name}' must be an object")
        resolved: dict[str, str] = {}
        entry_url = entry.get("url", archive)
        if not isinstance(entry_url, str) or not entry_url:
            raise CospError(f"invalid package index {url}: package '{name}' url must be a string")
        parsed_entry = urlsplit(entry_url)
        if (
            parsed_entry.scheme.lower() != "https"
            or not parsed_entry.hostname
            or parsed_entry.username
            or parsed_entry.password
        ):
            raise CospError(
                f"invalid package index {url}: package '{name}' url must be an HTTPS URL"
            )
        resolved["url"] = entry_url
        subdir = entry.get("subdir")
        if subdir is not None:
            if not isinstance(subdir, str):
                raise CospError(f"invalid package index {url}: package '{name}' subdir must be a string")
            resolved["subdir"] = _safe_subdir(subdir, f"package '{name}' subdir")
        version = entry.get("version")
        if version is not None:
            if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
                raise CospError(
                    f"invalid package index {url}: package '{name}' version must be a semantic version"
                )
            resolved["version"] = version
        description = entry.get("description")
        if description is not None and not isinstance(description, str):
            raise CospError(
                f"invalid package index {url}: package '{name}' description must be a string"
            )
        entries[name] = resolved
    return {"archive": archive, "packages": entries}


def _load_index(url: str | None = None) -> tuple[str, dict[str, dict[str, str]]]:
    index_url = url or _default_index_url()
    try:
        payload = _fetch_https_document(index_url, limit=_MAX_INDEX_SIZE, label="package index")
    except CospError as error:
        hint = (
            f"; default index is {_DEFAULT_INDEX_URL}, override with --index or "
            "COSMIC_COSP_INDEX"
            if url is None and index_url == _DEFAULT_INDEX_URL
            else ""
        )
        raise CospError(f"{error}{hint}") from error
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise CospError(f"invalid package index {index_url}: {error}") from error
    parsed = _parse_index(document, index_url)
    packages = parsed["packages"]
    if not isinstance(packages, dict):
        raise CospError(f"invalid package index {index_url}: packages must be an object")
    return index_url, packages


def _resolve_registry_entry(name: str, url: str | None = None) -> dict[str, str]:
    index_url, packages = _load_index(url)
    entry = packages.get(name)
    if entry is None:
        available = ", ".join(sorted(packages)) or "(empty index)"
        raise CospError(f"package '{name}' is not listed in {index_url}; available: {available}")
    return {**entry, "registry": index_url}


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
        existing_lock = _read_lock(lock_path)
    previous_packages = existing_lock.get("packages", {})
    if not isinstance(previous_packages, dict):
        raise CospError(f"invalid {_LOCK}: packages must be an object")
    if lock_path.exists() and not refresh and set(previous_packages) != set(dependencies):
        raise CospError(
            f"dependencies in {_LOCK} do not match {_MANIFEST}; use cosp add/remove to update the lock"
        )

    locked: dict[str, dict[str, str]] = {}
    with tempfile.TemporaryDirectory(prefix="stage-", dir=cosp_root) as temporary:
        staged_root = Path(temporary)
        staged_packages = staged_root / "packages"
        staged_packages.mkdir()
        for name, value in sorted(dependencies.items()):
            dependency_value = _dependency_url(value)
            subdir = _dependency_subdir(value)
            expected_version = _dependency_version(value)
            parsed_source = urlsplit(dependency_value)
            if parsed_source.scheme:
                if parsed_source.scheme.lower() != "https":
                    raise CospError("remote dependencies must use HTTPS")
                source = staged_root / f"{name}.zip"
                _download_https_archive(dependency_value, source)
                is_directory = False
            else:
                if subdir is not None:
                    raise CospError(
                        f"dependency '{name}' subdir is only supported for remote archives"
                    )
                source, is_directory = _dependency_source(root, dependency_value)
            target = staged_packages / name
            target.mkdir()
            if is_directory:
                shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", ".cosp", "__pycache__"))
            else:
                # 先解到独立目录，再只把目标目录（默认整包，或 subdir 指定的子目录）
                # 复制进包目录；这样从整仓归档安装时不会把仓库其余内容一起铺进来。
                extract_root = staged_root / f"{name}.extract"
                extract_root.mkdir()
                _safe_extract(source, extract_root)
                entries = list(extract_root.iterdir())
                package_source = (
                    entries[0] if len(entries) == 1 and entries[0].is_dir() else extract_root
                )
                if subdir is not None:
                    package_source = package_source.joinpath(*PurePosixPath(subdir).parts)
                    if not package_source.is_dir():
                        raise CospError(
                            f"dependency '{name}' subdir '{subdir}' is missing from the archive"
                        )
                shutil.copytree(
                    package_source,
                    target,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".git", ".cosp", "__pycache__"),
                )
                shutil.rmtree(extract_root)
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
            if expected_version is not None and version != expected_version:
                raise CospError(
                    f"dependency '{name}' resolved version '{version}' does not match "
                    f"the requested version '{expected_version}'"
                )
            package_lock = {"source": dependency_value, "version": version, "sha256": _content_hash(target)}
            if subdir is not None:
                package_lock["subdir"] = subdir
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
    add_parser = subparsers.add_parser(
        "add", help="add a dependency from the remote index, a local path, or an HTTPS ZIP URL"
    )
    add_parser.add_argument("name")
    add_parser.add_argument(
        "path",
        nargs="?",
        help="local directory/ZIP or HTTPS ZIP URL; omit to resolve the package from the index",
    )
    add_parser.add_argument(
        "--index",
        default=None,
        help="package index JSON URL (default: COSMIC_COSP_INDEX or the Cosmic-ASM repository index)",
    )
    index_parser = subparsers.add_parser(
        "index", help="list packages published in the remote index"
    )
    index_parser.add_argument(
        "--index",
        default=None,
        help="package index JSON URL (default: COSMIC_COSP_INDEX or the Cosmic-ASM repository index)",
    )
    remove_parser = subparsers.add_parser("remove", help="remove a dependency")
    remove_parser.add_argument("name")
    subparsers.add_parser("install", help="install dependencies and update cosp.lock")
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

        if args.command == "index":
            index_url, packages = _load_index(args.index)
            print(f"Index: {index_url}")
            if not packages:
                print("No packages published.")
            for name in sorted(packages):
                entry = packages[name]
                version = entry.get("version", "0.0.0")
                location = entry.get("subdir") or entry["url"]
                print(f"{name} {version} ({location})")
            return 0

        manifest = _parse_manifest(root / _MANIFEST)
        dependencies = manifest["dependencies"]
        if not isinstance(dependencies, dict):
            raise CospError("invalid project manifest dependencies")
        if args.command == "add":
            if not _NAME_RE.fullmatch(args.name):
                raise CospError("invalid dependency name")
            if args.path is None:
                resolved = _resolve_registry_entry(args.name, args.index)
                dependencies[args.name] = resolved
                _install(root, manifest, refresh=True)
                _write_manifest(root, manifest)
                location = resolved.get("subdir") or resolved["url"]
                print(f"Added {args.name} {resolved.get('version', '0.0.0')} from {location}")
                return 0
            remote = urlsplit(args.path)
            if remote.scheme:
                if remote.scheme.lower() != "https":
                    raise CospError("remote dependencies must use HTTPS")
                dependencies[args.name] = args.path
            else:
                source = Path(args.path).resolve()
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
            print(f"Installed {len(dependencies)} package(s)")
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
