"""v10 阶段 4（B3）：函数体内可以 print。

没有它就没法在函数里调试，也写不出「边生成边输出」的代码生成函数。
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from cosmic_interpreter.compiler import compile_source
from cosmic_interpreter.parser import parse
from cosmic_interpreter.runtime import Interpreter


def run_interpreted(source: str) -> str:
    output = io.StringIO()
    Interpreter(
        output=output, error_output=io.StringIO(), sleep=lambda _seconds: None
    ).run(parse(source))
    return output.getvalue().replace("\r\n", "\n")


@unittest.skipUnless(shutil.which("gcc"), "GCC is required for native compiler tests")
def run_compiled(source: str) -> str:
    with tempfile.TemporaryDirectory() as temporary_directory:
        directory = Path(temporary_directory)
        source_path = directory / "program.cos"
        source_path.write_bytes(source.encode("utf-8"))
        _c_path, executable = compile_source(
            source_path, output_path=directory / "program.exe"
        )
        completed = subprocess.run(
            [str(executable)], check=False, capture_output=True, timeout=120
        )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr.decode(errors="replace"))
    return completed.stdout.decode(errors="replace").replace("\r\n", "\n")


class FunctionPrintTests(unittest.TestCase):
    def assert_parity(self, source: str, expected: str) -> None:
        self.assertEqual(run_interpreted(source), expected)
        if shutil.which("gcc"):
            self.assertEqual(run_compiled(source), expected)

    def test_print_inside_function(self) -> None:
        self.assert_parity(
            "function show(n)\n"
            "{\n"
            'print (<"enter: " (and) {n}>)\n'
            "return n\n"
            "}\n"
            "math_run show(7) = ?\n",
            "enter: 7\n",
        )

    def test_print_inside_loop_prints_once_per_iteration(self) -> None:
        self.assert_parity(
            "function count(n)\n"
            "{\n"
            "let i = 0\n"
            "while (i < n)\n"
            "{\n"
            "print (<{i}>)\n"
            "set i = i + 1\n"
            "}\n"
            "return i\n"
            "}\n"
            "math_run count(3) = ?\n",
            "0\n1\n2\n",
        )

    def test_print_text_parameter(self) -> None:
        self.assert_parity(
            "function say(t : text)\n"
            "{\n"
            "print (<{t}>)\n"
            "return 1\n"
            "}\n"
            'math_run say("hi") = ?\n',
            "hi\n",
        )

    def test_print_mixed_text_and_number(self) -> None:
        self.assert_parity(
            "function report(name : text, score)\n"
            "{\n"
            'print (<{name} (and) " = " (and) {score}>)\n'
            "return score\n"
            "}\n"
            'math_run report("ann", 95) = ?\n',
            "ann = 95\n",
        )

    def test_recursive_print_happens_exactly_once_per_call(self) -> None:
        # B1 修好之前，同一次调用会被求值两次，print 就会重复输出。
        self.assert_parity(
            "function down(n)\n"
            "{\n"
            "if (n <= 0)\n"
            "{\n"
            "return 0\n"
            "}\n"
            "print (<{n}>)\n"
            "return down(n - 1)\n"
            "}\n"
            "math_run down(3) = ?\n",
            "3\n2\n1\n",
        )

    def test_print_numeric_text_conversion_uses_precision(self) -> None:
        self.assert_parity(
            "function show(x)\n"
            "{\n"
            "print (<{x}>)\n"
            "return 0\n"
            "}\n"
            "math_run show(1 / 3) = ?\n",
            "0.333333333333333\n",
        )

    def test_invalid_print_inside_function_is_a_parse_error(self) -> None:
        with self.assertRaises(Exception) as caught:
            run_interpreted(
                "function bad()\n"
                "{\n"
                "print nope\n"
                "return 0\n"
                "}\n"
            )
        self.assertIn("print", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
