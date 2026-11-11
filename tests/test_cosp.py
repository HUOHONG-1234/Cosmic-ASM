from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cosmic_interpreter.cosp import (
    CospError,
    _default_index_url,
    _download_https_archive,
    _fetch_https_document,
    _install,
    _main,
    _parse_manifest,
    _read_lock,
    _render_template,
    _resolve_registry_entry,
    _safe_extract,
    _write_manifest,
)


class CospTests(unittest.TestCase):
    def test_builtin_scaffold_writes_manifest_and_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "nebula"
            root.mkdir()
            _render_template("nebula", root, "app")
            manifest = _parse_manifest(root / "cosp.toml")
            self.assertEqual(manifest["package"]["name"], "nebula")
            self.assertTrue((root / "main.cos").is_file())
            self.assertIn("nebula", (root / "README.md").read_text(encoding="utf-8"))

    def test_local_directory_dependency_installs_and_locks_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            package = root / "local_lib"
            project.mkdir()
            package.mkdir()
            (package / "cosp.toml").write_text(
                '[package]\nname = "local_lib"\nversion = "1.2.3"\nentry = "lib.cos"\n',
                encoding="utf-8",
            )
            (package / "lib.cos").write_text("math_run 1 = ?\n", encoding="utf-8")
            manifest = {
                "package": {"name": "project", "version": "0.1.0", "entry": "main.cos"},
                "dependencies": {"local_lib": "../local_lib"},
            }
            from cosmic_interpreter.cosp import _write_manifest

            _write_manifest(project, manifest)
            _install(project, manifest)
            lock = json.loads((project / "cosp.lock").read_text(encoding="utf-8"))
            self.assertEqual(lock["packages"]["local_lib"]["version"], "1.2.3")
            self.assertEqual(
                (project / ".cosp" / "packages" / "local_lib" / "lib.cos").read_text(encoding="utf-8"),
                "math_run 1 = ?\n",
            )
            self.assertRegex(lock["packages"]["local_lib"]["sha256"], r"^[0-9a-f]{64}$")
            installed_file = project / ".cosp" / "packages" / "local_lib" / "lib.cos"
            original_installed_content = installed_file.read_text(encoding="utf-8")
            (package / "lib.cos").write_text("math_run 2 = ?\n", encoding="utf-8")
            with self.assertRaisesRegex(CospError, "differs from cosp.lock"):
                _install(project, manifest)
            self.assertEqual(installed_file.read_text(encoding="utf-8"), original_installed_content)
            _install(project, manifest, refresh=True)
            self.assertEqual(installed_file.read_text(encoding="utf-8"), "math_run 2 = ?\n")

    def test_zip_extraction_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("../outside.txt", "unsafe")
            with self.assertRaisesRegex(CospError, "unsafe archive member"):
                _safe_extract(archive, root / "destination")
            self.assertFalse((root / "outside.txt").exists())

    def test_zip_dependency_installs_without_executing_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            archive = root / "safe_package.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr(
                    "safe_package/cosp.toml",
                    '[package]\nname = "safe_package"\nversion = "1.0.0"\nentry = "lib.cos"\n',
                )
                output.writestr("safe_package/lib.cos", "# dependency source\n")
            manifest = {
                "package": {"name": "project", "version": "0.1.0", "entry": "main.cos"},
                "dependencies": {"safe_package": "../safe_package.zip"},
            }
            from cosmic_interpreter.cosp import _write_manifest

            _write_manifest(project, manifest)
            _install(project, manifest)
            installed = project / ".cosp" / "packages" / "safe_package"
            self.assertTrue((installed / "lib.cos").is_file())
            self.assertNotEqual(list(installed.iterdir()), [])

    def test_manifest_rejects_parent_entry_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "cosp.toml"
            manifest.write_text(
                '[package]\nname = "demo"\nversion = "0.1.0"\nentry = "../outside.cos"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CospError, "project-relative"):
                _parse_manifest(manifest)

    def test_custom_template_only_renders_listed_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "files").mkdir()
            (root / "files" / "main.cos").write_text("# ${project_name}\n", encoding="utf-8")
            (root / "template.json").write_text(
                json.dumps({"files": ["main.cos"]}), encoding="utf-8"
            )
            output = root / "output"
            output.mkdir()
            _render_template("project_x", output, str(root))
            self.assertEqual(
                (output / "main.cos").read_text(encoding="utf-8"),
                "# project_x\n",
            )
            self.assertTrue((output / "cosp.toml").is_file())

    def test_manifest_reads_plugin_metadata_and_toml_features(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "cosp.toml"
            manifest_path.write_text(
                '[package]\nname = "plugin_lib"\nversion = "1.2.0"\nentry = "lib.cos"\n'
                '\n[dependencies]\n'
                '\n[plugin]\ntype = "library"\nfeatures = ["no-bigint", "no-matrix"]\n'
                'requires = ["core"]\n\n[plugin.exposes]\nquick = "sort_quick"\n',
                encoding="utf-8",
            )
            manifest = _parse_manifest(manifest_path)
            self.assertEqual(
                manifest["plugin"],
                {
                    "type": "library",
                    "features": ["no-bigint", "no-matrix"],
                    "requires": ["core"],
                    "exposes": {"quick": "sort_quick"},
                },
            )
            from cosmic_interpreter.cosp import _write_manifest

            _write_manifest(root, manifest)
            reloaded = _parse_manifest(manifest_path)
            self.assertEqual(reloaded["plugin"], manifest["plugin"])

    def test_manifest_rejects_invalid_plugin_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "cosp.toml"
            manifest.write_text(
                '[package]\nname = "plugin_lib"\nversion = "1.0.0"\n'
                '\n[plugin]\ntype = "unknown"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CospError, "plugin.type"):
                _parse_manifest(manifest)

    def test_lockfile_requires_supported_version_and_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "cosp.lock"
            cases = (
                ('{"packages": {}}', "lock_version"),
                ('{"lock_version": 2, "packages": {}}', "lock_version"),
                ('{"lock_version": true, "packages": {}}', "lock_version"),
                (
                    '{"lock_version": 1, "packages": {"pkg": '
                    '{"source": "../pkg", "version": "1.0.0", "sha256": "bad"}}}',
                    "SHA-256",
                ),
                (
                    '{"lock_version": 1, "packages": {"pkg": '
                    '{"source": "../pkg", "version": "invalid", "sha256": "'
                    + "0" * 64
                    + '"}}}',
                    "version",
                ),
            )
            for content, expected in cases:
                with self.subTest(expected=expected):
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaisesRegex(CospError, expected):
                        _read_lock(path)

    def test_install_rejects_lock_dependencies_not_matching_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "library"
            source.mkdir()
            (source / "lib.cos").write_text("math_run 1 = ?\n", encoding="utf-8")
            manifest = {
                "package": {"name": "project", "version": "0.1.0", "entry": "main.cos"},
                "dependencies": {"library": "library"},
            }
            _install(root, manifest)
            stale_manifest = {**manifest, "dependencies": {}}
            with self.assertRaisesRegex(CospError, "do not match cosp.toml"):
                _install(root, stale_manifest)
            self.assertTrue((root / ".cosp" / "packages" / "library").is_dir())

    def test_remote_dependency_requires_https(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CospError, "must use an HTTPS URL"):
                _download_https_archive("http://example.test/package.zip", Path(temporary) / "pkg.zip")
            with self.assertRaisesRegex(CospError, "without embedded credentials"):
                _download_https_archive("https://user:password@example.test/package.zip", Path(temporary) / "pkg.zip")

    def test_https_zip_dependency_is_installed_and_locked_without_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = {
                "package": {"name": "project", "version": "0.1.0", "entry": "main.cos"},
                "dependencies": {"remote_lib": "https://packages.example/remote_lib.zip"},
            }

            def fake_download(_url: str, destination: Path) -> None:
                with zipfile.ZipFile(destination, "w") as archive:
                    archive.writestr(
                        "remote_lib/cosp.toml",
                        '[package]\nname = "remote_lib"\nversion = "1.0.0"\nentry = "lib.cos"\n',
                    )
                    archive.writestr("remote_lib/lib.cos", "# inert source\n")
                    archive.writestr("remote_lib/install.py", "raise RuntimeError('must not execute')\n")

            with patch("cosmic_interpreter.cosp._download_https_archive", side_effect=fake_download):
                _install(root, manifest)
            lock = json.loads((root / "cosp.lock").read_text(encoding="utf-8"))
            self.assertEqual(lock["packages"]["remote_lib"]["source"], manifest["dependencies"]["remote_lib"])
            self.assertRegex(lock["packages"]["remote_lib"]["sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue((root / ".cosp" / "packages" / "remote_lib" / "install.py").is_file())


    def _index_document(self, packages, archive: str = "https://example.test/repo.zip") -> dict:
        return {"index_version": 1, "archive": archive, "packages": packages}

    def _write_repository_archive(self, destination: Path, version: str = "1.0.0") -> None:
        with zipfile.ZipFile(destination, "w") as archive:
            archive.writestr(
                "Cosmic-ASM-main/packages/mathx/cosp.toml",
                f'[package]\nname = "mathx"\nversion = "{version}"\nentry = "lib.cos"\n',
            )
            archive.writestr("Cosmic-ASM-main/packages/mathx/lib.cos", "function twice(v) = v * 2\n")
            archive.writestr("Cosmic-ASM-main/packages/other/lib.cos", "# other package\n")
            archive.writestr("Cosmic-ASM-main/README.md", "# repository\n")

    def test_add_resolves_package_from_repository_index_and_installs_subdir(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            _render_template("project", project, "app")
            document = self._index_document(
                {"mathx": {"version": "1.0.0", "subdir": "packages/mathx"}}
            )
            payload = json.dumps(document).encode("utf-8")

            def fake_download(_url: str, destination: Path) -> None:
                self._write_repository_archive(destination)

            with patch(
                "cosmic_interpreter.cosp._fetch_https_document", return_value=payload
            ), patch(
                "cosmic_interpreter.cosp._download_https_archive", side_effect=fake_download
            ), patch(
                "cosmic_interpreter.cosp._project_root", return_value=project
            ):
                self.assertEqual(_main(["add", "mathx"]), 0)

            manifest_text = (project / "cosp.toml").read_text(encoding="utf-8")
            self.assertIn("mathx = { url = ", manifest_text)
            self.assertIn('subdir = "packages/mathx"', manifest_text)
            dependencies = _parse_manifest(project / "cosp.toml")["dependencies"]
            self.assertEqual(dependencies["mathx"]["subdir"], "packages/mathx")
            self.assertEqual(dependencies["mathx"]["version"], "1.0.0")
            self.assertEqual(dependencies["mathx"]["url"], "https://example.test/repo.zip")

            installed = project / ".cosp" / "packages" / "mathx"
            self.assertTrue((installed / "lib.cos").is_file())
            self.assertFalse((installed / "packages").exists())
            self.assertFalse((installed / "other").exists())
            # 整仓归档只应取出 subdir 内容，不能把仓库其余文件铺进包目录。
            self.assertEqual(
                sorted(item.name for item in installed.iterdir()),
                ["cosp.toml", "lib.cos"],
            )

            lock = json.loads((project / "cosp.lock").read_text(encoding="utf-8"))
            self.assertEqual(lock["packages"]["mathx"]["version"], "1.0.0")
            self.assertEqual(lock["packages"]["mathx"]["subdir"], "packages/mathx")
            self.assertRegex(lock["packages"]["mathx"]["sha256"], r"^[0-9a-f]{64}$")

    def test_index_validation_rejects_malformed_documents(self) -> None:
        cases = {
            "index_version": {"packages": {}},
            "packages must be an object": {"index_version": 1, "packages": []},
            "archive must be an HTTPS URL": {
                "index_version": 1,
                "archive": "http://example.test/repo.zip",
                "packages": {},
            },
            "subdir must be a relative POSIX path": {
                "index_version": 1,
                "packages": {"mathx": {"subdir": "../escape"}},
            },
            "version must be a semantic version": {
                "index_version": 1,
                "packages": {"mathx": {"version": "latest"}},
            },
        }
        for message, document in cases.items():
            with self.subTest(message=message):
                payload = json.dumps(document).encode("utf-8")
                with patch(
                    "cosmic_interpreter.cosp._fetch_https_document", return_value=payload
                ):
                    with self.assertRaisesRegex(CospError, message):
                        _resolve_registry_entry("mathx")

    def test_index_missing_package_lists_available_names(self) -> None:
        document = self._index_document({"mathx": {"version": "1.0.0"}})
        with patch(
            "cosmic_interpreter.cosp._fetch_https_document",
            return_value=json.dumps(document).encode("utf-8"),
        ):
            with self.assertRaisesRegex(CospError, "available: mathx"):
                _resolve_registry_entry("missing")
            resolved = _resolve_registry_entry("mathx")
        self.assertEqual(resolved["url"], "https://example.test/repo.zip")
        self.assertEqual(resolved["registry"], _default_index_url())

    def test_default_index_url_can_be_overridden_by_environment(self) -> None:
        with patch.dict(os.environ, {"COSMIC_COSP_INDEX": "https://mirror.test/index.json"}):
            self.assertEqual(_default_index_url(), "https://mirror.test/index.json")
        with patch.dict(os.environ, {"COSMIC_COSP_INDEX": ""}):
            self.assertTrue(_default_index_url().startswith("https://raw.githubusercontent.com/"))

    def test_dependency_table_round_trips_through_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = {
                "package": {"name": "project", "version": "0.1.0", "entry": "main.cos"},
                "dependencies": {
                    "mathx": {
                        "url": "https://example.test/repo.zip",
                        "subdir": "packages/mathx",
                        "version": "1.0.0",
                        "registry": "https://example.test/index.json",
                    }
                },
            }
            _write_manifest(root, manifest)
            reparsed = _parse_manifest(root / "cosp.toml")
        self.assertEqual(reparsed["dependencies"]["mathx"], manifest["dependencies"]["mathx"])

    def test_dependency_table_validation(self) -> None:
        cases = {
            "has unsupported key": {"url": "https://example.test/a.zip", "branch": "main"},
            "requires a url": {"subdir": "packages/mathx"},
            "url must be an HTTPS URL": {"url": "http://example.test/a.zip"},
            "subdir must be a relative POSIX path": {
                "url": "https://example.test/a.zip",
                "subdir": "../outside",
            },
            "version must be a semantic version": {
                "url": "https://example.test/a.zip",
                "version": "v1",
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            for message, location in cases.items():
                with self.subTest(message=message):
                    manifest_path = Path(temporary) / "cosp.toml"
                    manifest_path.write_text(
                        '[package]\nname = "project"\nversion = "0.1.0"\nentry = "main.cos"\n'
                        "[dependencies]\n"
                        "lib = { " + ", ".join(f'{k} = "{v}"' for k, v in location.items()) + " }\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(CospError, message):
                        _parse_manifest(manifest_path)

    def test_dependency_version_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = {
                "package": {"name": "project", "version": "0.1.0", "entry": "main.cos"},
                "dependencies": {
                    "mathx": {
                        "url": "https://example.test/repo.zip",
                        "subdir": "packages/mathx",
                        "version": "2.0.0",
                    }
                },
            }

            def fake_download(_url: str, destination: Path) -> None:
                self._write_repository_archive(destination)

            with patch(
                "cosmic_interpreter.cosp._download_https_archive", side_effect=fake_download
            ):
                with self.assertRaisesRegex(CospError, "does not match the requested version"):
                    _install(root, manifest)

    def test_transient_http_errors_are_retried(self) -> None:
        import urllib.error

        payload = b"{}"
        calls: list[int] = []

        class FakeResponse:
            def __init__(self, body: bytes) -> None:
                self._body = body
                self._read = False

            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self, _size: int) -> bytes:
                if self._read:
                    return b""
                self._read = True
                return self._body

            def geturl(self) -> str:
                return "https://example.test/index.json"

        class FakeOpener:
            def open(self, _request, timeout: int = 30):
                calls.append(timeout)
                if len(calls) < 3:
                    raise urllib.error.HTTPError(
                        "https://example.test/index.json", 502, "Bad Gateway", {}, None
                    )
                return FakeResponse(payload)

        with patch("cosmic_interpreter.cosp.urllib.request.build_opener", return_value=FakeOpener()), patch(
            "cosmic_interpreter.cosp.time.sleep"
        ) as fake_sleep:
            document = _fetch_https_document(
                "https://example.test/index.json", limit=1024, label="package index"
            )
        self.assertEqual(document, payload)
        self.assertEqual(len(calls), 3)
        self.assertEqual(fake_sleep.call_count, 2)

    def test_client_errors_are_not_retried(self) -> None:
        import urllib.error

        calls: list[int] = []

        class FakeOpener:
            def open(self, _request, timeout: int = 30):
                calls.append(timeout)
                raise urllib.error.HTTPError(
                    "https://example.test/index.json", 404, "Not Found", {}, None
                )

        with patch("cosmic_interpreter.cosp.urllib.request.build_opener", return_value=FakeOpener()):
            with self.assertRaisesRegex(CospError, "HTTP Error 404"):
                _fetch_https_document(
                    "https://example.test/index.json", limit=1024, label="package index"
                )
        self.assertEqual(len(calls), 1)

    def test_index_command_lists_packages(self) -> None:
        document = self._index_document(
            {"mathx": {"version": "1.0.0", "subdir": "packages/mathx"}}
        )
        buffer = io.StringIO()
        with patch(
            "cosmic_interpreter.cosp._fetch_https_document",
            return_value=json.dumps(document).encode("utf-8"),
        ), redirect_stdout(buffer):
            self.assertEqual(_main(["index"]), 0)
        output = buffer.getvalue()
        self.assertIn("Index: ", output)
        self.assertIn("mathx 1.0.0 (packages/mathx)", output)


if __name__ == "__main__":
    unittest.main()
