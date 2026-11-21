"""v10 阶段 5（B4）：循环体里的 `var` 是每轮一次新的绑定。

之前的实现复用同一个 Context，第二圈就报 `variable 'P' is already declared`，
于是「循环 + 局部变量」这种最普通的写法直接不可用。
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


class LoopVarTests(unittest.TestCase):
    def assert_parity(self, source: str, expected: str) -> None:
        self.assertEqual(run_interpreted(source), expected)
        if shutil.which("gcc"):
            self.assertEqual(run_compiled(source), expected)

    def test_var_inside_loop_body_runs_every_iteration(self) -> None:
        self.assert_parity(
            'var i = {"0"}\n'
            "while ({i} < 3)\n"
            "{\n"
            'var P = {"x"}\n'
            "print (<{P}>)\n"
            "math_run {i} + 1 = ?\n"
            "if up = (ok) goto (push ({i} = {(in = up)}))\n"
            "}\n",
            "x\nx\nx\n",
        )

    def test_loop_var_reinitializes_each_round(self) -> None:
        self.assert_parity(
            'var i = {"0"}\n'
            'var total = {"0"}\n'
            "while ({i} < 3)\n"
            "{\n"
            'var step = {"10"}\n'
            "math_run {total} + {step} = ?\n"
            "if up = (ok) goto (push ({total} = {(in = up)}))\n"
            "math_run {i} + 1 = ?\n"
            "if up = (ok) goto (push ({i} = {(in = up)}))\n"
            "}\n"
            "print (<{total}>)\n",
            "30\n",
        )

    def test_let_inside_function_loop(self) -> None:
        # v10 起函数体内也可以在 if/while 里声明局部量（声明提升到函数作用域）。
        self.assert_parity(
            "function build(n) -> text\n"
            "{\n"
            'let acc = ""\n'
            "let i = 1\n"
            "while (i <= n)\n"
            "{\n"
            'let piece = text_concat("x", to_text(i))\n'
            "set acc = text_concat(acc, piece)\n"
            "set i = i + 1\n"
            "}\n"
            "return acc\n"
            "}\n"
            "math_run build(3) = ?\n"
            "print (<{(in = up)}>)\n",
            "x1x2x3\n",
        )

    def test_top_level_redeclaration_still_fails(self) -> None:
        with self.assertRaises(Exception) as caught:
            run_interpreted('var A = {"1"}\nvar A = {"2"}\n')
        self.assertIn("already declared", str(caught.exception))

    def test_redeclaration_outside_a_loop_still_fails(self) -> None:
        with self.assertRaises(Exception) as caught:
            run_interpreted(
                'var A = {"1"}\n'
                "if ({A} == 1)\n"
                "{\n"
                'var A = {"2"}\n'
                "}\n"
            )
        self.assertIn("already declared", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
