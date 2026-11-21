from __future__ import annotations

import io
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from cosmic_interpreter.parser import parse
from cosmic_interpreter.runtime import Interpreter, RuntimeError


class BuiltinTestBase(unittest.TestCase):
    def run_source(self, source: str) -> tuple[Interpreter, str]:
        output = io.StringIO()
        interpreter = Interpreter(output=output, error_output=io.StringIO())
        interpreter.run(parse(source))
        return interpreter, output.getvalue()


class TextBuiltinTests(BuiltinTestBase):
    def test_split_count_counts_segments(self) -> None:
        interpreter, _output = self.run_source('math_run split_count("a,b,c", ",") = ?\n')
        self.assertEqual(interpreter.last.value, "3")
        interpreter, _output = self.run_source('math_run split_count("a,b,", ",") = ?\n')
        self.assertEqual(interpreter.last.value, "3")
        interpreter, _output = self.run_source('math_run split_count("abc", ",") = ?\n')
        self.assertEqual(interpreter.last.value, "1")

    def test_split_count_rejects_empty_separator(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "separator must not be empty"):
            self.run_source('math_run split_count("a,b", "") = ?\n')

    def test_field_returns_one_based_segment(self) -> None:
        interpreter, _output = self.run_source('math_run field("a,b,c", ",", 2) = ?\n')
        self.assertEqual(interpreter.last.value, "b")
        interpreter, _output = self.run_source('math_run field("a,b,", ",", 3) = ?\n')
        self.assertEqual(interpreter.last.value, "")

    def test_field_rejects_bad_separator_and_index(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "separator must not be empty"):
            self.run_source('math_run field("a,b", "", 1) = ?\n')
        with self.assertRaisesRegex(RuntimeError, "index is outside the text"):
            self.run_source('math_run field("a,b", ",", 3) = ?\n')
        with self.assertRaisesRegex(RuntimeError, "must be a positive integer"):
            self.run_source('math_run field("a,b", ",", 0) = ?\n')

    def test_count_text_counts_non_overlapping_matches(self) -> None:
        interpreter, _output = self.run_source('math_run count_text("banana", "an") = ?\n')
        self.assertEqual(interpreter.last.value, "2")
        interpreter, _output = self.run_source('math_run count_text("banana", "z") = ?\n')
        self.assertEqual(interpreter.last.value, "0")

    def test_count_text_rejects_empty_needle(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "needle must not be empty"):
            self.run_source('math_run count_text("banana", "") = ?\n')

    def test_contains_text_treats_empty_needle_as_match(self) -> None:
        interpreter, _output = self.run_source('math_run contains_text("cosmos", "sm") = ?\n')
        self.assertEqual(interpreter.last.value, "1")
        interpreter, _output = self.run_source('math_run contains_text("cosmos", "") = ?\n')
        self.assertEqual(interpreter.last.value, "1")
        interpreter, _output = self.run_source('math_run contains_text("cosmos", "x") = ?\n')
        self.assertEqual(interpreter.last.value, "0")

    def test_starts_with_and_ends_with(self) -> None:
        interpreter, _output = self.run_source('math_run starts_with("cosmos", "cos") = ?\n')
        self.assertEqual(interpreter.last.value, "1")
        interpreter, _output = self.run_source('math_run starts_with("cosmos", "mos") = ?\n')
        self.assertEqual(interpreter.last.value, "0")
        interpreter, _output = self.run_source('math_run ends_with("cosmos", "mos") = ?\n')
        self.assertEqual(interpreter.last.value, "1")
        interpreter, _output = self.run_source('math_run ends_with("cosmos", "") = ?\n')
        self.assertEqual(interpreter.last.value, "1")

    def test_repeat_text_and_zero_repetition(self) -> None:
        interpreter, _output = self.run_source('math_run repeat_text("ab", 3) = ?\n')
        self.assertEqual(interpreter.last.value, "ababab")
        interpreter, _output = self.run_source('math_run repeat_text("ab", 0) = ?\n')
        self.assertEqual(interpreter.last.value, "")

    def test_repeat_text_rejects_negative_and_oversized(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must not be negative"):
            self.run_source('math_run repeat_text("ab", -1) = ?\n')
        with self.assertRaisesRegex(RuntimeError, "16 MiB limit"):
            self.run_source('math_run repeat_text("ab", 10000000) = ?\n')

    def test_pad_left_uses_code_point_width(self) -> None:
        interpreter, _output = self.run_source('math_run pad_left("东", 4, "-") = ?\n')
        self.assertEqual(interpreter.last.value, "---东")
        interpreter, _output = self.run_source('math_run pad_left("东", 1, "-") = ?\n')
        self.assertEqual(interpreter.last.value, "东")

    def test_pad_right_uses_code_point_width(self) -> None:
        interpreter, _output = self.run_source('math_run pad_right("东", 3, "x") = ?\n')
        self.assertEqual(interpreter.last.value, "东xx")
        interpreter, _output = self.run_source('math_run pad_right("东", 0, "x") = ?\n')
        self.assertEqual(interpreter.last.value, "东")

    def test_pad_rejects_multi_code_point_padding(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "single character"):
            self.run_source('math_run pad_left("a", 3, "xy") = ?\n')
        with self.assertRaisesRegex(RuntimeError, "single character"):
            self.run_source('math_run pad_right("a", 3, "") = ?\n')

    def test_line_at_returns_lines_without_carriage_return(self) -> None:
        source = 'push ({multi} = {"a"} (and) {"\\r\\n"} (and) {"b"})\n'
        interpreter, _output = self.run_source(
            source + "math_run line_at({multi}, 1) = ?\n"
        )
        self.assertEqual(interpreter.last.value, "a")
        interpreter, _output = self.run_source(
            source + "math_run line_at({multi}, 2) = ?\n"
        )
        self.assertEqual(interpreter.last.value, "b")

    def test_line_at_rejects_empty_text_and_trailing_newline_slot(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "index is outside the text"):
            self.run_source('math_run line_at("", 1) = ?\n')
        with self.assertRaisesRegex(RuntimeError, "index is outside the text"):
            self.run_source('math_run line_at("a\\n", 2) = ?\n')

    def test_to_number_parses_signed_decimal(self) -> None:
        interpreter, _output = self.run_source('math_run to_number(" -3.5 ") = ?\n')
        self.assertEqual(interpreter.last.value, "-3.5")
        interpreter, _output = self.run_source('math_run to_number("42") = ?\n')
        self.assertEqual(interpreter.last.value, "42")

    def test_to_number_rejects_garbage(self) -> None:
        for text in ("abc", "", "1.2.3", "0x10"):
            with self.subTest(text=text):
                with self.assertRaisesRegex(RuntimeError, "integer or decimal"):
                    self.run_source(f'math_run to_number("{text}") = ?\n')


class RandomTimeBuiltinTests(BuiltinTestBase):
    def test_random_secure_degenerate_range(self) -> None:
        interpreter, _output = self.run_source("math_run random_secure(5, 5) = ?\n")
        self.assertEqual(interpreter.last.value, "5")

    def test_random_secure_stays_inside_range(self) -> None:
        for _ in range(30):
            interpreter, _output = self.run_source("math_run random_secure(1, 6) = ?\n")
            self.assertIn(int(interpreter.last.value), range(1, 7))

    def test_random_secure_rejects_invalid_bounds(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "low <= high"):
            self.run_source("math_run random_secure(5, 1) = ?\n")
        with self.assertRaisesRegex(RuntimeError, "must be integers"):
            self.run_source("math_run random_secure(1.5, 4) = ?\n")
        with self.assertRaisesRegex(RuntimeError, "below 2\\^53"):
            self.run_source("math_run random_secure(0, 9007199254740992) = ?\n")

    def test_random_bytes_returns_byte_array(self) -> None:
        interpreter, _output = self.run_source("array bytes = random_bytes(8)\n")
        values = interpreter.context.arrays["bytes"]
        self.assertEqual(len(values), 8)
        self.assertTrue(all(float(value).is_integer() and 0 <= value <= 255 for value in values))

    def test_random_bytes_rejects_invalid_length(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "between 0 and 1000000"):
            self.run_source("array bytes = random_bytes(-1)\n")

    def test_now_ms_is_monotonic_and_near_wall_clock(self) -> None:
        interpreter, _output = self.run_source("math_run now_ms() = ?\n")
        first = int(interpreter.last.value)
        interpreter, _output = self.run_source("math_run now_ms() = ?\n")
        second = int(interpreter.last.value)
        self.assertGreaterEqual(second, first)
        self.assertLess(abs(first - int(time.time() * 1000)), 60_000)


class ArrayBuiltinTests(BuiltinTestBase):
    def test_sort_array_returns_sorted_copy(self) -> None:
        source = (
            "array values = array_fill(3, 1)\n"
            "array values = array_set(values, 1, 5)\n"
            "array values = array_set(values, 2, 3)\n"
            "array ordered = sort_array(values)\n"
            "math_run array_at(ordered, 0) = ?\n"
        )
        interpreter, _output = self.run_source(source)
        self.assertEqual(interpreter.last.value, "1")
        self.assertEqual(interpreter.context.arrays["ordered"], [1, 3, 5])
        self.assertEqual(interpreter.context.arrays["values"], [1, 5, 3])

    def test_reverse_array_returns_reversed_copy(self) -> None:
        source = (
            "array values = array_fill(3, 1)\n"
            "array values = array_set(values, 1, 5)\n"
            "array values = array_set(values, 2, 3)\n"
            "array flipped = reverse_array(values)\n"
            "math_run array_at(flipped, 0) = ?\n"
        )
        interpreter, _output = self.run_source(source)
        self.assertEqual(interpreter.last.value, "3")
        self.assertEqual(interpreter.context.arrays["flipped"], [3, 5, 1])
        self.assertEqual(interpreter.context.arrays["values"], [1, 5, 3])

    def test_reverse_array_keeps_empty_array_empty(self) -> None:
        interpreter, _output = self.run_source(
            "array empty = array_fill(0, 0)\narray flipped = reverse_array(empty)\n"
        )
        self.assertEqual(interpreter.context.arrays["flipped"], [])

    def test_array_index_of_returns_first_index_or_minus_one(self) -> None:
        source = (
            "array values = array_fill(3, 7)\n"
            "array values = array_set(values, 0, 4)\n"
            "array values = array_set(values, 2, 9)\n"
        )
        interpreter, _output = self.run_source(source + "math_run array_index_of(values, 7) = ?\n")
        self.assertEqual(interpreter.last.value, "1")
        interpreter, _output = self.run_source(source + "math_run array_index_of(values, 42) = ?\n")
        self.assertEqual(interpreter.last.value, "-1")


PARITY_SOURCE = r'''push ({csv} = {"a,b,c"})
math_run split_count({csv}, ",") = ?
print (<{(in = up)}>)
math_run field({csv}, ",", 2) = ?
print (<{(in = up)}>)
math_run count_text({csv}, ",") = ?
print (<{(in = up)}>)
math_run contains_text({csv}, "z") = ?
print (<{(in = up)}>)
math_run starts_with({csv}, "a") = ?
print (<{(in = up)}>)
math_run ends_with({csv}, "c") = ?
print (<{(in = up)}>)
math_run repeat_text("ab", 2) = ?
print (<{(in = up)}>)
math_run pad_left("东", 4, "-") = ?
print (<{(in = up)}>)
math_run pad_right("东", 3, "x") = ?
print (<{(in = up)}>)
push ({multi} = {"a"} (and) {"\r\n"} (and) {"b"})
math_run line_at({multi}, 2) = ?
print (<{(in = up)}>)
math_run to_number(" -3.5 ") = ?
print (<{(in = up)}>)
array values = array_fill(3, 1)
array values = array_set(values, 1, 5)
array values = array_set(values, 2, 3)
array ordered = sort_array(values)
math_run array_at(ordered, 0) = ?
print (<{(in = up)}>)
array flipped = reverse_array(values)
math_run array_at(flipped, 0) = ?
print (<{(in = up)}>)
math_run array_index_of(values, 5) = ?
print (<{(in = up)}>)
'''

STOCHASTIC_SOURCE = '''\
math_run random_secure(5, 5) = ?
print (<{(in = up)}>)
math_run random_secure(1, 6) = ?
print (<{(in = up)}>)
array bytes = random_bytes(8)
math_run array_len(bytes) = ?
print (<{(in = up)}>)
math_run array_at(bytes, 0) = ?
print (<{(in = up)}>)
math_run now_ms() = ?
print (<{(in = up)}>)
math_run now_ms() = ?
print (<{(in = up)}>)
'''


class BackendParityTests(BuiltinTestBase):
    def compile_and_run(self, source: str) -> tuple[str, str]:
        from cosmic_interpreter.compiler import compile_source

        interpreter_output = io.StringIO()
        Interpreter(output=interpreter_output).run(parse(source))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source_path = directory / "builtins.cos"
            source_path.write_text(source, encoding="utf-8")
            _c_path, output_path = compile_source(
                source_path, output_path=directory / "builtins.exe"
            )
            completed = subprocess.run(
                [str(output_path)], capture_output=True, timeout=120, check=False
            )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        compiled = completed.stdout.decode(errors="replace").replace("\r\n", "\n")
        return interpreter_output.getvalue(), compiled

    def test_text_and_array_builtins_match_compiled_output(self) -> None:
        interpreted, compiled = self.compile_and_run(PARITY_SOURCE)
        self.assertEqual(compiled, interpreted)
        self.assertEqual(
            interpreted.splitlines(),
            ["3", "b", "2", "0", "1", "1", "abab", "---东", "东xx", "b", "-3.5", "1", "3", "1"],
        )

    def test_nested_string_builtin_arguments_match_compiled_output(self) -> None:
        """`to_number(field(...))` 这类嵌套文本实参在两端必须都支持。"""
        source = (
            '#%math = int64\n'
            'push ({csv} = {"10,20,30"})\n'
            'math_run to_number(field({csv}, ",", 3)) = ? -int64\n'
            'if up = (ok) goto (push ({last} = {(in = up)}))\n'
            'math_run count_text(upper("aXa"), "A") = ? -int64\n'
            'if up = (ok) goto (push ({hits} = {(in = up)}))\n'
            'math_run text_len_of(pad_right("ab", 4, ".")) = ? -int64\n'
            'print (<{last}>)\n'
            'print (<{hits}>)\n'
        )
        # pad_right 结果长度用 strlen 取，避免依赖不存在的函数名。
        source = source.replace(
            'math_run text_len_of(pad_right("ab", 4, ".")) = ? -int64\n',
            'math_run strlen(pad_right("ab", 4, ".")) = ? -int64\n'
            'if up = (ok) goto (push ({width} = {(in = up)}))\n',
        )
        source += "print (<{width}>)\n"
        interpreted, compiled = self.compile_and_run(source)
        self.assertEqual(compiled, interpreted)
        self.assertEqual(interpreted.splitlines(), ["30", "2", "4"])

    def test_random_and_time_builtins_in_compiled_program(self) -> None:
        _interpreted, compiled = self.compile_and_run(STOCHASTIC_SOURCE)
        lines = compiled.splitlines()
        self.assertEqual(len(lines), 6)
        self.assertEqual(lines[0], "5")
        self.assertIn(int(lines[1]), range(1, 7))
        self.assertEqual(lines[2], "8")
        self.assertIn(int(lines[3]), range(0, 256))
        first, second = int(lines[4]), int(lines[5])
        self.assertGreaterEqual(second, first)
        self.assertLess(abs(first - int(time.time() * 1000)), 120_000)


class ReservedNameTests(BuiltinTestBase):
    def test_new_builtins_are_reserved_function_names(self) -> None:
        for name in (
            "split_count", "field", "count_text", "pad_left", "line_at",
            "to_number", "random_secure", "now_ms", "sort_array", "array_index_of",
        ):
            with self.subTest(name=name):
                with self.assertRaisesRegex(RuntimeError, "is reserved"):
                    self.run_source(f"function {name}(value) = value\n")


if __name__ == "__main__":
    unittest.main()
