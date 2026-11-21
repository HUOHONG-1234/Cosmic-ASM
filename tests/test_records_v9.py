"""v9 主线 B：记录成为一等值（跨函数参数与返回值），双后端一致。"""

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

PRINT_NUMBER = (
    "if up = (ok) goto (push ({shown} = {(in = up)}))\n"
    "print (<{shown}>)\n"
)


class RecordFunctionTests(unittest.TestCase):
    def run_source(self, source: str) -> str:
        output = io.StringIO()
        Interpreter(output=output, error_output=io.StringIO(), sleep=lambda _seconds: None).run(
            parse(source)
        )
        return output.getvalue()

    def assert_record_function(self, source: str, expected: str) -> None:
        self.assertEqual(self.run_source(source), expected)


@unittest.skipUnless(shutil.which("gcc"), "GCC is required for native compiler tests")
class CompiledRecordFunctionTests(RecordFunctionTests):
    def run_source(self, source: str) -> str:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            source_path = directory / "program.cos"
            source_path.write_text(source, encoding="utf-8")
            _c_path, executable = compile_source(
                source_path, output_path=directory / "program.exe"
            )
            completed = subprocess.run(
                [str(executable)], check=False, capture_output=True, timeout=60
            )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        return completed.stdout.decode(errors="replace").replace("\r\n", "\n")


class RecordFunctionCases:
    def test_record_parameter_fields(self) -> None:
        self.assert_record_function(
            "record Point { x, y }\n"
            "function norm(point : Point) = point.x * point.x + point.y * point.y\n"
            "function demo()\n"
            "{\n"
            "let a = Point(3, 4)\n"
            "return norm(a)\n"
            "}\n"
            "math_run demo() = ?\n" + PRINT_NUMBER,
            "25\n",
        )

    def test_record_return_from_value_documented_example(self) -> None:
        self.assert_record_function(
            "record Money { amount, currency }\n"
            "function add_money(a : Money, b : Money) -> Money\n"
            "{\n"
            "return Money(a.amount + b.amount, a.currency)\n"
            "}\n"
            "function demo()\n"
            "{\n"
            "let m1 = Money(10, 840)\n"
            "let m2 = Money(5, 840)\n"
            "let total = add_money(m1, m2)\n"
            "return total.amount\n"
            "}\n"
            "math_run demo() = ?\n" + PRINT_NUMBER,
            "15\n",
        )

    def test_record_operator_across_functions(self) -> None:
        self.assert_record_function(
            "record Point { x, y }\n"
            "operator + Point = { left.x + right.x, left.y + right.y }\n"
            "function addp(a : Point, b : Point) -> Point = a + b\n"
            "function demo()\n"
            "{\n"
            "let p = Point(1, 2)\n"
            "let q = Point(10, 20)\n"
            "let sum = addp(p, q)\n"
            "return sum.x + sum.y\n"
            "}\n"
            "math_run demo() = ?\n" + PRINT_NUMBER,
            "33\n",
        )

    def test_record_returned_from_statement_body(self) -> None:
        self.assert_record_function(
            "record Point { x, y }\n"
            "function build() -> Point\n"
            "{\n"
            "let a = Point(1, 2)\n"
            "return a\n"
            "}\n"
            "function demo()\n"
            "{\n"
            "let c = build()\n"
            "return c.x * 10 + c.y\n"
            "}\n"
            "math_run demo() = ?\n" + PRINT_NUMBER,
            "12\n",
        )

    def test_record_field_still_settable_locally(self) -> None:
        self.assert_record_function(
            "record Point { x, y }\n"
            "function demo()\n"
            "{\n"
            "let a = Point(1, 2)\n"
            "set a.x = 9\n"
            "return a.x + a.y\n"
            "}\n"
            "math_run demo() = ?\n" + PRINT_NUMBER,
            "11\n",
        )

    def test_record_copy_is_independent(self) -> None:
        self.assert_record_function(
            "record Point { x, y }\n"
            "function demo()\n"
            "{\n"
            "let a = Point(1, 2)\n"
            "let b = a\n"
            "set b.x = 7\n"
            "return a.x * 10 + b.x\n"
            "}\n"
            "math_run demo() = ?\n" + PRINT_NUMBER,
            "17\n",
        )

    def test_record_chained_between_functions(self) -> None:
        self.assert_record_function(
            "record Pair { first, second }\n"
            "function make(a, b) -> Pair = Pair(a, b)\n"
            "function total(pair : Pair) = pair.first + pair.second\n"
            "function demo()\n"
            "{\n"
            "let one = make(2, 5)\n"
            "let two = make(1, 1)\n"
            "return total(one) + total(two)\n"
            "}\n"
            "math_run demo() = ?\n" + PRINT_NUMBER,
            "9\n",
        )


class V9ExampleTests(unittest.TestCase):
    """examples/v9_delta.cos 必须两个后端输出一致（锁住 v9 的三条主线）。"""

    def run_both(self) -> tuple[str, str]:
        from cosmic_interpreter.source_loader import load_source

        root = Path(__file__).resolve().parent.parent
        source_path = root / "examples" / "v9_delta.cos"
        interpreted = io.StringIO()
        Interpreter(
            output=interpreted, error_output=io.StringIO(), sleep=lambda _s: None
        ).run(parse(load_source(source_path)))
        if not shutil.which("gcc"):
            return interpreted.getvalue(), interpreted.getvalue()
        with tempfile.TemporaryDirectory() as temporary_directory:
            _c_path, executable = compile_source(
                source_path, output_path=Path(temporary_directory) / "demo.exe"
            )
            completed = subprocess.run(
                [str(executable)], check=False, capture_output=True, timeout=120
            )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        return interpreted.getvalue(), completed.stdout.decode(errors="replace").replace(
            "\r\n", "\n"
        )

    def test_example_matches_compiled_output(self) -> None:
        interpreted, compiled = self.run_both()
        self.assertEqual(compiled, interpreted)
        self.assertEqual(
            interpreted.splitlines(),
            [
                "line 1 price = 12",
                "line 3 price = 30",
                "wallet total = 15",
                "array total = 31",
                "currency = EUR",
                "shouting = USD",
            ],
        )


class InterpreterRecordFunctionTests(RecordFunctionCases, RecordFunctionTests):
    pass


class CompiledRecordFunctionCases(RecordFunctionCases, CompiledRecordFunctionTests):
    pass


del RecordFunctionCases


if __name__ == "__main__":
    unittest.main()
