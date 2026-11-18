"""ARM64 (aarch64-linux-gnu) 交叉编译目标测试。"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cosmic_interpreter.compiler import CompileError, compile_source
from cosmic_interpreter.parser import ParseError, parse

WSL_DISTRO = "Ubuntu-22.04"


class Arm64TargetParsingTests(unittest.TestCase):
    def test_aarch64_target_is_accepted(self) -> None:
        program = parse(
            "#%target = aarch64-linux-gnu\n"
            'var greeting = {"hi"}\n'
            "print (<{greeting}>)\n"
        )
        self.assertEqual(program.target, "aarch64-linux-gnu")

    def test_invalid_target_reports_all_supported_values(self) -> None:
        with self.assertRaises(ParseError) as caught:
            parse("#%target = arm64-darwin\npush ({x} = {1})\n")
        message = str(caught.exception)
        self.assertIn("host", message)
        self.assertIn("riscv64-linux-gnu", message)
        self.assertIn("aarch64-linux-gnu", message)

    def test_unknown_target_directive_reports_all_supported_values(self) -> None:
        with self.assertRaises(ParseError) as caught:
            parse("#%target = aarch64\npush ({x} = {1})\n")
        message = str(caught.exception)
        self.assertIn("host", message)
        self.assertIn("riscv64-linux-gnu", message)
        self.assertIn("aarch64-linux-gnu", message)


def _fake_runs() -> list[subprocess.CompletedProcess]:
    # wslpath(c) -> wslpath(output) -> gcc
    return [
        subprocess.CompletedProcess([], 0, stdout="/mnt/c/program.c\n", stderr=""),
        subprocess.CompletedProcess([], 0, stdout="/mnt/c/program\n", stderr=""),
        subprocess.CompletedProcess([], 0, stdout="", stderr=""),
    ]


class Arm64CompileCommandTests(unittest.TestCase):
    def test_aarch64_selects_cross_compiler_and_march(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "program.cos"
            source.write_text("#%target = aarch64-linux-gnu\n", encoding="utf-8")
            with patch("cosmic_interpreter.compiler.subprocess.run") as run:
                run.side_effect = _fake_runs()
                compile_source(source, output_path=root / "program", gcc=None)
            command = run.call_args_list[-1].args[0]
            self.assertIn("aarch64-linux-gnu-gcc", command)
            self.assertIn("-march=armv8-a", command)
            # 不能把 riscv 的 ABI 参数带过来
            self.assertNotIn("-march=rv64gc", command)
            self.assertNotIn("-mabi=lp64d", command)

    @unittest.skipUnless(os.name == "nt", "Windows 才会走 WSL 交叉编译路径")
    def test_aarch64_cross_compilation_goes_through_wsl(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "program.cos"
            source.write_text("#%target = aarch64-linux-gnu\n", encoding="utf-8")
            with patch("cosmic_interpreter.compiler.subprocess.run") as run:
                run.side_effect = _fake_runs()
                compile_source(source, output_path=root / "program")
            command = run.call_args_list[-1].args[0]
            self.assertEqual(command[0], "wsl.exe")
            self.assertEqual(command[1:4], ["-d", WSL_DISTRO, "--"])
            self.assertEqual(command[4], "aarch64-linux-gnu-gcc")
            # 路径应转成 WSL 路径，Windows 盘符路径不得出现在 C 编译器参数里
            self.assertTrue(any(str(arg).startswith("/") for arg in command[5:]))
            self.assertFalse(any(":\\" in str(arg) for arg in command[5:]))

    @unittest.skipUnless(os.name == "nt", "Windows 输出命名才依赖 linux_target 判断")
    def test_aarch64_default_output_has_no_windows_exe_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "program.cos"
            source.write_text("#%target = aarch64-linux-gnu\n", encoding="utf-8")
            with patch("cosmic_interpreter.compiler.subprocess.run") as run:
                run.side_effect = _fake_runs()
                _c_path, executable = compile_source(source)
            self.assertEqual(executable, source.with_suffix(""))


def _wsl_has(command: str) -> bool:
    if os.name != "nt" or shutil.which("wsl.exe") is None:
        return False
    try:
        probe = subprocess.run(
            ["wsl.exe", "-d", WSL_DISTRO, "--", "bash", "-lc", f"command -v {command}"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0 and probe.stdout.strip() != ""


@unittest.skipUnless(
    os.name == "nt"
    and shutil.which("wsl.exe") is not None
    and _wsl_has("aarch64-linux-gnu-gcc")
    and _wsl_has("qemu-aarch64"),
    "需要 WSL + aarch64-linux-gnu-gcc + qemu-aarch64",
)
class Arm64WslIntegrationTests(unittest.TestCase):
    def test_cross_compiled_aarch64_program_runs_under_qemu(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temporary:
            root = Path(temporary)
            source = root / "arm.cos"
            output = root / "arm-program"
            source.write_text(
                "#%target = aarch64-linux-gnu\n"
                'var greeting = {"hello from arm64"}\n'
                "print (<{greeting}>)\n"
                "array values = array_fill(4, 3)\n"
                "math_run array_sum(values) = ? -int32\n"
                "if up = (ok) goto (push ({sum} = {(in = up)}))\n"
                "print (<{sum}>)\n",
                encoding="utf-8",
            )
            _c_path, executable = compile_source(
                source,
                output_path=output,
                wsl_distro=WSL_DISTRO,
            )
            converted = subprocess.run(
                [
                    "wsl.exe", "-d", WSL_DISTRO, "--", "wslpath", "-a",
                    str(executable).replace("\\", "/"),
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            emulated = subprocess.run(
                [
                    "wsl.exe", "-d", WSL_DISTRO, "--",
                    "qemu-aarch64", "-L", "/usr/aarch64-linux-gnu",
                    converted.stdout.strip(),
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        self.assertEqual(emulated.returncode, 0, emulated.stderr)
        self.assertEqual(
            emulated.stdout.replace("\r\n", "\n"), "hello from arm64\n12\n"
        )


class Arm64ToolchainFailureTests(unittest.TestCase):
    def test_missing_toolchain_surfaces_clear_compile_error(self) -> None:
        """工具链缺失时不能静默成功，必须给出可读的 CompileError。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "program.cos"
            source.write_text("#%target = aarch64-linux-gnu\n", encoding="utf-8")
            failure = subprocess.CompletedProcess(
                [], 1, stdout="", stderr="aarch64-linux-gnu-gcc: not found\n"
            )
            with patch("cosmic_interpreter.compiler.subprocess.run") as run:
                run.side_effect = [
                    subprocess.CompletedProcess([], 0, stdout="/tmp/program.c\n", stderr=""),
                    subprocess.CompletedProcess([], 0, stdout="/tmp/program\n", stderr=""),
                    failure,
                ]
                with self.assertRaisesRegex(CompileError, "not found"):
                    compile_source(source, output_path=root / "program")


if __name__ == "__main__":
    unittest.main()
