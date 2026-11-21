from __future__ import annotations

import io
import json
import math
import os
import platform
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cosmic_interpreter.compiler import CompileError, compile_source, main as compiler_main
from cosmic_interpreter.c_backend import generate_c
from cosmic_interpreter.parser import parse
from cosmic_interpreter.runtime import Interpreter
from cosmic_interpreter.source_loader import load_source


def _wsl_cross_toolchain_ready() -> bool:
    """Windows 上 wsl.exe 存在并不代表能交叉编译。

    很多 CI runner（例如 windows-latest）有 wsl.exe，却没有装 Ubuntu-22.04
    发行版或 riscv 工具链，会让该用例失败。这里真实探测一次发行版 + qemu。
    """
    if os.name != "nt" or not shutil.which("wsl.exe"):
        return False
    try:
        probe = subprocess.run(
            [
                "wsl.exe", "-d", "Ubuntu-22.04", "--", "bash", "-lc",
                "command -v riscv64-linux-gnu-gcc && command -v qemu-riscv64",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


@unittest.skipUnless(shutil.which("gcc"), "GCC is required for native compiler tests")
class CompilerTests(unittest.TestCase):
    def compile_and_run(self, source: str) -> subprocess.CompletedProcess[bytes]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            source_path = directory / "program.cos"
            executable_path = directory / "program.exe"
            source_path.write_text(source, encoding="utf-8")
            c_path, output_path = compile_source(source_path, output_path=executable_path)
            self.assertTrue(c_path.is_file())
            self.assertEqual(output_path, executable_path.resolve())
            return subprocess.run(
                [str(output_path)],
                check=False,
                capture_output=True,
                timeout=15,
            )

    def test_generated_c_omits_unreachable_user_functions(self) -> None:
        source = (
            "function root(value) = helper(value)\n"
            "function helper(value) = value + 1\n"
            "function unused(value) = value * 100\n"
            "math_run root(4) = ?\n"
            "if up = (ok) goto (push ({result} = {(in = up)}))\n"
            "print (<{result}>)\n"
        )
        generated = generate_c(parse(source))
        self.assertIn("user_root", generated)
        self.assertIn("user_helper", generated)
        self.assertNotIn("user_unused", generated)
        completed = self.compile_and_run(source)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"5\n")

    def test_compiled_explicit_and_implicit_string_variables(self) -> None:
        completed = self.compile_and_run(
            'var greeting = {"hello, \\"cosmos\\" {word}"}\n'
            "push ({copy} = {greeting})\n"
            'push ({implicit} = {"42"})\n'
            "math_run {implicit} + 1 = ? -int32\n"
            "if up = (ok) goto (push ({number} = {(in = up)}))\n"
            "print (<{copy}>)\n"
            'print (<{"literal {word}"}>)\n'
            'print (<{"prefix "} (and) {copy}>)\n'
            "print (<{number}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            completed.stdout.replace(b"\r\n", b"\n"),
            b'hello, "cosmos" {word}\nliteral {word}\nprefix hello, "cosmos" {word}\n43\n',
        )

    def test_compiled_ask_reads_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            source_path = directory / "ask.cos"
            executable_path = directory / "ask.exe"
            source_path.write_text('ask ({answer}) (<{"Name: "}>)\nprint (<{answer}>)\n', encoding="utf-8")
            compile_source(source_path, output_path=executable_path)
            completed = subprocess.run(
                [str(executable_path)],
                input=b"Cosmos\n",
                check=False,
                capture_output=True,
                timeout=15,
            )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"Name: Cosmos\n")

    def test_compiled_ask_no_input_flag_does_not_read_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            source_path = directory / "ask.cos"
            executable_path = directory / "ask.exe"
            source_path.write_text("ask ({answer})\n", encoding="utf-8")
            compile_source(source_path, output_path=executable_path, no_input=True)
            completed = subprocess.run([str(executable_path)], input=b"ignored\n", capture_output=True, timeout=15)
        self.assertEqual(completed.returncode, 0)

    def test_compiled_string_library_functions(self) -> None:
        completed = self.compile_and_run(
            'math_run substr_bytes("宇宙", 0, 3) = ?\n'
            "print (<{(in = up)}>)\n"
            'math_run find("a宇宙b", "宇") = ? -int32\n'
            "if up = (ok) goto (push ({position} = {(in = up)}))\n"
            "print (<{position}>)\n"
            'math_run trim("  Cosmic  ") = ?\n'
            "print (<{(in = up)}>)\n"
            'math_run upper("aBc-宇宙") = ?\n'
            "print (<{(in = up)}>)\n"
            'math_run lower("AbC-宇宙") = ?\n'
            "print (<{(in = up)}>)\n"
            'math_run upper("straße-𐐨") = ?\n'
            "print (<{(in = up)}>)\n"
            'math_run lower("İΣ") = ?\n'
            "print (<{(in = up)}>)\n"
            'math_run replace("a-b-a", "a", "x") = ?\n'
            "print (<{(in = up)}>)\n"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(
            completed.stdout.replace(b"\r\n", b"\n"),
            "宇\n1\nCosmic\nABC-宇宙\nabc-宇宙\nSTRASSE-𐐀\ni\u0307σ\nx-b-x\n".encode(),
        )

    def test_compiler_writes_context_memory_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            source = directory / "memory.cos"
            executable = directory / "memory.exe"
            report = directory / "memory.memreport"
            source.write_text(
                'var greeting = {"hello"}\n'
                "array values = [1, 2]\n"
                "math_run 1 + 1 = ?\n",
                encoding="utf-8",
            )
            compile_source(source, output_path=executable, memory_report_path=report)
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(data["target"], "host")
            self.assertEqual(data["pointer_bits"], 64)
            self.assertEqual(data["variable_count"], 1)
            self.assertEqual(data["array_count"], 1)
            self.assertEqual(data["single_context_bytes"], 64 + 48)

    def test_generated_main_context_uses_static_storage(self) -> None:
        generated = generate_c(parse('var greeting = {"hello"}\n'))
        self.assertIn("static Context cosmic_main_context;", generated)
        self.assertIn("Context *context = &cosmic_main_context;", generated)
        self.assertNotIn("Context storage = {0};", generated)

    def test_unicode_case_tables_are_emitted_on_demand(self) -> None:
        upper_only = generate_c(parse('math_run upper("a") = ?\n'))
        lower_only = generate_c(parse('math_run lower("A") = ?\n'))
        no_case = generate_c(parse("math_run 1 + 1 = ?\n"))
        self.assertIn("cosmic_case_upper_map", upper_only)
        self.assertNotIn("cosmic_case_lower_map", upper_only)
        self.assertIn("cosmic_case_lower_map", lower_only)
        self.assertNotIn("cosmic_case_upper_map", lower_only)
        self.assertNotIn("cosmic_case_upper_map", no_case)
        self.assertNotIn("cosmic_case_lower_map", no_case)

    def test_compiled_array_fill_and_copy(self) -> None:
        completed = self.compile_and_run(
            "array values = array_fill(4, 3)\n"
            "array copy = array_copy(values)\n"
            "array copy = array_set(copy, 0, 9)\n"
            "math_run array_sum(values) = ? -int32\n"
            "if up = (ok) goto (push ({sum} = {(in = up)}))\n"
            "print (<{sum}>)\n"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"12\n")

    def test_compiled_string_and_bytes_conversion(self) -> None:
        completed = self.compile_and_run(
            'array data = bytes_from_str("宇宙")\n'
            "math_run str_from_bytes(data) = ?\n"
            "print (<{(in = up)}>)\n"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), "宇宙\n".encode())

    def test_compiler_expands_relative_includes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "lib").mkdir()
            (root / "lib" / "message.cos").write_text(
                'var message = {"included"}\n', encoding="utf-8"
            )
            source_path = root / "main.cos"
            source_path.write_text(
                '#%include = "lib/message.cos"\n#include\nprint (<{message}>)\n',
                encoding="utf-8",
            )
            executable_path = root / "main.exe"
            compile_source(source_path, output_path=executable_path)
            completed = subprocess.run(
                [str(executable_path)], capture_output=True, timeout=15
            )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"included\n")

    def test_include_loader_rejects_cycles_absolute_paths_and_excess_depth(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "a.cos").write_text('#%include = "b.cos"\n', encoding="utf-8")
            (root / "b.cos").write_text('#%include = "a.cos"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "include cycle detected"):
                load_source(root / "a.cos")
            (root / "absolute.cos").write_text(
                '#%include = "C:\\\\Windows\\\\win.ini"\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "include path must be relative"):
                load_source(root / "absolute.cos")
            for index in range(18):
                include = f'#%include = "{index + 1}.cos"\n' if index < 17 else ""
                (root / f"{index}.cos").write_text(include, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "include depth exceeds 16"):
                load_source(root / "0.cos")

    def test_riscv_target_selects_linux_cross_compiler_and_abi(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "target.cos"
            source.write_text("#%target = riscv64-linux-gnu\n", encoding="utf-8")
            with patch("cosmic_interpreter.compiler.subprocess.run") as run:
                run.side_effect = [
                    subprocess.CompletedProcess([], 0, stdout="/mnt/c/target.c\n", stderr=""),
                    subprocess.CompletedProcess([], 0, stdout="/mnt/c/target\n", stderr=""),
                    subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                ]
                compile_source(source, output_path=root / "target")
            command = run.call_args_list[-1].args[0]
            self.assertEqual(len(run.call_args_list), 3)
            self.assertEqual(command[4], "riscv64-linux-gnu-gcc")
            self.assertIn("-march=rv64gc", command)
            self.assertIn("-mabi=lp64d", command)

    @unittest.skipUnless(os.name == "nt", "Windows output naming is required")
    def test_riscv_default_output_has_no_windows_exe_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "target.cos"
            source.write_text("#%target = riscv64-linux-gnu\n", encoding="utf-8")
            with patch("cosmic_interpreter.compiler.subprocess.run") as run:
                run.side_effect = [
                    subprocess.CompletedProcess([], 0, stdout="/mnt/c/target.c\n", stderr=""),
                    subprocess.CompletedProcess([], 0, stdout="/mnt/c/target\n", stderr=""),
                    subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                ]
                _c_path, executable = compile_source(source)
            self.assertEqual(executable, source.with_suffix(""))

    @unittest.skipUnless(
        _wsl_cross_toolchain_ready(),
        "WSL Ubuntu-22.04 with riscv64-linux-gnu-gcc and qemu-riscv64 is required",
    )
    def test_windows_compiler_cross_compiles_riscv_through_wsl(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temporary:
            root = Path(temporary)
            source = root / "riscv.cos"
            output = root / "riscv-program"
            source.write_text(
                '#%target = riscv64-linux-gnu\n'
                'var greeting = {"hello from WSL"}\n'
                'print (<{greeting}>)\n',
                encoding="utf-8",
            )
            _c_path, executable = compile_source(
                source,
                output_path=output,
                wsl_distro="Ubuntu-22.04",
            )
            path_result = subprocess.run(
                ["wsl.exe", "-d", "Ubuntu-22.04", "--", "wslpath", "-a", str(executable).replace("\\", "/")],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
            emulated = subprocess.run(
                [
                    "wsl.exe", "-d", "Ubuntu-22.04", "--",
                    "qemu-riscv64", "-L", "/usr/riscv64-linux-gnu",
                    path_result.stdout.strip(),
                ],
                check=False,
                capture_output=True,
                timeout=30,
            )
        self.assertEqual(emulated.returncode, 0, emulated.stderr.decode(errors="replace"))
        self.assertEqual(emulated.stdout.replace(b"\r\n", b"\n"), b"hello from WSL\n")

    def test_includes_merge_identical_global_directives_and_reject_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "math.cos").write_text("#%math = float64\n", encoding="utf-8")
            source = root / "main.cos"
            source.write_text(
                '#%include = "math.cos"\n#%math = float64\nmath_run 1 + 1 = ?\n',
                encoding="utf-8",
            )
            self.assertEqual(load_source(source).count("#%math"), 1)
            source.write_text(
                '#%include = "math.cos"\n#%math = bigint\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "conflicting #%math declaration"):
                load_source(source)

    def test_compiler_rejects_uninitialized_braced_variable_reference(self) -> None:
        completed = self.compile_and_run("math_run {missing} + 1 = ?\n")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(b"numeric variable is empty or killed", completed.stderr)

    def test_compiler_rejects_removed_pe_component(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = Path(temporary_directory) / "removed_pe.cos"
            source_path.write_text("#pe\n", encoding="utf-8")
            with self.assertRaisesRegex(
                CompileError,
                r"line 1: component 'pe' is not supported \(removed in v6\)",
            ):
                compile_source(source_path)

    def test_compiled_duplicate_variable_declaration_fails(self) -> None:
        completed = self.compile_and_run(
            'var name = {"first"}\nvar name = {"second"}\n'
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(b"already declared", completed.stderr)

    def test_compiled_kill_force_wipes_variable_storage(self) -> None:
        completed = self.compile_and_run(
            'var secret = {"sensitive text"}\nkill {secret} -force\n'
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        generated = generate_c(parse('var secret = {"sensitive text"}\nkill {secret} -force\n'))
        self.assertIn("secure_wipe(", generated)

    def test_compiled_math_accepts_braced_string_literal(self) -> None:
        completed = self.compile_and_run(
            'math_run ord({"A"}) = ? -int32\n'
            "if up = (ok) goto (push ({result} = {(in = up)}))\n"
            "print (<{result}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), b"65")

    def test_compiled_bigint_fixed_point_constants(self) -> None:
        completed = self.compile_and_run(
            "#%math = bigint\n"
            "math_run pi_digits(40) = ?\n"
            "if up = (ok) goto (push ({pi40} = {(in = up)}))\n"
            "print (<{pi40}>)\n"
            "math_run pi_str(5) = ?\n"
            "if up = (ok) goto (push ({pi5} = {(in = up)}))\n"
            "print (<{pi5}>)\n"
            "math_run e_str(10) = ?\n"
            "if up = (ok) goto (push ({e10} = {(in = up)}))\n"
            "print (<{e10}>)\n"
            "math_run sqrt2_str(10) = ?\n"
            "if up = (ok) goto (push ({sqrt10} = {(in = up)}))\n"
            "print (<{sqrt10}>)\n"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(
            completed.stdout.replace(b"\r\n", b"\n"),
            b"31415926535897932384626433832795028841971\n"
            b"3.14159\n2.7182818284\n1.4142135623\n",
        )

    def test_compiles_and_runs_variable_loop_math_and_dog(self) -> None:
        source = """\
#execute
#math
#hello = GBK
push ({hello} = {"hello_world"})
call = (execute)
father = up
start(loop) = {loop}
{
    print(<{hello}>) -GBK
    if up = (error) goto (to = stop)
    kill{hello} -force
}
dog(loop) = {stop}
call = (math)
father = up
start(normal) = {math}
{
    math_run (1 + 2) * (3 + 4) = ?
    if up = (ok) goto (push ({answer} = {(in = up)} -GBK ) )
    print(<{answer}>) -GBK
}
"""
        completed = self.compile_and_run(source)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"hello_world\n21\n")
        self.assertEqual(completed.stderr, b"")

    def test_compiled_program_invokes_bash_statement(self) -> None:
        source = """\
#execute
start(normal) = {command}
{
    command = (open = (open = (bash = echo compiler_ok) ) )
    if up = (ok) goto (push ({answer} = {(in = up)} ) )
    print(<{answer}>)
}
dog(command) = {ok}
"""
        completed = self.compile_and_run(source)
        self.assertEqual(completed.returncode, 0)
        self.assertIn(b"compiler_ok", completed.stdout)

    def test_compiled_dog_status_whitelist(self) -> None:
        completed = self.compile_and_run(
            "start(normal) = {work}\n"
            "{\n"
            "    if up = (ok) goto (to = stop)\n"
            "}\n"
            "dog(work) = (stop : error) (pass : ok)\n"
            "if up = (error) goto (push ({state} = {\"handled_stop\"}))\n"
            "print (<{state}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), b"handled_stop")

    def test_compiled_execution_block_else_runs_after_stop(self) -> None:
        completed = self.compile_and_run(
            "push ({state} = {\"before\"})\n"
            "father = up\n"
            "start(normal) = {work}\n"
            "{\n"
            "    push ({state} = {\"primary\"})\n"
            "    if up = (ok) goto (to = stop)\n"
            "}\n"
            "else\n"
            "{\n"
            "    push ({state} = {\"fallback\"})\n"
            "    print (<{state}>)\n"
            "}\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), b"fallback")

    def test_compiled_jump_targets_physical_line_in_block(self) -> None:
        completed = self.compile_and_run(
            "push ({state} = {\"before\"})\n"
            "father = up\n"
            "start(normal) = {work}\n"
            "{\n"
            "    jump(8)\n"
            "    push ({state} = {\"skipped\"})\n"
            "    push ({state} = {\"also_skipped\"})\n"
            "    push ({state} = {\"target\"})\n"
            "    print (<{state}>)\n"
            "}\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), b"target")

    def test_compiler_rejects_invalid_jump_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = Path(temporary_directory) / "jump.cos"
            source_path.write_text(
                "start(normal) = {work}\n{\njump(99)\n}\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CompileError, "jump target line 99"):
                compile_source(source_path)

    @unittest.skipUnless(os.name == "nt", "GBK code page conversion uses Windows APIs")
    def test_compiled_program_encodes_gbk_output(self) -> None:
        source = """\
#text = GBK
push ({text} = {"你好"})
print(<{text}>) -GBK
"""
        completed = self.compile_and_run(source)
        self.assertEqual(completed.returncode, 0)
        self.assertIn("你好".encode("gbk"), completed.stdout)

    def test_compiled_condition_can_check_killed_variable(self) -> None:
        source = """\
#item = GBK
push ({item} = {"alive"})
kill{item} -force
if up {item} = (killer) goto (push ({state} = {"killed"}))
print(<{state}>)
"""
        completed = self.compile_and_run(source)
        self.assertEqual(completed.returncode, 0)
        self.assertIn(b"killed", completed.stdout)

    def test_compiled_math_uses_fractional_division(self) -> None:
        source = """\
#math
call = (math)
father = up
start(normal) = {math}
{
    math_run 1 / 2 = ?
    if up = (ok) goto (push ({half} = {(in = up)} ) )
    print(<{half}>)
}
"""
        completed = self.compile_and_run(source)
        self.assertEqual(completed.returncode, 0)
        self.assertIn(b"0.5", completed.stdout)

    def test_compiled_float_output_precision_is_configurable(self) -> None:
        completed = self.compile_and_run(
            "#%precision = 17\nmath_run 1 / 3 = ?\n"
            "if up = (ok) goto (push ({third} = {(in = up)}))\n"
            "print (<{third}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), b"0.33333333333333331")

    def test_compiled_complex_arithmetic_and_functions(self) -> None:
        completed = self.compile_and_run(
            "#%math = complex\n"
            "math_run complex(1, 2) * complex(3, -1) = ?\n"
            "if up = (ok) goto (push ({product} = {(in = up)}))\n"
            "print (<{product}>)\n"
            "math_run sqrt(-1) = ?\n"
            "if up = (ok) goto (push ({root} = {(in = up)}))\n"
            "print (<{root}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"5+5i\n0+1i\n")

    def test_compiled_abs_of_complex_in_float64_mode(self) -> None:
        completed = self.compile_and_run(
            "math_run abs(complex(3, 4)) = ? -float64\n"
            "if up = (ok) goto (push ({magnitude} = {(in = up)}))\n"
            "print (<{magnitude}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), b"5")

    def test_float64_mode_still_rejects_complex_arithmetic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = Path(temporary_directory) / "complex.cos"
            source_path.write_text(
                "math_run complex(1, 2) + 1 = ? -float64\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CompileError, "complex\\(\\) requires complex math mode"):
                compile_source(source_path)

    def test_compiled_complex_arrays_and_matrix_operations(self) -> None:
        completed = self.compile_and_run(
            "#%math = complex\n"
            "math_run array_sum([complex(1, 2), complex(3, 4)]) = ?\n"
            "if up = (ok) goto (push ({sum} = {(in = up)}))\n"
            "print (<{sum}>)\n"
            "math_run matrix2_det([[complex(1, 1), 2], [3, 4]]) = ?\n"
            "if up = (ok) goto (push ({det} = {(in = up)}))\n"
            "print (<{det}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"4+6i\n-2+4i\n")

    def test_compiled_complex_named_and_variable_arrays(self) -> None:
        completed = self.compile_and_run(
            "#%math = complex\n"
            "array values = [complex(1, 2), complex(3, 4)]\n"
            "push ({copy} = array_push(values, complex(5, 6)))\n"
            "push ({alias} = {copy})\n"
            "push ({copy} = array_push({copy}, complex(7, 8)))\n"
            "math_run array_sum({alias}) = ?\n"
            "if up = (ok) goto (push ({sum} = {(in = up)}))\n"
            "print (<{sum}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"9+12i\n")

    def test_compiled_array_values_are_copied_and_reassignable(self) -> None:
        completed = self.compile_and_run(
            "push ({values} = [1, 2])\n"
            "push ({copy} = {values})\n"
            "push ({copy} = array_push({copy}, 3))\n"
            "push ({copy} = array_push({copy}, 4))\n"
            "math_run array_sum({values}) = ? -int64\n"
            "if up = (ok) goto (push ({original_sum} = {(in = up)}))\n"
            "math_run array_sum({copy}) = ? -int64\n"
            "if up = (ok) goto (push ({copy_sum} = {(in = up)}))\n"
            'push ({values} = {"text"})\n'
            "push ({values} = [7, 8])\n"
            "math_run array_sum({values}) = ? -int64\n"
            "if up = (ok) goto (push ({new_sum} = {(in = up)}))\n"
            "print (<{original_sum}>)\n"
            "print (<{copy_sum}>)\n"
            "print (<{new_sum}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"3\n10\n15\n")

    def test_compiled_complex_values_round_trip_through_variables(self) -> None:
        completed = self.compile_and_run(
            "math_run complex(2, -3) = ? -complex\n"
            "if up = (ok) goto (push ({z} = {(in = up)}))\n"
            "math_run {z} + complex(1, 1) = ? -complex\n"
            "if up = (ok) goto (push ({result} = {(in = up)}))\n"
            "print (<{result}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"3-2i\n")

    def test_compiled_bigint_arithmetic_and_recursive_functions(self) -> None:
        completed = self.compile_and_run(
            "#%math = bigint\n"
            "function factorial(n) = choose(n <= 1, 1, n * factorial(n - 1))\n"
            "array values = [4, 5, 6]\n"
            'push ({large} = {"123456789012345678901234567890"})\n'
            "math_run 999999999999999999999999999999 ** 2 = ?\n"
            "if up = (ok) goto (push ({square} = {(in = up)}))\n"
            "math_run {large} + 1 = ?\n"
            "if up = (ok) goto (push ({incremented} = {(in = up)}))\n"
            "math_run array_len(values) = ?\n"
            "if up = (ok) goto (push ({length} = {(in = up)}))\n"
            'math_run ord({"A"}) = ?\n'
            "if up = (ok) goto (push ({codepoint} = {(in = up)}))\n"
            "math_run factorial(30) = ?\n"
            "if up = (ok) goto (push ({factorial} = {(in = up)}))\n"
            "math_run -7 // 3 = ?\n"
            "if up = (ok) goto (push ({quotient} = {(in = up)}))\n"
            "math_run -7 % 3 = ?\n"
            "if up = (ok) goto (push ({remainder} = {(in = up)}))\n"
            "print (<{square}>)\n"
            "print (<{incremented}>)\n"
            "print (<{length}>)\n"
            "print (<{codepoint}>)\n"
            "print (<{factorial}>)\n"
            "print (<{quotient}>)\n"
            "print (<{remainder}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            completed.stdout.replace(b"\r\n", b"\n"),
            b"999999999999999999999999999998000000000000000000000000000001\n"
            b"123456789012345678901234567891\n"
            b"3\n65\n"
            b"265252859812191058636308480000000\n-3\n2\n",
        )

    def test_compiled_bigint_enforces_the_interpreter_bit_limit(self) -> None:
        completed = self.compile_and_run(
            "#%math = bigint\nmath_run (2 ** 1000) ** 1000 = ?\n"
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(b"integer result is too large", completed.stderr)

    def test_compiler_rejects_non_integer_bigint_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = Path(temporary_directory) / "bigint.cos"
            source_path.write_text(
                "#%math = bigint\nmath_run 1 < 2 = ?\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CompileError, "bigint mode requires integer-only expressions"):
                compile_source(source_path)

    def test_non_windows_runtime_guards_windows_code_page_apis(self) -> None:
        generated = generate_c(parse("nop\n"))
        start = generated.index("static unsigned int code_page")
        end = generated.index("#endif", start)
        self.assertTrue(generated[:start].rstrip().endswith("#ifdef _WIN32"))
        self.assertIn("GetACP()", generated[start:end])

    def test_compiled_utf8_file_read_and_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "text.txt"
            source = (
                f"math_run write_text({str(target)!r}, '你好') = ?\n"
                "if up = (ok) goto (push ({written} = {(in = up)}))\n"
                "print (<{written}>)\n"
                f"math_run read_text({str(target)!r}) = ?\n"
                "if up = (ok) goto (push ({text} = {(in = up)}))\n"
                "print (<{text}>)\n"
            )
            completed = self.compile_and_run(source)
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), "2\n你好\n".encode("utf-8"))
            self.assertEqual(target.read_text(encoding="utf-8"), "你好")

    def test_compiled_recursive_expression_function(self) -> None:
        completed = self.compile_and_run(
            "function factorial(n) = choose(n <= 1, 1, n * factorial(n - 1))\n"
            "math_run factorial(10) = ?\n"
            "if up = (ok) goto (push ({answer} = {(in = up)}))\n"
            "print (<{answer}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), b"3628800")

    def test_compiled_recursive_expression_function_reports_depth_error(self) -> None:
        completed = self.compile_and_run(
            "function descend(n) = choose(n <= 0, 0, descend(n - 1))\n"
            "math_run descend(300) = ?\n"
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(b"function recursion depth exceeded", completed.stderr)

    def test_compiled_multi_statement_function_with_locals_and_return(self) -> None:
        completed = self.compile_and_run(
            "function transform(value)\n"
            "{\n"
            "let doubled = {value} * 2\n"
            "let adjusted = {doubled} + 3\n"
            "return {adjusted}\n"
            "}\n"
            "math_run transform(5) = ? -int64\n"
            "if up = (ok) goto (push ({answer} = {(in = up)}))\n"
            "print (<{answer}>)\n"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.strip(), b"13")

    def test_compiled_multi_statement_function_can_recurse(self) -> None:
        completed = self.compile_and_run(
            "function factorial(n)\n"
            "{\n"
            "let previous = {n} - 1\n"
            "return choose({n} <= 1, 1, {n} * factorial({previous}))\n"
            "}\n"
            "math_run factorial(10) = ? -int64\n"
            "if up = (ok) goto (push ({answer} = {(in = up)}))\n"
            "print (<{answer}>)\n"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.strip(), b"3628800")

    def test_compiled_multi_statement_function_conditional_returns(self) -> None:
        completed = self.compile_and_run(
            "function absolute(value)\n"
            "{\n"
            "if ({value} < 0)\n"
            "{\n"
            "let negated = 0 - {value}\n"
            "return {negated}\n"
            "}\n"
            "else\n"
            "{\n"
            "let unchanged = {value}\n"
            "return {unchanged}\n"
            "}\n"
            "}\n"
            "math_run absolute(-12) = ? -int64\n"
            "if up = (ok) goto (push ({answer} = {(in = up)}))\n"
            "print (<{answer}>)\n"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.strip(), b"12")

    def test_compiled_multi_statement_function_supports_nested_conditional_returns(self) -> None:
        completed = self.compile_and_run(
            "function classify(value)\n"
            "{\n"
            "if ({value} < 0)\n"
            "{\n"
            "return -1\n"
            "}\n"
            "else\n"
            "{\n"
            "if ({value} == 0)\n"
            "{\n"
            "return 0\n"
            "}\n"
            "else\n"
            "{\n"
            "return 1\n"
            "}\n"
            "}\n"
            "}\n"
            "math_run classify(0) = ? -int64\n"
            "if up = (ok) goto (push ({answer} = {(in = up)}))\n"
            "print (<{answer}>)\n"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.strip(), b"0")

    def test_compiled_function_body_with_while_loop(self) -> None:
        completed = self.compile_and_run(
            "function gcd(a, b)\n"
            "{\n"
            "let x = {a}\n"
            "let y = {b}\n"
            "let remainder = 0\n"
            "while ({y} > 0)\n"
            "{\n"
            "set remainder = {x} % {y}\n"
            "set x = {y}\n"
            "set y = {remainder}\n"
            "}\n"
            "return {x}\n"
            "}\n"
            "math_run gcd(1071, 462) = ? -int32\n"
            "if up = (ok) goto (push ({answer} = {(in = up)}))\n"
            "print (<{answer}>)\n"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.strip(), b"21")

    def test_compiled_function_body_with_continue_break_and_early_return(self) -> None:
        completed = self.compile_and_run(
            "function sum_odd(limit)\n"
            "{\n"
            "let total = 0\n"
            "let index = 0\n"
            "while ({index} < {limit})\n"
            "{\n"
            "set index = {index} + 1\n"
            "if ({index} % 2 == 0)\n"
            "{\n"
            "continue\n"
            "}\n"
            "if ({total} > 100)\n"
            "{\n"
            "break\n"
            "}\n"
            "set total = {total} + {index}\n"
            "}\n"
            "return {total}\n"
            "}\n"
            "math_run sum_odd(10) = ? -int64\n"
            "if up = (ok) goto (push ({answer} = {(in = up)}))\n"
            "print (<{answer}>)\n"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.strip(), b"25")
        early = self.compile_and_run(
            "function first_multiple(start)\n"
            "{\n"
            "let value = {start}\n"
            "while (1 == 1)\n"
            "{\n"
            "if ({value} % 7 == 0)\n"
            "{\n"
            "return {value}\n"
            "}\n"
            "set value = {value} + 1\n"
            "}\n"
            "return -1\n"
            "}\n"
            "math_run first_multiple(10) = ? -int64\n"
            "if up = (ok) goto (push ({answer} = {(in = up)}))\n"
            "print (<{answer}>)\n"
        )
        self.assertEqual(early.returncode, 0, early.stderr.decode(errors="replace"))
        self.assertEqual(early.stdout.strip(), b"14")

    def test_compiled_function_body_falls_through_to_zero(self) -> None:
        completed = self.compile_and_run(
            "function positive_only(value)\n"
            "{\n"
            "if ({value} > 0)\n"
            "{\n"
            "return {value}\n"
            "}\n"
            "}\n"
            "math_run positive_only(-5) = ? -int64\n"
            "if up = (ok) goto (push ({answer} = {(in = up)}))\n"
            "print (<{answer}>)\n"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.strip(), b"0")

    def test_compiled_bigint_function_body_with_loop(self) -> None:
        completed = self.compile_and_run(
            "#%math = bigint\n"
            "function factorial(n)\n"
            "{\n"
            "let result = 1\n"
            "let index = 1\n"
            "while ({index} <= {n})\n"
            "{\n"
            "set result = {result} * {index}\n"
            "set index = {index} + 1\n"
            "}\n"
            "return {result}\n"
            "}\n"
            "math_run factorial(25) = ?\n"
            "if up = (ok) goto (push ({answer} = {(in = up)}))\n"
            "print (<{answer}>)\n"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(
            completed.stdout.strip(), b"15511210043330985984000000"
        )

    def test_compiled_uint32_function_body_uses_wrapping_math(self) -> None:
        completed = self.compile_and_run(
            "#%math = uint32\n"
            "function rotate(value)\n"
            "{\n"
            "let result = {value}\n"
            "set result = ({result} << 1) | ({result} >> 7)\n"
            "return {result}\n"
            "}\n"
            "math_run rotate(200) = ?\n"
            "if up = (ok) goto (push ({answer} = {(in = up)}))\n"
            "print (<{answer}>)\n"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.strip(), b"401")

    def test_compiled_function_body_respects_loop_iteration_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            source_path = directory / "program.cos"
            executable_path = directory / "program.exe"
            source_path.write_text(
                "function spin()\n"
                "{\n"
                "let count = 0\n"
                "while (1 == 1)\n"
                "{\n"
                "set count = {count} + 1\n"
                "}\n"
                "return {count}\n"
                "}\n"
                "math_run spin() = ? -int64\n",
                encoding="utf-8",
            )
            c_path, output_path = compile_source(
                source_path, output_path=executable_path, max_loop_iterations=5
            )
            completed = subprocess.run(
                [str(output_path)], check=False, capture_output=True, timeout=15
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(b"loop iteration limit exceeded", completed.stderr)

    def test_generated_c_with_statement_function_uses_goto_exit_label(self) -> None:
        generated = generate_c(
            parse(
                "function pick(value)\n"
                "{\n"
                "if ({value} > 0)\n"
                "{\n"
                "return 1\n"
                "}\n"
                "return 0\n"
                "}\n"
                "math_run pick(3) = ? -int64\n"
            )
        )
        self.assertIn("static double user_pick(double param_value)", generated)
        self.assertIn("goto cosmic_function_exit_pick;", generated)
        self.assertIn("cosmic_function_exit_pick:", generated)
        self.assertIn("++cosmic_function_depth;", generated)
        self.assertIn("--cosmic_function_depth;", generated)

    def test_compiled_nested_function_capture_parity(self) -> None:
        source = (
            "function outer(seed)\n"
            "{\n"
            "let factor = 2\n"
            "function apply(value) = value * factor + seed\n"
            "let first = apply(3)\n"
            "set factor = 10\n"
            "let second = apply(3)\n"
            "return first * 100 + second\n"
            "}\n"
            "math_run outer(5) = ? -int64\n"
            "if up = (ok) goto (push ({answer} = {(in = up)}))\n"
            "print (<{answer}>)\n"
        )
        completed = self.compile_and_run(source)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.strip(), b"1135")
        generated = generate_c(parse(source))
        self.assertIn("user_outer__apply", generated)
        self.assertIn("captured_factor", generated)

    def test_compiled_record_values_and_operators(self) -> None:
        source = (
            "record Point { x, y }\n"
            "operator + Point = { left.x + right.x, left.y + right.y }\n"
            "function compute()\n"
            "{\n"
            "let a = Point(1, 2)\n"
            "let b = Point(3, 4)\n"
            "let sum = a + b\n"
            "let copy = sum\n"
            "set copy.y = 100\n"
            "return sum.y * 1000 + copy.y\n"
            "}\n"
            "math_run compute() = ? -int64\n"
            "if up = (ok) goto (push ({answer} = {(in = up)}))\n"
            "print (<{answer}>)\n"
        )
        completed = self.compile_and_run(source)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.strip(), b"6100")
        generated = generate_c(parse(source))
        self.assertIn("local_sum__x", generated)
        self.assertIn("local_copy__y", generated)

    def test_compiled_try_catch_finally_matches_interpreter(self) -> None:
        source = (
            "try\n"
            "{\n"
            "ask ({line})\n"
            "push ({after_ask} = {\"continued\"})\n"
            "}\n"
            "catch\n"
            "{\n"
            "push ({caught} = {\"caught\"})\n"
            "}\n"
            "finally\n"
            "{\n"
            "push ({cleanup} = {\"done\"})\n"
            "}\n"
            "print (<{caught}>)\n"
            "print (<{cleanup}>)\n"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            source_path = directory / "program.cos"
            executable_path = directory / "program.exe"
            source_path.write_text(source, encoding="utf-8")
            _c_path, output_path = compile_source(
                source_path, output_path=executable_path, no_input=True
            )
            completed = subprocess.run(
                [str(output_path)],
                check=False,
                capture_output=True,
                timeout=15,
                stdin=subprocess.DEVNULL,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(
            completed.stdout.replace(b"\r\n", b"\n"), b"caught\ndone\n"
        )

    def test_compiled_try_without_catch_keeps_error_status_after_finally(self) -> None:
        source = (
            "try\n"
            "{\n"
            "ask ({line})\n"
            "}\n"
            "finally\n"
            "{\n"
            "push ({cleanup} = {\"done\"})\n"
            "}\n"
            "if up = (error) goto (push ({failed} = {\"error\"}))\n"
            "print (<{cleanup}>)\n"
            "print (<{failed}>)\n"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            source_path = directory / "program.cos"
            executable_path = directory / "program.exe"
            source_path.write_text(source, encoding="utf-8")
            _c_path, output_path = compile_source(
                source_path, output_path=executable_path, no_input=True
            )
            completed = subprocess.run(
                [str(output_path)],
                check=False,
                capture_output=True,
                timeout=15,
                stdin=subprocess.DEVNULL,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(
            completed.stdout.replace(b"\r\n", b"\n"), b"done\nerror\n"
        )

    def test_compiled_namespaces_and_aliases(self) -> None:
        completed = self.compile_and_run(
            "#%namespace = sort\n"
            "function quick(values) = values * 2\n"
            "#%namespace = mathx\n"
            "function quick(values) = values * 10\n"
            "#%namespace = global\n"
            "#%use = sort.quick as doubled\n"
            "math_run doubled(4) = ? -int64\n"
            "if up = (ok) goto (push ({a} = {(in = up)}))\n"
            "math_run mathx::quick(3) = ? -int64\n"
            "if up = (ok) goto (push ({b} = {(in = up)}))\n"
            "print (<{a}>)\n"
            "print (<{b}>)\n"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"8\n30\n")

    def test_compiled_gc_pin_and_the_world(self) -> None:
        completed = self.compile_and_run(
            "#gc = semi\n"
            "array data = [1, 2, 3]\n"
            "gc_pin ({data})\n"
            "The_world (stop)\n"
            "math_run array_sum(data) = ? -int64\n"
            "if up = (ok) goto (push ({total} = {(in = up)}))\n"
            "The_world (force)\n"
            "The_world (start)\n"
            "print (<{total}>)\n"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.strip(), b"6")

    def test_compiled_gc_none_disables_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            source_path = directory / "program.cos"
            executable_path = directory / "program.exe"
            source_path.write_text(
                "#gc = none\n"
                "array data = [4, 5, 6]\n"
                "math_run array_sum(data) = ? -int64\n"
                "if up = (ok) goto (push ({total} = {(in = up)}))\n"
                "print (<{total}>)\n",
                encoding="utf-8",
            )
            c_path, output_path = compile_source(source_path, output_path=executable_path)
            generated = c_path.read_text(encoding="utf-8")
            completed = subprocess.run(
                [str(output_path)], check=False, capture_output=True, timeout=15
            )
        self.assertIn("#define COSMIC_GC_NONE 1", generated)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.strip(), b"15")

    def test_compiled_inline_assembly_runs(self) -> None:
        if platform.machine().lower() not in {"amd64", "x86_64", "x64"}:
            self.skipTest("x86_64 inline assembly requires an x86_64 host")
        completed = self.compile_and_run(
            "#%asm = x86_64 (clobber: rax, memory)\n"
            "#asm\n"
            "{\n"
            "    nop\n"
            "}\n"
            "math_run 1 + 1 = ? -int32\n"
            "if up = (ok) goto (push ({answer} = {(in = up)}))\n"
            "print (<{answer}>)\n"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.strip(), b"2")

    def test_compiled_included_library_prunes_unused_functions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            library = directory / "lib" / "mathx.cos"
            library.parent.mkdir()
            library.write_text(
                "function used(value) = value * 2\n"
                "function unused(value) = value * 100\n",
                encoding="utf-8",
            )
            source_path = directory / "program.cos"
            executable_path = directory / "program.exe"
            source_path.write_text(
                '#%include = "lib/mathx.cos"\n'
                "math_run used(21) = ? -int32\n"
                "if up = (ok) goto (push ({answer} = {(in = up)}))\n"
                "print (<{answer}>)\n",
                encoding="utf-8",
            )
            c_path, output_path = compile_source(
                source_path, output_path=executable_path
            )
            generated = c_path.read_text(encoding="utf-8")
            completed = subprocess.run(
                [str(output_path)], check=False, capture_output=True, timeout=15
            )
        self.assertIn("user_used", generated)
        self.assertNotIn("user_unused", generated)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.strip(), b"42")

    def test_feature_flags_gate_language_surface(self) -> None:
        with self.assertRaisesRegex(Exception, "not available because feature 'no-array'"):
            parse("#%features = no-array\nmath_run array_sum([1, 2]) = ?\n")
        with self.assertRaisesRegex(Exception, "unknown feature tag"):
            parse("#%features = no-string\n")
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            source_path = directory / "program.cos"
            executable_path = directory / "program.exe"
            source_path.write_text(
                "#%features = no-array, no-bigint, no-matrix\n"
                "math_run 1 + 1 = ? -int32\n"
                "if up = (ok) goto (push ({answer} = {(in = up)}))\n"
                "print (<{answer}>)\n",
                encoding="utf-8",
            )
            c_path, output_path = compile_source(
                source_path, output_path=executable_path
            )
            generated = c_path.read_text(encoding="utf-8")
            completed = subprocess.run(
                [str(output_path)], check=False, capture_output=True, timeout=15
            )
        self.assertIn("#define COSMIC_FEATURE_BIGINT 0", generated)
        self.assertIn("#if COSMIC_FEATURE_BIGINT", generated)
        self.assertIn("#define COSMIC_FEATURE_ARRAY 0", generated)
        self.assertIn("#define COSMIC_FEATURE_MATRIX 0", generated)
        self.assertIn("#if COSMIC_FEATURE_ARRAY\nstatic double cosmic_array_mean(", generated)
        self.assertIn("#if COSMIC_FEATURE_MATRIX\nstatic CosmicMatrix cosmic_matrix(", generated)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.strip(), b"2")

    def test_memory_modes_and_gc_mark_keep_identical_output(self) -> None:
        body = (
            "#%math = int64\n"
            'var greeting = {"hello"}\n'
            "function twice(value) = value * 2\n"
            "array data = [1, 2, 3, 4]\n"
            "math_run twice(array_sum(data)) = ? -int64\n"
            "if up = (ok) goto (push ({total} = {(in = up)}))\n"
            "array more = array_push(data, 5)\n"
            "math_run array_sum(more) + array_len(more) = ? -int64\n"
            "if up = (ok) goto (push ({sum} = {(in = up)}))\n"
            "print (<{greeting}>)\n"
            "print (<{total}>)\n"
            "print (<{sum}>)\n"
        )
        cases = {
            "heap": "",
            "arena": "#%memory = arena\n",
            "static": "#%memory = static\n#%maxlen = 16\n",
            "mark": "#gc = mark\n",
            "mark-arena": "#gc = mark\n#%memory = arena\n",
        }
        for name, prefix in cases.items():
            with self.subTest(mode=name):
                completed = self.compile_and_run(prefix + body)
                self.assertEqual(
                    completed.returncode, 0, completed.stderr.decode(errors="replace")
                )
                self.assertEqual(
                    completed.stdout.replace(b"\r\n", b"\n"), b"hello\n20\n20\n"
                )

    def test_maxlen_capacity_aborts_or_reports_error(self) -> None:
        aborting = self.compile_and_run(
            "#%maxlen = 2\narray data = [1, 2, 3]\n"
            "math_run array_len(data) = ? -int32\n"
        )
        self.assertEqual(aborting.returncode, 1)
        self.assertIn(b"array capacity exceeded", aborting.stderr)

        recovering = self.compile_and_run(
            "#%maxlen = 2\n#%on_oom = error\n"
            "array data = [1, 2, 3]\n"
            'if up = (error) goto (push ({state} = {"oom"}))\n'
            "print (<{state}>)\n"
        )
        self.assertEqual(
            recovering.returncode, 0, recovering.stderr.decode(errors="replace")
        )
        self.assertEqual(recovering.stdout.replace(b"\r\n", b"\n"), b"oom\n")

    def test_heap_report_prints_peak_temporary_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            source_path = directory / "program.cos"
            executable_path = directory / "program.exe"
            source_path.write_text(
                "#%math = int64\n"
                "array data = array_fill(1000, 2)\n"
                "array more = array_push(data, 3)\n"
                "math_run array_sum(more) = ? -int64\n"
                "if up = (ok) goto (push ({total} = {(in = up)}))\n"
                "print (<{total}>)\n",
                encoding="utf-8",
            )
            c_path, output_path = compile_source(
                source_path, output_path=executable_path, report_heap=True
            )
            generated = c_path.read_text(encoding="utf-8")
            completed = subprocess.run(
                [str(output_path)], check=False, capture_output=True, timeout=15
            )
        self.assertIn("#define COSMIC_REPORT_HEAP 1", generated)
        self.assertEqual(
            completed.returncode, 0, completed.stderr.decode(errors="replace")
        )
        self.assertEqual(completed.stdout.strip(), b"2003")
        self.assertRegex(
            completed.stderr.decode(errors="replace"),
            r"memory: peak temporary bytes [1-9]\d*",
        )

    def test_reachable_pruning_keeps_functions_called_inside_try_and_blocks(self) -> None:
        completed = self.compile_and_run(
            "function twice(value) = value * 2\n"
            "start(normal) = {compute}\n"
            "{\n"
            "math_run twice(3) = ? -int32\n"
            "if up = (ok) goto (push ({blocked} = {(in = up)}))\n"
            "}\n"
            "try\n"
            "{\n"
            "math_run twice(5) = ? -int32\n"
            "if up = (ok) goto (push ({guarded} = {(in = up)}))\n"
            "}\n"
            "finally\n"
            "{\n"
            "push ({done} = {\"yes\"})\n"
            "}\n"
            "print (<{guarded}>)\n"
            "print (<{done}>)\n"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"10\nyes\n")

    def test_compiled_nested_function_inside_while(self) -> None:
        completed = self.compile_and_run(
            "function outer(seed)\n"
            "{\n"
            "let total = 0\n"
            "let index = 0\n"
            "function weight(value) = value * 2 + seed\n"
            "while (index < 3)\n"
            "{\n"
            "set index = index + 1\n"
            "set total = total + weight(index)\n"
            "}\n"
            "return total\n"
            "}\n"
            "math_run outer(5) = ? -int64\n"
            "if up = (ok) goto (push ({answer} = {(in = up)}))\n"
            "print (<{answer}>)\n"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.strip(), b"27")

    def test_compiled_unbalanced_the_world_warns_and_restores(self) -> None:
        completed = self.compile_and_run(
            "start(normal) = {leak}\n"
            "{\n"
            "The_world (stop)\n"
            "push ({x} = {\"1\"})\n"
            "}\n"
            "math_run 1 + 1 = ? -int32\n"
            "if up = (ok) goto (push ({y} = {(in = up)}))\n"
            "print (<{y}>)\n"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.strip(), b"2")
        self.assertIn(b"left The_world stop depth unbalanced", completed.stderr)

    def test_compiled_record_operator_with_constructor_operand(self) -> None:
        completed = self.compile_and_run(
            "record Point { x, y }\n"
            "operator + Point = { left.x + right.x, left.y + right.y }\n"
            "function compute()\n"
            "{\n"
            "let base = Point(1, 2)\n"
            "let shifted = base + Point(3, 4)\n"
            "return shifted.x * 10 + shifted.y\n"
            "}\n"
            "math_run compute() = ? -int64\n"
            "if up = (ok) goto (push ({answer} = {(in = up)}))\n"
            "print (<{answer}>)\n"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.strip(), b"46")

    def test_compiled_generic_expression_function(self) -> None:
        completed = self.compile_and_run(
            "generic function square(value) = value * value\n"
            "math_run square(5.5) = ?\n"
            "if up = (ok) goto (push ({answer} = {(in = up)}))\n"
            "print (<{answer}>)\n"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.strip(), b"30.25")

    def test_compiled_user_function_in_loop_condition(self) -> None:
        completed = self.compile_and_run(
            "function below_three(value) = value < 3\n"
            "math_run 0 = ? -int32\n"
            "if up = (ok) goto (push ({counter} = {(in = up)}))\n"
            "while (below_three({counter}))\n"
            "{\n"
            "    math_run {counter} + 1 = ? -int32\n"
            "    if up = (ok) goto (push ({counter} = {(in = up)}))\n"
            "}\n"
            "print (<{counter}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), b"3")

    def test_compiled_array_and_matrix_expression_helpers(self) -> None:
        completed = self.compile_and_run(
            "math_run array_sum([1, 2, 3]) + dot([1, 2, 3], [4, 5, 6]) = ?\n"
            "if up = (ok) goto (push ({vector_result} = {(in = up)}))\n"
            "print (<{vector_result}>)\n"
            "math_run matrix2_det([[1, 2], [3, 4]]) = ?\n"
            "if up = (ok) goto (push ({determinant} = {(in = up)}))\n"
            "print (<{determinant}>)\n"
            "math_run matrix2_mul_entry([[1, 2], [3, 4]], [[5, 6], [7, 8]], 1, 0) = ?\n"
            "if up = (ok) goto (push ({matrix_result} = {(in = up)}))\n"
            "print (<{matrix_result}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"38\n-2\n43\n")

    def test_compiled_array_mutators_statistics_and_precision(self) -> None:
        completed = self.compile_and_run(
            "#%precision = 4\n"
            "math_run array_len(array_push(array_set([1, 2, 3], 1, 5), 9)) = ?\n"
            "if up = (ok) goto (push ({length} = {(in = up)}))\n"
            "print (<{length}>)\n"
            "math_run array_sum(array_set(array_push([1, 2], 4), 1, 5)) = ?\n"
            "if up = (ok) goto (push ({sum} = {(in = up)}))\n"
            "print (<{sum}>)\n"
            "math_run array_at(array_set([1, 2, 3], 1, 5), 1) = ?\n"
            "if up = (ok) goto (push ({changed} = {(in = up)}))\n"
            "print (<{changed}>)\n"
            "math_run mean([1, 2, 4]) = ?\n"
            "if up = (ok) goto (push ({average} = {(in = up)}))\n"
            "print (<{average}>)\n"
            "math_run std([1, 2, 3]) = ?\n"
            "if up = (ok) goto (push ({deviation} = {(in = up)}))\n"
            "print (<{deviation}>)\n"
            "math_run min([3, -1, 2]) = ?\n"
            "if up = (ok) goto (push ({minimum} = {(in = up)}))\n"
            "print (<{minimum}>)\n"
            "math_run max([3, -1, 2]) = ?\n"
            "if up = (ok) goto (push ({maximum} = {(in = up)}))\n"
            "print (<{maximum}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            completed.stdout.replace(b"\r\n", b"\n"),
            b"4\n10\n5\n2.333\n0.8165\n-1\n3\n",
        )

    def test_compiled_named_array_storage_mutation_and_dot(self) -> None:
        completed = self.compile_and_run(
            "array scores = [2, 4, 6]\n"
            "math_run array_sum(scores) = ?\n"
            "if up = (ok) goto (push ({initial} = {(in = up)}))\n"
            "print (<{initial}>)\n"
            "math_run array_sum(array_set(scores, 1, 10)) = ?\n"
            "if up = (ok) goto (push ({updated} = {(in = up)}))\n"
            "print (<{updated}>)\n"
            "math_run array_len(array_push(scores, 8)) = ?\n"
            "if up = (ok) goto (push ({length} = {(in = up)}))\n"
            "print (<{length}>)\n"
            "math_run dot(scores, [1, 1, 1, 1]) = ?\n"
            "if up = (ok) goto (push ({dot_result} = {(in = up)}))\n"
            "print (<{dot_result}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            completed.stdout.replace(b"\r\n", b"\n"),
            b"12\n18\n4\n26\n",
        )

    def test_compiled_inherited_named_array_is_a_copy(self) -> None:
        completed = self.compile_and_run(
            "array values = [1, 2]\n"
            "father = up\n"
            "start(normal) = {child}\n"
            "{\n"
            "    math_run array_sum(array_set(values, 0, 9)) = ?\n"
            "    if up = (ok) goto (push ({child_sum} = {(in = up)}))\n"
            "    print (<{child_sum}>)\n"
            "}\n"
            "math_run array_sum(values) = ?\n"
            "if up = (ok) goto (push ({parent_sum} = {(in = up)}))\n"
            "print (<{parent_sum}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            completed.stdout.replace(b"\r\n", b"\n"),
            b"11\n3\n",
        )

    def test_compiler_rejects_bare_array_as_scalar_math_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = Path(temporary_directory) / "array.cos"
            source_path.write_text(
                "array values = [1, 2]\nmath_run array_push(values, 3) = ?\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CompileError, "array values cannot be used as scalar"):
                compile_source(source_path)

    def test_compiler_rejects_array_aggregate_in_scalar_expression(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = Path(temporary_directory) / "array.cos"
            source_path.write_text(
                "array values = [1, 2]\n"
                "math_run 1 + array_set(values, 0, 3) = ?\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CompileError, "array values must be passed to an array function"):
                compile_source(source_path)

    def test_compiled_array_statistics_reject_empty_arrays(self) -> None:
        completed = self.compile_and_run("math_run mean([]) = ?\n")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(b"mean requires a non-empty array", completed.stderr)

    def test_compiler_native_vector_library_linking_and_simd_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = Path(temporary_directory) / "blas.cos"
            source_path.write_text(
                "math_run blas_dot([1, 2], [3, 4]) = ?\n",
                encoding="utf-8",
            )
            result = subprocess.CompletedProcess([], 0, "", "")
            with patch("cosmic_interpreter.compiler.subprocess.run", return_value=result) as run:
                c_path, _output_path = compile_source(source_path, simd=True)
            command = run.call_args.args[0]
            self.assertIn("-O3", command)
            self.assertIn("-ftree-vectorize", command)
            self.assertIn("-lblas", command)
            self.assertIn("ddot_", c_path.read_text(encoding="utf-8"))

            source_path.write_text(
                "math_run lapack_determinant2([[1, 2], [3, 4]]) = ?\n",
                encoding="utf-8",
            )
            with patch("cosmic_interpreter.compiler.subprocess.run", return_value=result) as run:
                c_path, _output_path = compile_source(source_path)
            command = run.call_args.args[0]
            self.assertIn("-O2", command)
            self.assertIn("-llapack", command)
            self.assertIn("-lblas", command)
            self.assertIn("dgetrf_", c_path.read_text(encoding="utf-8"))
            with patch("cosmic_interpreter.compiler.subprocess.run", return_value=result) as run:
                compile_source(source_path, fast_math=True)
            self.assertIn("-O2", run.call_args.args[0])
            self.assertIn("-ffast-math", run.call_args.args[0])

    def test_math_expression_can_override_global_integer_mode(self) -> None:
        source = """\
#%math = int32
math_run 1 / 2 = ? -float64
if up = (ok) goto (push ({half} = {(in = up)}))
print (<{half}>)
"""
        completed = self.compile_and_run(source)
        self.assertEqual(completed.returncode, 0)
        self.assertIn(b"0.5", completed.stdout)

    def test_compiled_int32_rejects_fractional_scientific_result(self) -> None:
        completed = self.compile_and_run(
            "#%math = int32\nmath_run sqrt(2) = ?\n"
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(b"result is outside int32 range", completed.stderr)

    def test_compiled_int64_keeps_full_integer_precision(self) -> None:
        source = """\
#%math = int64
math_run 9007199254740993 + 0 = ?
if up = (ok) goto (push ({answer} = {(in = up)}))
print (<{answer}>)
"""
        completed = self.compile_and_run(source)
        self.assertEqual(completed.returncode, 0)
        self.assertIn(b"9007199254740993", completed.stdout)

    def test_compiled_science_control_flow_and_tape(self) -> None:
        source = """\
#%math = float64
tape_create (work)
tape_write (work) = {3}
while (tape(work) > 0)
{
    if (tape(work) > 1)
    {
        tape_write (work) = {1}
    }
    else
    {
        tape_write (work) = {0}
    }
}
tape_read (work) = ?
if up {*} = (0) goto (push ({state} = {"halted"}))
print (<{state}>)
math_run sin(pi / 2) + sqrt(9) + log(8, 2) = ?
if up = (ok) goto (push ({answer} = {(in = up)}))
print (<{answer}>)
"""
        completed = self.compile_and_run(source)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            completed.stdout.replace(b"\r\n", b"\n"),
            b"halted\n7\n",
        )

    def test_compiled_atan_and_machin_pi_example(self) -> None:
        source_path = Path(__file__).parents[1] / "examples" / "pi_machin.cos"
        completed = self.compile_and_run(source_path.read_text(encoding="utf-8"))
        self.assertEqual(completed.returncode, 0)
        self.assertAlmostEqual(float(completed.stdout.strip()), 3.141592653589793)

    def test_compiled_rag_returns_a_die_roll_and_validates_sides(self) -> None:
        completed = self.compile_and_run(
            "#%math = int32\nmath_run rag (max : (2 + 4)) (min : (1 + 0)) = ?\n"
            "if up = (ok) goto (push ({roll} = {(in = up)}))\n"
            "print (<{roll}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn(int(completed.stdout.strip()), range(1, 7))

        fixed = self.compile_and_run(
            "#%math = int32\nmath_run rag (max : -3) (min : -3) = ?\n"
            "if up = (ok) goto (push ({roll} = {(in = up)}))\n"
            "print (<{roll}>)\n"
        )
        self.assertEqual(fixed.returncode, 0)
        self.assertEqual(fixed.stdout.strip(), b"-3")

        invalid = self.compile_and_run("math_run rag (max : 1) (min : 2) = ?\n")
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn(b"rag requires int32 bounds", invalid.stderr)

        fractional = self.compile_and_run("math_run rag (max : 2.5) (min : 1) = ?\n")
        self.assertNotEqual(fractional.returncode, 0)
        self.assertIn(b"rag requires int32 bounds", fractional.stderr)

    def test_compiled_chr_ord_round_trip_unicode(self) -> None:
        source = """\
#%math = int32
push ({source} = {"你"})
math_run ord({source}) = ?
if up = (ok) goto (push ({codepoint} = {(in = up)}))
math_run chr({codepoint}) = ?
if up = (ok) goto (push ({character} = {(in = up)}))
print (<{codepoint}>)
print (<{character}>)
"""
        completed = self.compile_and_run(source)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            completed.stdout.replace(b"\r\n", b"\n"),
            "20320\n你\n".encode("utf-8"),
        )

    def test_compiled_chr_rejects_invalid_unicode_scalar(self) -> None:
        completed = self.compile_and_run("math_run chr(55296) = ?\n")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(b"valid Unicode scalar", completed.stderr)

    def test_compiled_ord_rejects_multiple_characters(self) -> None:
        completed = self.compile_and_run('math_run ord("ab") = ?\n')
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(b"exactly one Unicode character", completed.stderr)

    def test_compiled_string_concat_strlen_and_char_at(self) -> None:
        source = """\
push ({a} = {"你好"})
push ({b} = {"world"})
push ({d} = {a} (and) {b})
push ({greeting} = {"Hi, "} (and) {a})
push ({legacy} = {a}{b})
push ({digits} = {"123"})
math_run strlen({d}) = ? -int32
if up = (ok) goto (push ({length} = {(in = up)}))
math_run char_at({d}, 1) = ?
if up = (ok) goto (push ({second} = {(in = up)}))
math_run strlen({digits}) = ? -int32
if up = (ok) goto (push ({digit_length} = {(in = up)}))
math_run char_at({digits}, 1) = ?
if up = (ok) goto (push ({digit} = {(in = up)}))
print (<{d}>)
print (<{greeting}>)
print (<{length}>)
print (<{second}>)
print (<{digit_length}>)
print (<{digit}>)
print (<{legacy}>)
"""
        completed = self.compile_and_run(source)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            completed.stdout.replace(b"\r\n", b"\n"),
            "你好world\nHi, 你好\n7\n好\n3\n2\n你好world\n".encode("utf-8"),
        )

    def test_compiled_char_at_rejects_out_of_range(self) -> None:
        completed = self.compile_and_run(
            'push ({word} = {"ab"})\nmath_run char_at({word}, 2) = ?\n'
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(b"char_at index is outside the string", completed.stderr)

    def test_compiled_numeric_variable_while_loop(self) -> None:
        source = """\
#%math = int32
math_run 0 = ?
if up = (ok) goto (push ({counter} = {(in = up)}))
while ({counter} < 5)
{
    math_run {counter} + 1 = ?
    if up = (ok) goto (push ({counter} = {(in = up)}))
}
print (<{counter}>)
"""
        completed = self.compile_and_run(source)
        self.assertEqual(completed.returncode, 0)
        self.assertIn(b"5", completed.stdout)

    def test_compiles_scientific_tape_example(self) -> None:
        source_path = Path(__file__).parents[1] / "examples" / "scientific_tape.cos"
        completed = self.compile_and_run(source_path.read_text(encoding="utf-8"))
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            completed.stdout.replace(b"\r\n", b"\n"),
            b"4\n5\nhalted\n",
        )

    def test_compiles_bubble_sort_example(self) -> None:
        source_path = Path(__file__).parents[1] / "examples" / "bubble_sort.cos"
        completed = self.compile_and_run(source_path.read_text(encoding="utf-8"))
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            completed.stdout.replace(b"\r\n", b"\n"),
            b"1\n2\n3\n4\n",
        )

    def test_compiled_tape_moves_and_keeps_cells(self) -> None:
        source = """\
tape_create (memory)
tape_write (memory) = {7}
tape_move (memory) = (right)
tape_read (memory) = ?
if up {*} = (0) goto (push ({right_cell} = {"ok"}))
tape_write (memory) = {1}
tape_move (memory) = (left)
tape_read (memory) = ?
if up {*} = (7) goto (push ({result} = {"tape_ok"}))
print (<{result}>)
"""
        completed = self.compile_and_run(source)
        self.assertEqual(completed.returncode, 0)
        self.assertIn(b"tape_ok", completed.stdout)

    def test_compiled_inherited_variable_is_an_independent_copy(self) -> None:
        source = """\
push ({value} = {"parent"})
father = up
start(normal) = {child}
{
    push ({value} = {"child"})
}
print (<{value}>)
"""
        completed = self.compile_and_run(source)
        self.assertEqual(completed.returncode, 0)
        self.assertIn(b"parent", completed.stdout)

    def test_rejects_encodings_not_supported_by_c_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = Path(temporary_directory) / "program.cos"
            source_path.write_text("#word = shift_jis\n", encoding="utf-8")
            with self.assertRaisesRegex(CompileError, "not supported by the C backend"):
                compile_source(source_path)

    def test_rejects_call_without_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = Path(temporary_directory) / "program.cos"
            source_path.write_text("call = (unknown_component)\n", encoding="utf-8")
            with self.assertRaisesRegex(CompileError, "has not been imported"):
                compile_source(source_path)

    def test_refuses_to_overwrite_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = Path(temporary_directory) / "program.cos"
            source_path.write_text("hello\n", encoding="utf-8")
            with self.assertRaisesRegex(CompileError, "must not overwrite the source"):
                compile_source(source_path, output_path=source_path)

    def test_memory_report_cannot_overwrite_project_inputs_or_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = Path(temporary_directory) / "program.cos"
            source_path.write_text("math_run 1 = ?\n", encoding="utf-8")
            with self.assertRaisesRegex(CompileError, "memory report path"):
                compile_source(source_path, memory_report_path=source_path)

    def test_compiler_rejects_context_over_configured_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = Path(temporary_directory) / "program.cos"
            source_path.write_text('var greeting = {"hello"}\n', encoding="utf-8")
            with self.assertRaisesRegex(
                CompileError,
                "static Context requires 112 bytes.*limit of 63 bytes",
            ):
                compile_source(source_path, max_context_bytes=63)

    def test_context_budget_must_be_positive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = Path(temporary_directory) / "program.cos"
            source_path.write_text("math_run 1 = ?\n", encoding="utf-8")
            with self.assertRaisesRegex(CompileError, "max_context_bytes must be greater than zero"):
                compile_source(source_path, max_context_bytes=0)

    def test_compiler_cli_defaults_memory_report_path_from_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            source = directory / "program.cos"
            executable = directory / "custom-name.exe"
            source.write_text("math_run 1 + 1 = ?\n", encoding="utf-8")
            self.assertEqual(
                compiler_main([str(source), "-o", str(executable), "--memory-report"]),
                0,
            )
            self.assertTrue(executable.with_suffix(".memreport").is_file())


    def test_compiled_array_rebinding_matches_interpreter(self) -> None:
        completed = self.compile_and_run(
            "array a = [1, 2, 3]\n"
            "array a = array_set(a, 1, 99)\n"
            "math_run array_sum(a) = ? -int64\n"
            "if up = (ok) goto (push ({total} = {(in = up)}))\n"
            "print (<{total}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), b"103")

    def test_compiled_err_count_and_undefined_print(self) -> None:
        completed = self.compile_and_run(
            "print (<{missing}>)\n"
            "math_run err_count() = ? -int64\n"
            "if up = (ok) goto (push ({errors} = {(in = up)}))\n"
            "print (<{errors}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), b"1")

    def test_compiled_exit_stops_program(self) -> None:
        completed = self.compile_and_run(
            'math_run 1 + 1 = ? -int32\nexit(7)\nprint (<{"unreachable"}>)\n'
        )
        self.assertEqual(completed.returncode, 7)
        self.assertNotIn(b"unreachable", completed.stdout)

    @unittest.skipUnless(os.name == "nt", "kernel32 is only available on Windows")
    def test_compiled_dll_call_and_status(self) -> None:
        completed = self.compile_and_run(
            "#dll\n"
            "call = (DLL)\n"
            "#extern gettickcount () -> DWORD as GetTickCount\n"
            "calldll = (kernel32)\n"
            "cdll = come (kernel32) (gettickcount)\n"
            "if up = (ok) goto (push ({ticks} = {(in = up)}))\n"
            "cdll = status (kernel32)\n"
            "if up = (ok) goto (push ({state} = {(in = up)}))\n"
            "math_run err_count() = ? -int64\n"
            "if up = (ok) goto (push ({errors} = {(in = up)}))\n"
            "print (<{ticks}>)\n"
            "print (<{state}>)\n"
            "print (<{errors}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        lines = completed.stdout.replace(b"\r\n", b"\n").split(b"\n")
        self.assertTrue(lines[0].isdigit(), lines)
        self.assertEqual(lines[1], b"loaded")
        self.assertEqual(lines[2], b"0")

    def test_compiled_atom_lock_chain_and_shared_context(self) -> None:
        completed = self.compile_and_run(
            "#atom\n"
            "call = (atom)\n"
            'push ({counter} = {"0"})\n'
            "start(normal) = {worker}\n"
            "{\n"
            "    lock {worker}\n"
            "    {\n"
            "        math_run {counter} + 1 = ? -int64\n"
            "        if up = (ok) goto (push ({counter} = {(in = up)}))\n"
            "    }\n"
            "}\n"
            "proton {chain} = {worker} -> {worker} -> {worker}\n"
            "nuclear_reactor {main} = {chain}\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, b"")

    def test_compiled_start_outside_chain_runs_synchronously(self) -> None:
        # 文件里出现 atom 语句后，未被 proton 链调度的执行块仍要同步执行，
        # 否则 start 之后的 dog 会看到 idle 并误报死锁（与解释器不一致）。
        completed = self.compile_and_run(
            "#execute\n"
            "#math\n"
            "#%math = int64\n"
            "math_run 5 = ? -int64\n"
            "if up = (ok) goto (push ({seed} = {(in = up)}))\n"
            "call = (execute)\n"
            "father = up\n"
            "start(normal) = {doubler}\n"
            "{\n"
            "    math_run {seed} * 2 = ? -int64\n"
            "    if up = (ok) goto (push ({seed} = {(in = up)}))\n"
            '    print (<{"block seed="} (and) {seed}>)\n'
            "}\n"
            "dog(doubler) = (pass : ok) (error : error)\n"
            "#atom\n"
            "start(normal) = {worker}\n"
            "{\n"
            "    math_run 1 + 1 = ? -int32\n"
            "}\n"
            "proton {chain} = {worker}\n"
            "nuclear_reactor {main} = {chain}\n"
            "dog(worker) = (pass : ok) (error : error)\n"
            'print (<{"outer seed="} (and) {seed}>)\n'
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout.replace(b"\r\n", b"\n").split(b"\n")[:2],
            [b"block seed=10", b"outer seed=5"],
        )

    def test_compiled_atom_n_bomb_marks_block_stopped(self) -> None:
        completed = self.compile_and_run(
            "#atom\n"
            "start(normal) = {worker}\n"
            "{\n"
            "    math_run 1 + 1 = ? -int32\n"
            "}\n"
            "proton {chain} = {worker}\n"
            "nuclear_reactor {main} = {chain}\n"
            "n-bomb {worker}\n"
            'dog(worker) = {stopped}\n'
            'print (<{"done"}>)\n'
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), b"done")


    def test_compiled_lock_timeout_blocks_until_free(self) -> None:
        # The compiled lock polls with a deadline instead of blocking forever.
        # A re-entrant mutex cannot time out against its own thread, so this
        # asserts the polling path is generated and still acquires when free.
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            source_path = directory / "lock_timeout.cos"
            source_path.write_text(
                "#atom\n"
                "push ({counter} = {\"0\"})\n"
                "father = up\n"
                "start(normal) = {worker}\n"
                "{\n"
                "    lock {worker} -timeout 2\n"
                "    {\n"
                "        math_run {counter} + 1 = ? -int64\n"
                "        if up = (ok) goto (push ({counter} = {(in = up)}))\n"
                "    }\n"
                "}\n"
                "proton {chain} = {worker} -> {worker}\n"
                "nuclear_reactor {main} = {chain}\n"
                "dog(worker) = (pass : ok) (error : error)\n"
                "print (<{counter}>)\n",
                encoding="utf-8",
            )
            c_path, output_path = compile_source(source_path)
            generated = c_path.read_text(encoding="utf-8")
            self.assertIn("cosmic_mutex_trylock", generated)
            self.assertIn("lock timeout:", generated)
            build = subprocess.run(
                [
                    "gcc", "-std=c11", "-D_DEFAULT_SOURCE", "-O2",
                    str(c_path), "-lm", "-o", str(output_path),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            completed = subprocess.run(
                [str(output_path)], check=False, capture_output=True, timeout=30
            )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), b"2")
        self.assertEqual(completed.stderr, b"")


    def test_compiled_bigint_override_calls_recursive_functions(self) -> None:
        completed = self.compile_and_run(
            "function fact(n) = choose(n <= 1, 1, n * fact(n - 1))\n"
            "math_run fact(20) = ? -bigint\n"
            "if up = (ok) goto (push ({r} = {(in = up)}))\n"
            "print (<{r}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), b"2432902008176640000")


    #%% ---- v5: bitwise, hex, uint32 and bytes parity with the interpreter ----%%
    def test_compiled_bitwise_operators(self) -> None:
        completed = self.compile_and_run(
            "#%math = int64\n"
            "math_run 12 & 10 = ?\n"
            "if up = (ok) goto (push ({a} = {(in = up)}))\n"
            "math_run 12 | 10 = ?\n"
            "if up = (ok) goto (push ({b} = {(in = up)}))\n"
            "math_run 12 ^ 10 = ?\n"
            "if up = (ok) goto (push ({c} = {(in = up)}))\n"
            "math_run 1 << 10 = ?\n"
            "if up = (ok) goto (push ({d} = {(in = up)}))\n"
            "math_run ~5 = ?\n"
            "if up = (ok) goto (push ({e} = {(in = up)}))\n"
            "print (<{a}>)\n"
            "print (<{b}>)\n"
            "print (<{c}>)\n"
            "print (<{d}>)\n"
            "print (<{e}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"8\n14\n6\n1024\n-6\n")

    def test_compiled_hex_and_unhex(self) -> None:
        completed = self.compile_and_run(
            "math_run hex(255, 4) = ?\n"
            "if up = (ok) goto (push ({a} = {(in = up)}))\n"
            "print (<{a}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), b"00ff")

    def test_compiled_uint32_wraps_around(self) -> None:
        completed = self.compile_and_run(
            "#%math = uint32\n"
            "math_run 4294967295 + 2 = ?\n"
            "if up = (ok) goto (push ({a} = {(in = up)}))\n"
            "math_run 4294967295 >> 1 = ?\n"
            "if up = (ok) goto (push ({b} = {(in = up)}))\n"
            "print (<{a}>)\n"
            "print (<{b}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"1\n2147483647\n")

    def test_compiled_uint32_functions_use_unsigned_arithmetic(self) -> None:
        completed = self.compile_and_run(
            "#%math = uint32\n"
            "function rot(v) = ((v << 1) & 255) ^ ((v >> 7) * 27)\n"
            "math_run rot(128) = ?\n"
            "if up = (ok) goto (push ({a} = {(in = up)}))\n"
            "print (<{a}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), b"27")

    def test_compiled_bytes_arrays(self) -> None:
        completed = self.compile_and_run(
            "bytes data = [1, 2, 3]\n"
            "bytes_set(data, 1, 250)\n"
            "math_run bytes_get(data, 1) = ?\n"
            "if up = (ok) goto (push ({v} = {(in = up)}))\n"
            "math_run bytes_len(data) = ?\n"
            "if up = (ok) goto (push ({n} = {(in = up)}))\n"
            "print (<{v}>)\n"
            "print (<{n}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"250\n3\n")

    def test_compiled_sha256_matches_fips_vector(self) -> None:
        example = Path(__file__).resolve().parent.parent / "examples" / "sha256.cos"
        completed = self.compile_and_run(example.read_text(encoding="utf-8"))
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            completed.stdout.strip(),
            b"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_compiled_aes128_matches_fips197_vector(self) -> None:
        example = Path(__file__).resolve().parent.parent / "examples" / "aes128.cos"
        completed = self.compile_and_run(example.read_text(encoding="utf-8"))
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), b"69c4e0d86a7b0430d8cdb78070b4c55a")



    #%% ---- v5: NxM matrices match the interpreter ----%%
    def test_compiled_matrix_multiplication_and_indexing(self) -> None:
        completed = self.compile_and_run(
            "push ({p} = matrix_mul([[1,2],[3,4]], [[5,6],[7,8]]))\n"
            "math_run matrix_at({p}, 0, 1) = ?\n"
            "if up = (ok) goto (push ({a} = {(in = up)}))\n"
            "math_run matrix_at({p}, 1, 1) = ?\n"
            "if up = (ok) goto (push ({b} = {(in = up)}))\n"
            "print (<{a}>)\n"
            "print (<{b}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"22\n50\n")

    def test_compiled_matrix_transpose_keeps_non_square_shape(self) -> None:
        completed = self.compile_and_run(
            "push ({t} = matrix_transpose([[1,2,3],[4,5,6]]))\n"
            "math_run matrix_rows({t}) = ?\n"
            "if up = (ok) goto (push ({r} = {(in = up)}))\n"
            "math_run matrix_cols({t}) = ?\n"
            "if up = (ok) goto (push ({c} = {(in = up)}))\n"
            "math_run matrix_at({t}, 2, 1) = ?\n"
            "if up = (ok) goto (push ({v} = {(in = up)}))\n"
            "print (<{r}>)\n"
            "print (<{c}>)\n"
            "print (<{v}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"3\n2\n6\n")

    def test_compiled_matrix_inverses(self) -> None:
        completed = self.compile_and_run(
            "push ({m} = matrix2_inverse([[4,7],[2,6]]))\n"
            "math_run matrix_at({m}, 0, 0) = ?\n"
            "if up = (ok) goto (push ({a} = {(in = up)}))\n"
            "math_run matrix_at({m}, 1, 1) = ?\n"
            "if up = (ok) goto (push ({b} = {(in = up)}))\n"
            "push ({n} = matrix3_inverse([[1,2,3],[0,1,4],[5,6,0]]))\n"
            "math_run matrix_at({n}, 0, 0) = ?\n"
            "if up = (ok) goto (push ({c} = {(in = up)}))\n"
            "print (<{a}>)\n"
            "print (<{b}>)\n"
            "print (<{c}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"0.6\n0.4\n-24\n")

    def test_compiled_two_dimensional_array_declaration(self) -> None:
        completed = self.compile_and_run(
            "array A = [[1,2],[3,4]]\n"
            "math_run matrix_at(A, 1, 0) = ?\n"
            "if up = (ok) goto (push ({v} = {(in = up)}))\n"
            "print (<{v}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), b"3")

    def test_compiled_singular_matrix_inverse_fails(self) -> None:
        completed = self.compile_and_run(
            "push ({m} = matrix2_inverse([[1,2],[2,4]]))\n"
            "print (<{m}>)\n"
        )
        self.assertNotEqual(completed.returncode, 0)



    #%% ---- v5: solve_linear matches the interpreter ----%%
    def test_compiled_solve_linear(self) -> None:
        completed = self.compile_and_run(
            "push ({x} = solve_linear([[2,1,-1],[-3,-1,2],[-2,1,2]], [8,-11,-3]))\n"
            "math_run array_at({x}, 0) = ?\n"
            "if up = (ok) goto (push ({a} = {(in = up)}))\n"
            "math_run array_at({x}, 1) = ?\n"
            "if up = (ok) goto (push ({b} = {(in = up)}))\n"
            "math_run array_at({x}, 2) = ?\n"
            "if up = (ok) goto (push ({c} = {(in = up)}))\n"
            "print (<{a}>)\n"
            "print (<{b}>)\n"
            "print (<{c}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"2\n3\n-1\n")

    def test_compiled_solve_linear_with_named_arrays(self) -> None:
        completed = self.compile_and_run(
            "array A = [[1,1],[2,3]]\n"
            "array B = [3,8]\n"
            "push ({x} = solve_linear(A, B))\n"
            "math_run array_at({x}, 1) = ?\n"
            "if up = (ok) goto (push ({a} = {(in = up)}))\n"
            "print (<{a}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout.strip(), b"2")

    def test_compiled_solve_linear_rejects_singular_systems(self) -> None:
        completed = self.compile_and_run(
            "push ({x} = solve_linear([[1,2],[2,4]], [1,2]))\n"
            "print (<{x}>)\n"
        )
        self.assertNotEqual(completed.returncode, 0)



    #%% ---- v5: statistics and integration match the interpreter ----%%
    def test_compiled_statistics(self) -> None:
        completed = self.compile_and_run(
            "math_run median([4,1,3,2]) = ?\n"
            "if up = (ok) goto (push ({a} = {(in = up)}))\n"
            "math_run variance([1,2,3,4]) = ?\n"
            "if up = (ok) goto (push ({b} = {(in = up)}))\n"
            "math_run correlation([1,2,3],[2,4,6]) = ?\n"
            "if up = (ok) goto (push ({c} = {(in = up)}))\n"
            "math_run percentile([1,2,3,4], 25) = ?\n"
            "if up = (ok) goto (push ({d} = {(in = up)}))\n"
            "print (<{a}>)\n"
            "print (<{b}>)\n"
            "print (<{c}>)\n"
            "print (<{d}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            completed.stdout.replace(b"\r\n", b"\n"), b"2.5\n1.25\n1\n1.75\n"
        )

    def test_compiled_integration(self) -> None:
        completed = self.compile_and_run(
            "math_run trapz([0,1,4,9], 1) = ?\n"
            "if up = (ok) goto (push ({a} = {(in = up)}))\n"
            "math_run simpson([0,1,4,9,16], 1) = ?\n"
            "if up = (ok) goto (push ({b} = {(in = up)}))\n"
            "print (<{a}>)\n"
            "print (<{b}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            completed.stdout.replace(b"\r\n", b"\n"), b"9.5\n21.3333333333333\n"
        )



    #%% ---- v5: special functions match the interpreter ----%%
    def test_compiled_special_functions(self) -> None:
        completed = self.compile_and_run(
            "math_run lgamma(5) = ?\n"
            "if up = (ok) goto (push ({a} = {(in = up)}))\n"
            "math_run erfc(0.5) = ?\n"
            "if up = (ok) goto (push ({b} = {(in = up)}))\n"
            "math_run beta(2,3) = ?\n"
            "if up = (ok) goto (push ({c} = {(in = up)}))\n"
            "math_run zeta(2) = ?\n"
            "if up = (ok) goto (push ({d} = {(in = up)}))\n"
            "math_run zeta(3) = ?\n"
            "if up = (ok) goto (push ({e} = {(in = up)}))\n"
            "print (<{a}>)\n"
            "print (<{b}>)\n"
            "print (<{c}>)\n"
            "print (<{d}>)\n"
            "print (<{e}>)\n"
        )
        self.assertEqual(completed.returncode, 0)
        values = completed.stdout.replace(b"\r\n", b"\n").split()
        self.assertEqual(len(values), 5)
        expected = [
            math.lgamma(5.0),
            math.erfc(0.5),
            1.0 / 12.0,
            math.pi**2 / 6,
            1.2020569031595943,
        ]
        for actual, wanted in zip(values, expected):
            self.assertAlmostEqual(float(actual), wanted, places=12)



    #%% ---- v5: new examples compile to the same output as the interpreter ----%%
    def test_compiled_bitops_example(self) -> None:
        example = Path(__file__).resolve().parent.parent / "examples" / "bitops.cos"
        completed = self.compile_and_run(example.read_text(encoding="utf-8"))
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            completed.stdout.replace(b"\r\n", b"\n").split(),
            [b"8", b"14", b"6", b"1024", b"4294967290", b"878082066",
             b"1", b"4294967295", b"2147483647", b"ff", b"beef", b"6", b"99"],
        )

    def test_compiled_linear_algebra_example(self) -> None:
        example = Path(__file__).resolve().parent.parent / "examples" / "linear_algebra.cos"
        completed = self.compile_and_run(example.read_text(encoding="utf-8"))
        self.assertEqual(completed.returncode, 0)
        values = completed.stdout.replace(b"\r\n", b"\n").split()
        expected = [b"2", b"3", b"6", b"3", b"2", b"25", b"100", b"3", b"6", b"0.6", b"0.4", b"-24"]
        self.assertEqual(values[:12], expected)
        self.assertAlmostEqual(float(values[12]), 1.0, places=12)
        self.assertEqual(values[13:], [b"1", b"0", b"2", b"1", b"2", b"2", b"3", b"-1"])

    def test_compiled_numerical_example(self) -> None:
        example = Path(__file__).resolve().parent.parent / "examples" / "numerical.cos"
        completed = self.compile_and_run(example.read_text(encoding="utf-8"))
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            completed.stdout.replace(b"\r\n", b"\n").split(),
            [b"22", b"21.3333333333", b"44", b"22", b"18", b"15.5", b"151.666666667",
             b"12.3153021346", b"4", b"42", b"6", b"4", b"15.5", b"42", b"1", b"-1",
             b"3.17805383035", b"1", b"0.0833333333333", b"1.64493406685",
             b"1.08232323371", b"0.34375", b"0.3359375", b"0.333333333333"],
        )



    def test_compiled_named_one_dimensional_array_is_not_a_matrix(self) -> None:
        completed = self.compile_and_run(
            "array flat = [1,2,3,4,5,6]\n"
            "math_run matrix_rows(flat) = ?\n"
            "print (<{flat}>)\n"
        )
        self.assertNotEqual(completed.returncode, 0)

    #%% ---- v8: 循环直接下沉为真 C 循环（回归锁） ----%%
    def test_block_while_generates_native_c_loop(self) -> None:
        source = """\
#math
call = (math)
push ({n} = {"0"})
while ({n} < 5)
{
    math_run {n} + 1 = ?
    if up = (ok) goto (push ({n} = {(in = up)}))
    math_run {n} * 2 = ?
    if up = (ok) goto (push ({doubled} = {(in = up)}))
}
print (<{n}>)
"""
        generated = generate_c(parse(source))
        # 顶层 while 必须是真 C 循环，而不是回调解释器逐句执行。
        self.assertIn("while (", generated)
        self.assertNotIn("cosmic_run_statement", generated)
        self.assertNotIn("dispatch_statement", generated)
        completed = self.compile_and_run(source)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"5\n")

    def test_function_while_generates_native_c_loop(self) -> None:
        source = """\
function total(n)
{
let i = 0
let s = 0
while ({i} < {n})
{
set s = {s} + {i}
set i = {i} + 1
}
return {s}
}
math_run total(5) = ? -int64
if up = (ok) goto (push ({answer} = {(in = up)}))
print (<{answer}>)
"""
        generated = generate_c(parse(source))
        # 只看用户函数体，函数体内的 while 也应是真 C 循环。
        function_start = generated.index("user_total(double")
        function_end = generated.index("int main(void) {", function_start)
        self.assertIn("while (", generated[function_start:function_end])
        completed = self.compile_and_run(source)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(completed.stdout.replace(b"\r\n", b"\n"), b"10\n")

    def test_hop_pipeline_compiles_and_matches_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_path = Path(temporary_directory) / "lines.txt"
            data_path.write_text("a\nb\nc", encoding="utf-8")
            # HOP 中文动词由 parse() 展开，编译与解释应走同一语义。
            source = (
                f'打开 "{data_path.as_posix()}" 存 文本\n'
                "行数 文本 存 总行\n"
                "print (<{总行}>)\n"
            )
            completed = self.compile_and_run(source)
            interpreter_output = io.StringIO()
            Interpreter(output=interpreter_output).run(parse(source))
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        compiled_output = completed.stdout.decode(errors="replace").replace("\r\n", "\n")
        self.assertEqual(compiled_output, interpreter_output.getvalue())
        self.assertEqual(compiled_output, "3\n")

    def test_compiled_ask_secret_reads_piped_stdin(self) -> None:
        source = 'ask ({pwd}) (<{"密码: "}>) -secret\nprint (<{pwd}>)\n'
        # stdin 是管道时没有终端回显，读取必须回落到普通 fgets 并拿到正确值
        self.assertIn("read_secret_line(cosmic_input", generate_c(parse(source)))
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            source_path = directory / "secret.cos"
            executable_path = directory / "secret.exe"
            source_path.write_text(source, encoding="utf-8")
            compile_source(source_path, output_path=executable_path)
            completed = subprocess.run(
                [str(executable_path)],
                input=b"s3cret\n",
                check=False,
                capture_output=True,
                timeout=15,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        self.assertEqual(
            completed.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n"),
            "密码: s3cret\n",
        )


if __name__ == "__main__":
    unittest.main()
