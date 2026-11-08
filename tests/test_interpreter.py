from __future__ import annotations

import io
import math
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from cosmic_interpreter.parser import ParseError, parse
from cosmic_interpreter.runtime import ExitRequest, Interpreter, Result, RuntimeError


class InterpreterTests(unittest.TestCase):
    def run_source(self, source: str, **options: object) -> tuple[Interpreter, str, str]:
        output = io.StringIO()
        errors = io.StringIO()
        interpreter = Interpreter(output=output, error_output=errors, sleep=lambda _seconds: None, **options)
        interpreter.run(parse(source))
        return interpreter, output.getvalue(), errors.getvalue()

    def test_explicit_and_implicit_string_variables_use_unambiguous_literals(self) -> None:
        source = """\
var greeting = {"hello, \\"cosmos\\" {word}"}
push ({copy} = {greeting})
push ({implicit} = {"42"})
math_run {implicit} + 1 = ? -int32
if up = (ok) goto (push ({number} = {(in = up)}))
print (<{copy}>)
print (<{"literal {word}"}>)
print (<{"prefix "} (and) {copy}>)
print (<{number}>)
"""
        interpreter, output, _errors = self.run_source(source)
        self.assertEqual(
            output,
            'hello, "cosmos" {word}\nliteral {word}\nprefix hello, "cosmos" {word}\n43\n',
        )
        self.assertEqual(interpreter.context.variables["implicit"].text(), "42")
        self.assertEqual(interpreter.context.variables["number"].text(), "43")

    def test_variable_reference_requires_a_prior_assignment_or_declaration(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "empty variable 'missing'"):
            self.run_source('push ({copy} = {missing})\n')
        with self.assertRaisesRegex(RuntimeError, "variable 'missing' is not available"):
            self.run_source("math_run {missing} + 1 = ?\n")

    def test_variable_cannot_be_declared_twice(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "already declared"):
            self.run_source('var name = {"first"}\nvar name = {"second"}\n')

    def test_math_expression_supports_quoted_braced_string_literals(self) -> None:
        interpreter, _output, _errors = self.run_source(
            'math_run ord({"A"}) = ? -int32\n'
        )
        self.assertEqual(interpreter.last.value, "65")

    def test_bigint_fixed_point_constants(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "math_run pi_digits(40) = ? -bigint\n"
        )
        self.assertEqual(
            interpreter.last.value,
            "31415926535897932384626433832795028841971",
        )
        interpreter, _output, _errors = self.run_source(
            "math_run pi_str(5) = ? -bigint\n"
        )
        self.assertEqual(interpreter.last.value, "3.14159")
        for expression, expected in (
            ("e_str(10)", "2.7182818284"),
            ("sqrt2_str(10)", "1.4142135623"),
        ):
            interpreter, _output, _errors = self.run_source(
                f"math_run {expression} = ? -bigint\n"
            )
            self.assertEqual(interpreter.last.value, expected)

    def test_fixed_point_constants_validate_precision(self) -> None:
        for precision in (-1, 1001):
            with self.assertRaisesRegex(RuntimeError, "precision must be between 0 and 1000"):
                self.run_source(f"math_run pi_digits({precision}) = ? -bigint\n")

    def test_runs_math_example(self) -> None:
        source = """\
#math
call = (math)
father = (up)
start(normal) = {math}
{
    math_run (1) + (1) = ?
    if up = (ok) goto (push ({answer} = {(in = up)} -GBK ) )
    print (<{answer}>) -GBK
}
"""
        _interpreter, output, _errors = self.run_source(source)
        self.assertEqual(output, "2\n")

    def test_math_preserves_parentheses_precedence(self) -> None:
        interpreter, output, _errors = self.run_source("math_run (1 + 2) * (3 + 4) = ?\n")
        self.assertEqual(output, "")
        self.assertEqual(interpreter.last.value, "21")

    def test_float_output_precision_is_configurable(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "#%precision = 17\nmath_run 1 / 3 = ?\n"
        )
        self.assertEqual(interpreter.last.value, "0.33333333333333331")

    def test_output_precision_must_be_declared_once_and_in_range(self) -> None:
        for source, expected in (
            ("#%precision = 0\n", "between 1 and 17"),
            ("#%precision = 18\n", "between 1 and 17"),
            ("#%precision = 2.5\n", "must be an integer"),
            ("#%precision = 2\n#%precision = 3\n", "only be declared once"),
        ):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ParseError, expected):
                    parse(source)

    def test_complex_arithmetic_and_functions(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "#%math = complex\n"
            "math_run complex(1, 2) * complex(3, -1) = ?\n"
        )
        self.assertEqual(interpreter.last.value, "5+5i")

        root, _output, _errors = self.run_source(
            "#%math = complex\nmath_run sqrt(-1) = ?\n"
        )
        self.assertEqual(root.last.value, "0+1i")

    def test_array_mutators_and_statistics(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "#%precision = 17\n"
            "math_run array_len(array_push(array_set([1, 2, 3], 1, 5), 9)) = ?\n"
            "if up = (ok) goto (push ({length} = {(in = up)}))\n"
            "math_run array_sum(array_set(array_push([1, 2], 4), 1, 5)) = ?\n"
            "if up = (ok) goto (push ({sum} = {(in = up)}))\n"
            "math_run array_at(array_set([1, 2, 3], 1, 5), 1) = ?\n"
            "if up = (ok) goto (push ({changed} = {(in = up)}))\n"
            "math_run mean([1, 2, 4]) = ?\n"
            "if up = (ok) goto (push ({average} = {(in = up)}))\n"
            "math_run std([1, 2, 3]) = ?\n"
            "if up = (ok) goto (push ({deviation} = {(in = up)}))\n"
            "math_run min([3, -1, 2]) = ?\n"
            "if up = (ok) goto (push ({minimum} = {(in = up)}))\n"
            "math_run max([3, -1, 2]) = ?\n"
            "if up = (ok) goto (push ({maximum} = {(in = up)}))\n"
        )
        variables = interpreter.context.variables
        self.assertEqual(variables["length"].text(), "4")
        self.assertEqual(variables["sum"].text(), "10")
        self.assertEqual(variables["changed"].text(), "5")
        self.assertEqual(variables["average"].text(), "2.3333333333333335")
        self.assertEqual(variables["deviation"].text(), "0.81649658092772603")
        self.assertEqual(variables["minimum"].text(), "-1")
        self.assertEqual(variables["maximum"].text(), "3")

    def test_named_array_persists_across_math_runs_and_mutates(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "array scores = [2, 4, 6]\n"
            "math_run array_sum(scores) = ?\n"
            "if up = (ok) goto (push ({initial} = {(in = up)}))\n"
            "math_run array_sum(array_set(scores, 1, 10)) = ?\n"
            "if up = (ok) goto (push ({updated} = {(in = up)}))\n"
            "math_run array_len(array_push(scores, 8)) = ?\n"
            "if up = (ok) goto (push ({length} = {(in = up)}))\n"
            "math_run array_at(scores, 3) = ?\n"
            "if up = (ok) goto (push ({last} = {(in = up)}))\n"
        )
        self.assertEqual(interpreter.context.arrays["scores"], [2.0, 10.0, 6.0, 8.0])
        self.assertEqual(interpreter.context.variables["initial"].text(), "12")
        self.assertEqual(interpreter.context.variables["updated"].text(), "18")
        self.assertEqual(interpreter.context.variables["length"].text(), "4")
        self.assertEqual(interpreter.context.variables["last"].text(), "8")

    def test_inherited_named_arrays_are_independent_copies(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "array values = [1, 2]\n"
            "father = up\n"
            "start(normal) = {child}\n"
            "{\n"
            "    math_run array_sum(array_set(values, 0, 9)) = ?\n"
            "}\n"
            "math_run array_sum(values) = ?\n"
        )
        self.assertEqual(interpreter.context.arrays["values"], [1.0, 2.0])
        self.assertEqual(interpreter.last.value, "3")

    def test_array_declaration_requires_a_list_literal(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "requires a numeric list literal"):
            self.run_source("array values = 1\n")

    def test_bare_array_is_not_a_scalar_math_result(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "array values cannot be used as scalar"):
            self.run_source(
                "array values = [1, 2]\nmath_run array_push(values, 3) = ?\n"
            )

    def test_array_statistics_and_set_validate_inputs(self) -> None:
        for expression, expected in (
            ("mean([])", "mean requires a non-empty array"),
            ("array_sum(array_set([1], 1, 2))", "array index is outside the array"),
            ("array_sum(array_push([1], complex(1, 2)))", "real values only"),
        ):
            with self.subTest(expression=expression):
                with self.assertRaisesRegex(RuntimeError, expected):
                    self.run_source(f"math_run {expression} = ?\n")

    def test_abs_of_complex_in_float64_mode(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "math_run abs(complex(3, 4)) = ? -float64\n"
        )
        self.assertEqual(interpreter.last.value, "5")

    def test_complex_arrays_and_matrix_operations(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "#%math = complex\n"
            "math_run array_sum([complex(1, 2), complex(3, 4)]) = ?\n"
            "if up = (ok) goto (push ({sum} = {(in = up)}))\n"
            "math_run matrix2_det([[complex(1, 1), 2], [3, 4]]) = ?\n"
            "if up = (ok) goto (push ({det} = {(in = up)}))\n"
        )
        self.assertEqual(interpreter.context.variables["sum"].text(), "4+6i")
        self.assertEqual(interpreter.context.variables["det"].text(), "-2+4i")

    def test_complex_named_and_variable_arrays(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "#%math = complex\n"
            "array values = [complex(1, 2), complex(3, 4)]\n"
            "push ({copy} = array_push(values, complex(5, 6)))\n"
            "push ({alias} = {copy})\n"
            "push ({copy} = array_push({copy}, complex(7, 8)))\n"
            "math_run array_sum({alias}) = ?\n"
        )
        self.assertEqual(interpreter.context.arrays["values"], [1 + 2j, 3 + 4j, 5 + 6j])
        self.assertEqual(
            interpreter.context.variables["copy"].array_value,
            [1 + 2j, 3 + 4j, 5 + 6j, 7 + 8j],
        )
        self.assertEqual(
            interpreter.context.variables["alias"].array_value,
            [1 + 2j, 3 + 4j, 5 + 6j],
        )
        self.assertEqual(interpreter.last.value, "9+12i")

    def test_array_assignment_copies_values_and_allows_type_reassignment(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "push ({values} = [1, 2])\n"
            "push ({copy} = {values})\n"
            "push ({copy} = array_push({copy}, 3))\n"
            "push ({copy} = array_push({copy}, 4))\n"
            'push ({values} = {"text"})\n'
            "push ({values} = [7, 8])\n"
        )
        self.assertEqual(interpreter.context.variables["values"].array_value, [7.0, 8.0])
        self.assertEqual(interpreter.context.variables["copy"].array_value, [1.0, 2.0, 3.0, 4.0])

    def test_complex_values_round_trip_through_variables(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "math_run complex(2, -3) = ? -complex\n"
            "if up = (ok) goto (push ({z} = {(in = up)}))\n"
            "math_run {z} + complex(1, 1) = ? -complex\n"
        )
        self.assertEqual(interpreter.last.value, "3-2i")

    def test_bigint_arithmetic_preserves_large_integer_precision(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "#%math = bigint\n"
            "math_run 999999999999999999999999999999 ** 2 = ?\n"
        )
        self.assertEqual(
            interpreter.last.value,
            "999999999999999999999999999998000000000000000000000000000001",
        )

    def test_utf8_file_read_and_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = str(Path(temporary_directory) / "text.txt")
            source = (
                f"math_run write_text({path!r}, '你好') = ?\n"
                "if up = (ok) goto (push ({written} = {(in = up)}))\n"
                f"math_run read_text({path!r}) = ?\n"
            )
            interpreter, _output, _errors = self.run_source(source)
            self.assertEqual(interpreter.last.value, "你好")
            self.assertEqual(interpreter.context.variables["written"].text(), "2")

    def test_recursive_expression_function(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "function factorial(n) = choose(n <= 1, 1, n * factorial(n - 1))\n"
            "math_run factorial(10) = ?\n"
        )
        self.assertEqual(interpreter.last.value, "3628800")

    def test_generic_expression_function_infers_numeric_arguments(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "generic function square(value) = value * value\n"
            "math_run square(5.5) = ?\n"
        )
        self.assertEqual(interpreter.last.value, "30.25")

    def test_user_function_in_loop_condition(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "function below_three(value) = value < 3\n"
            "math_run 0 = ? -int32\n"
            "if up = (ok) goto (push ({counter} = {(in = up)}))\n"
            "while (below_three({counter}))\n"
            "{\n"
            "    math_run {counter} + 1 = ? -int32\n"
            "    if up = (ok) goto (push ({counter} = {(in = up)}))\n"
            "}\n"
        )
        self.assertEqual(interpreter.context.variables["counter"].text(), "3")

    def test_array_and_matrix_expression_helpers(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "math_run array_sum([1, 2, 3]) + dot([1, 2, 3], [4, 5, 6]) = ?\n"
            "if up = (ok) goto (push ({vector_result} = {(in = up)}))\n"
            "math_run matrix2_det([[1, 2], [3, 4]]) = ?\n"
            "if up = (ok) goto (push ({determinant} = {(in = up)}))\n"
            "math_run matrix2_mul_entry([[1, 2], [3, 4]], [[5, 6], [7, 8]], 1, 0) = ?\n"
            "if up = (ok) goto (push ({matrix_result} = {(in = up)}))\n"
        )
        self.assertEqual(interpreter.context.variables["vector_result"].text(), "38")
        self.assertEqual(interpreter.context.variables["determinant"].text(), "-2")
        self.assertEqual(interpreter.context.variables["matrix_result"].text(), "43")

    def test_blas_and_lapack_style_operations_match_reference_results(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "math_run blas_dot([1, 2, 3], [4, 5, 6]) = ?\n"
            "if up = (ok) goto (push ({dot_result} = {(in = up)}))\n"
            "math_run lapack_determinant2([[1, 2], [3, 4]]) = ?\n"
            "if up = (ok) goto (push ({det_result} = {(in = up)}))\n"
        )
        self.assertEqual(interpreter.context.variables["dot_result"].text(), "32")
        self.assertEqual(interpreter.context.variables["det_result"].text(), "-2")

    def test_scientific_functions_and_constants(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "#%math = float64\nmath_run sin(pi / 2) + sqrt(9) + log(8, 2) = ?\n"
        )
        self.assertEqual(interpreter.last.value, "7")

    def test_atan_and_machin_pi_example(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "#%math = float64\nmath_run atan(1) = ?\n"
        )
        self.assertAlmostEqual(float(interpreter.last.value), 0.7853981633974483)

        source = (Path(__file__).parents[1] / "examples" / "pi_machin.cos").read_text(
            encoding="utf-8"
        )
        _interpreter, output, _errors = self.run_source(source)
        self.assertAlmostEqual(float(output.strip()), 3.141592653589793)

    def test_rag_returns_a_die_roll_and_validates_sides(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "#%math = int32\nmath_run rag (max : (2 + 4)) (min : (1 + 0)) = ?\n"
        )
        self.assertIn(int(interpreter.last.value), range(1, 7))
        fixed, _output, _errors = self.run_source(
            "#%math = int32\nmath_run rag (max : -3) (min : -3) = ?\n"
        )
        self.assertEqual(fixed.last.value, "-3")
        with self.assertRaisesRegex(RuntimeError, "rag requires int32 bounds"):
            self.run_source("math_run rag (max : 1) (min : 2) = ?\n")
        with self.assertRaisesRegex(RuntimeError, "rag bounds must be integers"):
            self.run_source("math_run rag (max : 2.5) (min : 1) = ?\n")

    def test_chr_ord_round_trip_unicode(self) -> None:
        interpreter, output, _errors = self.run_source(
            '#%math = int32\npush ({source} = {"你"})\n'
            "math_run ord({source}) = ?\n"
            "if up = (ok) goto (push ({codepoint} = {(in = up)}))\n"
            "math_run chr({codepoint}) = ?\n"
            "if up = (ok) goto (push ({character} = {(in = up)}))\n"
            "print (<{character}>)\n"
        )
        self.assertEqual(interpreter.context.variables["codepoint"].text(), "20320")
        self.assertEqual(output, "你\n")

    def test_chr_ord_validate_unicode_inputs(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "valid Unicode scalar"):
            self.run_source("math_run chr(55296) = ?\n")
        with self.assertRaisesRegex(RuntimeError, "exactly one Unicode character"):
            self.run_source('math_run ord("ab") = ?\n')
        with self.assertRaisesRegex(RuntimeError, "does not support NUL"):
            self.run_source("math_run chr(0) = ?\n")

    def test_string_concat_strlen_and_char_at(self) -> None:
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
        _interpreter, output, _errors = self.run_source(source)
        self.assertEqual(output, "你好world\nHi, 你好\n7\n好\n3\n2\n你好world\n")

    def test_char_at_rejects_invalid_indices(self) -> None:
        for expression in ("char_at({word}, -1)", "char_at({word}, 1.5)", "char_at({word}, 2)"):
            with self.subTest(expression=expression):
                with self.assertRaises(RuntimeError):
                    self.run_source(f'push ({{word}} = {{"ab"}})\nmath_run {expression} = ?\n')

    def test_int32_math_mode_checks_overflow_and_fraction(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "requires an integer result"):
            self.run_source("#%math = int32\nmath_run 1 / 2 = ?\n")
        with self.assertRaisesRegex(RuntimeError, "overflows int32"):
            self.run_source("#%math = int32\nmath_run 2147483647 + 1 = ?\n")
        interpreter, _output, _errors = self.run_source(
            "#%math = int32\nmath_run 1 / 2 = ? -float64\n"
        )
        self.assertEqual(interpreter.last.value, "0.5")

    def test_math_rejects_excessive_exponents(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "exponent is too large"):
            self.run_source("math_run 2 ** 10001 = ?\n")

    def test_int64_keeps_full_integer_precision(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "#%math = int64\nmath_run 9007199254740993 + 0 = ?\n"
        )
        self.assertEqual(interpreter.last.value, "9007199254740993")

    def test_while_if_else_and_tape_run(self) -> None:
        source = """\
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
"""
        _interpreter, output, _errors = self.run_source(source)
        self.assertEqual(output, "halted\n")

    def test_numeric_variable_while_loop(self) -> None:
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
        _interpreter, output, _errors = self.run_source(source)
        self.assertEqual(output, "5\n")

    def test_scientific_tape_example_runs(self) -> None:
        source = (Path(__file__).parents[1] / "examples" / "scientific_tape.cos").read_text(
            encoding="utf-8"
        )
        _interpreter, output, _errors = self.run_source(source)
        self.assertEqual(output, "4\n5\nhalted\n")

    def test_bubble_sort_example_sorts_values(self) -> None:
        source = (Path(__file__).parents[1] / "examples" / "bubble_sort.cos").read_text(
            encoding="utf-8"
        )
        _interpreter, output, _errors = self.run_source(source)
        self.assertEqual(output, "1\n2\n3\n4\n")

    def test_configured_loop_limit_stops_nonterminating_while(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "loop iteration limit exceeded"):
            self.run_source("while (1 == 1)\n{\n    hello\n}\n", max_loop_iterations=3)

    def test_tape_moves_in_both_directions_and_preserves_cells(self) -> None:
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
        _interpreter, output, _errors = self.run_source(source)
        self.assertEqual(output, "tape_ok\n")

    def test_inherited_context_is_a_copy_for_scalar_variables(self) -> None:
        source = """\
push ({value} = {"parent"})
father = up
start(normal) = {child}
{
    push ({value} = {"child"})
}
print (<{value}>)
"""
        _interpreter, output, _errors = self.run_source(source)
        self.assertEqual(output, "parent\n")

    def test_encoding_is_used_for_variable_storage(self) -> None:
        source = """\
#word = GBK
push ({word} = {"你好"})
"""
        interpreter, _output, _errors = self.run_source(source)
        variable = interpreter.context.variables["word"]
        self.assertEqual(variable.value, "你好".encode("gbk"))
        self.assertEqual(variable.text(), "你好")

    def test_declared_but_uninitialized_variable_is_an_error(self) -> None:
        interpreter, output, _errors = self.run_source("#word = GBK\nprint(<{word}>)\n")
        self.assertEqual(output, "")
        self.assertEqual(interpreter.last.status, "error")
        self.assertEqual(interpreter.last.value, "error")

    def test_runs_loop_and_dog_example(self) -> None:
        source = """\
#execute
#hello = GBK
push ({hello} = {"hello_world"})
call = (execute)
father = (up)
start(loop) = {loop}
{
    sleep(0){s}
    print(<{hello}>) -GBK
    if up = (error) goto (to = stop)
    kill{hello} -force
}
dog(loop) = {stop}
"""
        _interpreter, output, _errors = self.run_source(source, max_loop_iterations=3)
        self.assertEqual(output, "hello_world\n")

    def test_runs_command_result_assignment(self) -> None:
        source = """\
#execute
start(normal) = {open}
{
    call = (execute)
    father = up
    open = (open = (open = (bash = dir) ) )
    if up = (ok) goto (push ({listing} = {(in = up)} -GBK ) )
    print (<{listing}>) -GBK
}
dog(open) = {ok}
"""
        _interpreter, output, _errors = self.run_source(
            source, external_runner=lambda _command: ("item-a\nitem-b\n", 0)
        )
        self.assertEqual(output, "item-a\nitem-b\n")

    def test_runs_combined_demo(self) -> None:
        source = (Path(__file__).parents[1] / "examples" / "demo.cos").read_text(encoding="utf-8")
        _interpreter, output, errors = self.run_source(
            source, external_runner=lambda _command: ("sample-directory\n", 0)
        )
        self.assertIn("hello_world\n", output)
        self.assertIn("sample-directory\n", output)
        self.assertTrue(output.endswith("2\n"))
        self.assertIn("pe is recognized but not implemented", errors)

    def test_missing_and_killed_variables_have_error_status(self) -> None:
        source = """\
push ({item} = {"hello"})
kill{item} -force
print(<{item}>) -GBK
"""
        interpreter, output, _errors = self.run_source(source)
        self.assertEqual(output, "")
        self.assertEqual(interpreter.last.status, "error")
        self.assertEqual(interpreter.last.value, "killer")

    def test_pe_is_reported_as_unsupported(self) -> None:
        _interpreter, _output, errors = self.run_source("#pe\n")
        self.assertIn("pe is recognized but not implemented", errors)

    def test_external_commands_can_be_disabled(self) -> None:
        source = """\
start(normal) = {cmd}
{
    result = (bash = echo hello)
}
"""
        with self.assertRaisesRegex(RuntimeError, "external command execution is disabled"):
            self.run_source(source, allow_external_commands=False)

    def test_invalid_syntax_reports_source_line(self) -> None:
        with self.assertRaisesRegex(ParseError, r"line 2: invalid start declaration"):
            parse("#% comment\nstart(loop) = loop\n")

    def test_stop_target_requires_an_execution_block(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "only valid inside an execution block"):
            self.run_source("if up = (ok) goto (to = stop)\n")

    def test_math_rejects_non_arithmetic_code(self) -> None:
        source = "math_run __import__('os') = ?\n"
        with self.assertRaisesRegex(RuntimeError, "invalid math expression"):
            self.run_source(source)

    def test_dog_reports_deadlock_when_status_does_not_match(self) -> None:
        source = """\
start(loop) = {loop}
{
    hello
}
dog(loop) = {stop}
"""
        with self.assertRaisesRegex(RuntimeError, "Loop_Deadlock"):
            self.run_source(source, max_loop_iterations=2)

    def test_dog_status_checks_last_block_status(self) -> None:
        source = """\
start(normal) = {work}
{
    hello
}
dog(status) = {ok}
"""
        self.run_source(source)

    def test_dog_status_whitelist_maps_block_outcomes(self) -> None:
        source = """\
start(normal) = {work}
{
    if up = (ok) goto (to = stop)
}
dog(work) = (stop : error) (pass : ok)
if up = (error) goto (push ({state} = {"handled_stop"}))
print (<{state}>)
"""
        _interpreter, output, _errors = self.run_source(source)
        self.assertEqual(output, "handled_stop\n")

        passed, _output, _errors = self.run_source(
            "start(normal) = {work}\n"
            "{\n"
            "    hello\n"
            "}\n"
            "dog(status) = (stop : error) (pass : ok)\n"
        )
        self.assertEqual(passed.last.status, "ok")
        self.assertEqual(passed.last.value, "ok")

    def test_dog_status_whitelist_rejects_unlisted_status(self) -> None:
        source = """\
start(normal) = {work}
{
    hello
}
dog(work) = (stop : error)
"""
        with self.assertRaisesRegex(RuntimeError, "not in whitelist"):
            self.run_source(source)

    def test_execution_block_else_runs_after_stop(self) -> None:
        source = """\
push ({state} = {"before"})
father = up
start(normal) = {work}
{
    push ({state} = {"primary"})
    if up = (ok) goto (to = stop)
}
else
{
    push ({state} = {"fallback"})
    print (<{state}>)
}
"""
        _interpreter, output, _errors = self.run_source(source)
        self.assertEqual(output, "fallback\n")

    def test_jump_skips_to_physical_source_line_in_same_block(self) -> None:
        source = """\
push ({state} = {"before"})
father = up
start(normal) = {work}
{
    jump(8)
    push ({state} = {"skipped"})
    push ({state} = {"also_skipped"})
    push ({state} = {"target"})
    print (<{state}>)
}
"""
        _interpreter, output, _errors = self.run_source(source)
        self.assertEqual(output, "target\n")

    def test_jump_rejects_target_outside_current_block(self) -> None:
        source = """\
start(normal) = {work}
{
    jump(99)
}
"""
        with self.assertRaisesRegex(RuntimeError, "jump target line 99"):
            self.run_source(source)


    def test_dll_status_reports_missing_loaded_and_unloaded(self) -> None:
        if os.name != "nt":
            self.skipTest("user32 is only available on Windows")
        source = """\
#dll
cdll = status (user32)
calldll = (user32)
cdll = status (user32)
cdll = out (user32)
cdll = status (user32)
"""
        interpreter, _output, errors = self.run_source(source)
        self.assertEqual(errors, "")
        self.assertEqual(interpreter.last, Result("ok", "unloaded"))
        self.assertEqual(interpreter.dll_states, {"user32": "unloaded"})
        self.assertEqual(interpreter.dll_libraries, {})
        self.assertEqual(interpreter.error_count, 0)

    def test_dll_status_reports_missing_for_never_loaded_library(self) -> None:
        if os.name != "nt":
            self.skipTest("user32 is only available on Windows")
        interpreter, _output, _errors = self.run_source("#dll\ncdll = status (user32)\n")
        self.assertEqual(interpreter.last, Result("ok", "missing"))
        self.assertEqual(interpreter.dll_libraries, {})

    def test_dll_come_requires_loaded_library(self) -> None:
        source = """\
#dll
#extern gettickcount () -> DWORD
cdll = come (kernel32) (gettickcount)
"""
        interpreter, _output, errors = self.run_source(source)
        self.assertEqual(interpreter.last.status, "error")
        self.assertIn("is not loaded", interpreter.last.value or "")
        self.assertEqual(interpreter.error_count, 1)
        self.assertIn("line 3:", errors)

    def test_dll_come_requires_extern_declaration(self) -> None:
        source = """\
#dll
#extern gettickcount () -> DWORD
calldll = (kernel32)
cdll = come (kernel32) (getmodulehandlea)
"""
        with self.assertRaisesRegex(ParseError, "has no #extern declaration"):
            parse(source)

    @unittest.skipUnless(os.name == "nt", "kernel32 is only available on Windows")
    def test_dll_come_calls_windows_function(self) -> None:
        source = """\
#dll
calldll = (kernel32)
#extern gettickcount () -> DWORD
cdll = come (kernel32) (gettickcount)
"""
        interpreter, _output, errors = self.run_source(source)
        self.assertEqual(errors, "")
        self.assertEqual(interpreter.last.status, "ok")
        self.assertEqual(interpreter.error_count, 0)
        self.assertGreaterEqual(int(interpreter.last.value or ""), 0)

    @unittest.skipUnless(os.name == "nt", "kernel32 is only available on Windows")
    def test_dll_come_passes_string_argument(self) -> None:
        source = """\
#dll
calldll = (kernel32)
#extern lstrlena (LPCSTR) -> INT
push ({word} = {"hello"})
cdll = come (kernel32) (lstrlena) ({word})
"""
        interpreter, _output, errors = self.run_source(source)
        self.assertEqual(errors, "")
        self.assertEqual(interpreter.last, Result("ok", "5"))

    @unittest.skipUnless(os.name != "nt", "POSIX libc is only available off Windows")
    def test_dll_come_calls_posix_function(self) -> None:
        source = """\
#dll
calldll = (libc)
#extern getpid () -> INT
cdll = come (libc) (getpid)
"""
        interpreter, _output, errors = self.run_source(source)
        self.assertEqual(errors, "")
        self.assertEqual(interpreter.last.status, "ok")
        self.assertGreater(int(interpreter.last.value or ""), 0)

    def test_dll_load_failure_reports_error_without_raising(self) -> None:
        source = "#dll\ncalldll = (nope_not_a_real_library_xyz)\n"
        interpreter, _output, errors = self.run_source(source)
        self.assertEqual(interpreter.last.status, "error")
        self.assertEqual(interpreter.error_count, 1)
        self.assertIn("calldll failed", errors)
        self.assertEqual(interpreter.dll_states, {"nope_not_a_real_library_xyz": "missing"})

    @unittest.skipUnless(os.name == "nt", "kernel32 is only available on Windows")
    def test_dll_load_is_idempotent(self) -> None:
        source = "#dll\ncalldll = (kernel32)\ncalldll = (kernel32)\n"
        interpreter, _output, errors = self.run_source(source)
        self.assertEqual(errors, "")
        self.assertEqual(interpreter.last, Result("ok", "kernel32"))
        self.assertEqual(interpreter.error_count, 0)

    @unittest.skipUnless(os.name == "nt", "kernel32 is only available on Windows")
    def test_dll_wrong_argument_type_reports_error(self) -> None:
        source = """\
#dll
calldll = (kernel32)
#extern lstrlena (LPCSTR) -> INT
cdll = come (kernel32) (lstrlena) (1)
"""
        interpreter, _output, errors = self.run_source(source)
        self.assertEqual(interpreter.last.status, "error")
        self.assertIn("expects LPCSTR", interpreter.last.value or "")
        self.assertEqual(interpreter.error_count, 1)
        self.assertIn("line 4:", errors)

    def test_dll_out_requires_loaded_library(self) -> None:
        interpreter, _output, errors = self.run_source("#dll\ncdll = out (kernel32)\n")
        self.assertEqual(interpreter.last.status, "error")
        self.assertEqual(interpreter.error_count, 1)
        self.assertIn("is not loaded", errors)

    def test_dll_requires_dll_import(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "component 'dll' has not been imported"):
            self.run_source("calldll = (kernel32)\n")
        with self.assertRaisesRegex(RuntimeError, "component 'dll' has not been imported"):
            self.run_source("cdll = status (kernel32)\n")

    def test_atom_requires_atom_import(self) -> None:
        for source in (
            "n-bomb {worker}\n",
            "neutron {main} = {worker}\n",
            "proton {chain} = {a}\n",
            "nuclear_reactor {main} = {chain}\n",
        ):
            with self.subTest(source=source):
                with self.assertRaisesRegex(
                    RuntimeError, "component 'atom' has not been imported"
                ):
                    self.run_source(source)

    def test_atom_requires_known_blocks(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "unknown execution block 'ghost'"):
            self.run_source("#atom\nn-bomb {ghost}\n")

    def test_atom_lock_serializes_shared_counter(self) -> None:
        source = '''\
#atom
start(normal) = {worker}
{
    lock {worker}
    {
        math_run 1 + 1 = ? -int64
    }
}
proton {chain} = {worker} -> {worker} -> {worker}
nuclear_reactor {main} = {chain}
'''
        interpreter, _output, errors = self.run_source(source)
        self.assertEqual(errors, "")
        worker = interpreter.atom_runtime["worker"]
        for _ in range(300):
            if worker.state() in {"ok", "stop", "error"}:
                break
            time.sleep(0.01)
        self.assertEqual(worker.state(), "ok")
        self.assertEqual(interpreter.error_count, 0)
        self.assertEqual(worker.context.lock_owner, None)
        self.assertEqual(worker.context.lock_depth, 0)

    def test_atom_n_bomb_marks_block_stopped(self) -> None:
        interpreter, _output, errors = self.run_source(
            "#atom\n"
            "start(normal) = {worker}\n"
            "{\n"
            "    math_run 1 + 1 = ? -int32\n"
            "}\n"
            "proton {chain} = {worker}\n"
            "nuclear_reactor {worker} = {chain}\n"
            "n-bomb {worker}\n"
            "dog(worker) = {stopped}\n"
        )
        self.assertEqual(errors, "")
        self.assertEqual(interpreter.atom_runtime["worker"].state(), "stopped")

    def test_atom_proton_chain_runs_blocks_in_declared_order(self) -> None:
        interpreter, _output, errors = self.run_source(
            "#atom\n"
            "start(normal) = {first}\n"
            "{\n"
            "    math_run 10 + 1 = ? -int64\n"
            "    if up = (ok) goto (push ({order} = {(in = up)}))\n"
            "}\n"
            "start(normal) = {second}\n"
            "{\n"
            "    math_run 20 + 2 = ? -int64\n"
            "    if up = (ok) goto (push ({order} = {(in = up)}))\n"
            "}\n"
            "proton {chain} = {first} -> {second}\n"
            "nuclear_reactor {main} = {chain}\n"
        )
        self.assertEqual(errors, "")
        for _ in range(300):
            if interpreter.atom_runtime["second"].state() in {"ok", "stop", "error"}:
                break
            time.sleep(0.01)
        second = interpreter.atom_runtime["second"]
        self.assertEqual(second.state(), "ok")
        self.assertEqual(second.context.variables["order"].text(), "22")

    def test_atom_lock_timeout_waits_then_reports_error(self) -> None:
        interpreter = Interpreter(output=io.StringIO(), error_output=io.StringIO())
        interpreter.run(parse("#atom\nstart(normal) = {worker}\n{\n}\n"))
        block = interpreter.atom_runtime["worker"]
        block.set_state("running")
        context = interpreter.context.inherit()
        context.imports.add("atom")
        block.context = context
        context.lock_owner = "other-block"
        try:
            with self.assertRaisesRegex(RuntimeError, "lock timeout"):
                interpreter.run(parse("#atom\nlock {worker} -timeout 0.1\n{\n}\n"))
        finally:
            context.lock_owner = None
        self.assertEqual(interpreter.error_count, 1)


    def test_array_can_be_rebound_from_an_array_expression(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "array a = [1, 2, 3]\n"
            "array a = array_set(a, 1, 99)\n"
            "math_run array_sum(a) = ? -int64\n"
        )
        self.assertEqual(interpreter.last.value, "103")
        self.assertEqual(interpreter.context.arrays["a"], [1.0, 99.0, 3.0])

    def test_print_of_undefined_variable_stays_silent_with_error_status(self) -> None:
        interpreter, output, _errors = self.run_source("print (<{missing}>)\n")
        self.assertEqual(output, "")
        self.assertEqual(interpreter.last.status, "error")

    def test_err_count_tracks_failures(self) -> None:
        interpreter, _output, _errors = self.run_source(
            "print (<{missing}>)\n"
            "math_run err_count() = ? -int64\n"
        )
        self.assertEqual(interpreter.last.value, "1")
        self.assertEqual(interpreter.error_count, 1)

    def test_exit_stops_the_program_with_a_status_code(self) -> None:
        with self.assertRaises(ExitRequest) as caught:
            self.run_source('math_run 1 + 1 = ? -int32\nexit(7)\nprint (<{"unreachable"}>)\n')
        self.assertEqual(caught.exception.code, 7)

    def test_err_count_rejects_arguments(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "err_count takes no arguments"):
            self.run_source("math_run err_count(1) = ?\n")


    #%% ---- v5: bitwise operators ----%%
    def run_math(self, expression: str, mode: str = "int64") -> str:
        interpreter, _output, _errors = self.run_source(
            f"math_run {expression} = ? -{mode}\n"
            "if up = (ok) goto (push ({r} = {(in = up)}))\n"
            "print (<{r}>)\n"
        )
        return interpreter.last.value

    def test_bitwise_and_or_xor(self) -> None:
        self.assertEqual(self.run_math("12 & 10"), "8")
        self.assertEqual(self.run_math("12 | 10"), "14")
        self.assertEqual(self.run_math("12 ^ 10"), "6")

    def test_bitwise_shifts_and_invert(self) -> None:
        self.assertEqual(self.run_math("1 << 10"), "1024")
        self.assertEqual(self.run_math("1024 >> 3"), "128")
        self.assertEqual(self.run_math("~0"), "-1")
        self.assertEqual(self.run_math("~5"), "-6")

    def test_bitwise_on_negative_values(self) -> None:
        self.assertEqual(self.run_math("-9 | 7"), "-9")
        self.assertEqual(self.run_math("-1 >> 1"), "-1")

    def test_bitwise_requires_integer_mode(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "bitwise operators require an integer math mode"):
            self.run_source("math_run 12.0 & 10 = ?\n")

    #%% ---- v5: hex and unhex ----%%
    def test_hex_encodes_with_optional_width(self) -> None:
        interpreter, output, _errors = self.run_source(
            "math_run hex(255, 4) = ?\n"
            "if up = (ok) goto (push ({a} = {(in = up)}))\n"
            "math_run hex(48879) = ?\n"
            "if up = (ok) goto (push ({b} = {(in = up)}))\n"
            "print (<{a}>)\n"
            "print (<{b}>)\n"
        )
        self.assertEqual(output, "00ff\nbeef\n")

    def test_unhex_round_trips_large_values(self) -> None:
        self.assertEqual(self.run_math("unhex(hex(2 ** 200)) + 0", "bigint"), str(2**200))

    #%% ---- v5: uint32 math mode ----%%
    def test_uint32_wraps_around(self) -> None:
        self.assertEqual(self.run_math("4294967295 + 2", "uint32"), "1")
        self.assertEqual(self.run_math("3 - 5", "uint32"), "4294967294")
        self.assertEqual(self.run_math("65536 * 65536", "uint32"), "0")

    def test_uint32_shifts_are_logical(self) -> None:
        self.assertEqual(self.run_math("4294967295 >> 1", "uint32"), "2147483647")

    #%% ---- v5: bytes arrays ----%%
    def test_bytes_arrays_get_set_and_len(self) -> None:
        interpreter, output, _errors = self.run_source(
            "bytes data = [1, 2, 3]\n"
            "math_run bytes_len(data) = ?\n"
            "if up = (ok) goto (push ({n} = {(in = up)}))\n"
            "bytes_set(data, 1, 250)\n"
            "math_run bytes_get(data, 1) = ?\n"
            "if up = (ok) goto (push ({v} = {(in = up)}))\n"
            "print (<{n}>)\n"
            "print (<{v}>)\n"
        )
        self.assertEqual(output, "3\n250\n")

    def test_bytes_reject_values_outside_one_byte(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "bytes elements must be integers in 0..255"):
            self.run_source("bytes data = [300]\n")
        with self.assertRaisesRegex(RuntimeError, "bytes elements must be integers in 0..255"):
            self.run_source("bytes data = [-1]\n")

    #%% ---- v5: SHA-256 and AES-128 examples ----%%
    def test_sha256_example_matches_fips_vector(self) -> None:
        example = Path(__file__).resolve().parent.parent / "examples" / "sha256.cos"
        interpreter, output, _errors = self.run_source(example.read_text(encoding="utf-8"))
        self.assertEqual(
            output.strip(),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_aes128_example_matches_fips197_vector(self) -> None:
        example = Path(__file__).resolve().parent.parent / "examples" / "aes128.cos"
        interpreter, output, _errors = self.run_source(example.read_text(encoding="utf-8"))
        self.assertEqual(output.strip(), "69c4e0d86a7b0430d8cdb78070b4c55a")



    #%% ---- v5: NxM matrices ----%%
    def matrix_values(self, expression: str, rows: int, columns: int) -> list[str]:
        source = ""
        for row in range(rows):
            for column in range(columns):
                source += (
                    f"math_run matrix_at({expression}, {row}, {column}) = ?\n"
                    f"if up = (ok) goto (push ({{v{row}_{column}}} = {{(in = up)}}))\n"
                )
        for row in range(rows):
            for column in range(columns):
                source += f"print (<{{v{row}_{column}}}>)\n"
        _interpreter, output, _errors = self.run_source(source)
        return output.split()

    def test_matrix_shape_queries(self) -> None:
        self.assertEqual(self.run_math("matrix_rows([[1,2,3],[4,5,6]])"), "2")
        self.assertEqual(self.run_math("matrix_cols([[1,2,3],[4,5,6]])"), "3")
        self.assertEqual(self.run_math("matrix_rows([1,2,3,4])"), "2")

    def test_matrix_multiplication(self) -> None:
        self.assertEqual(
            self.matrix_values("matrix_mul([[1,2],[3,4]], [[5,6],[7,8]])", 2, 2),
            ["19", "22", "43", "50"],
        )

    def test_matrix_multiplication_dimension_mismatch(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "matrix_mul inner dimensions do not match"):
            self.run_source("math_run matrix_rows(matrix_mul([[1,2]], [[1,2]])) = ?\n")

    def test_matrix_transpose_keeps_non_square_shape(self) -> None:
        self.assertEqual(
            self.matrix_values("matrix_transpose([[1,2,3],[4,5,6]])", 3, 2),
            ["1", "4", "2", "5", "3", "6"],
        )

    def test_matrix_inverse_of_order_two_and_three(self) -> None:
        self.assertEqual(
            self.matrix_values("matrix2_inverse([[4,7],[2,6]])", 2, 2),
            ["0.6", "-0.7", "-0.2", "0.4"],
        )
        values = self.matrix_values("matrix3_inverse([[1,2,3],[0,1,4],[5,6,0]])", 3, 3)
        expected = [-24, 18, 5, 20, -15, -4, -5, 4, 1]
        self.assertEqual(len(values), len(expected))
        for actual, wanted in zip(values, expected):
            self.assertAlmostEqual(float(actual), wanted, places=9)

    def test_matrix_inverse_detects_singular_matrices(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "matrix is singular"):
            self.run_source("push ({m} = matrix2_inverse([[1,2],[2,4]]))\n")

    def test_matrix_variables_copy_by_value(self) -> None:
        _interpreter, output, _errors = self.run_source(
            "push ({a} = matrix_mul([[1,2],[3,4]], [[1,0],[0,1]]))\n"
            "push ({b} = {a})\n"
            "math_run matrix_at({b}, 1, 1) = ?\n"
            "if up = (ok) goto (push ({v} = {(in = up)}))\n"
            "print (<{v}>)\n"
        )
        self.assertEqual(output, "4\n")

    def test_matrix_declared_with_two_dimensional_array(self) -> None:
        _interpreter, output, _errors = self.run_source(
            "array A = [[1,2],[3,4]]\n"
            "math_run matrix_at(A, 1, 0) = ?\n"
            "if up = (ok) goto (push ({v} = {(in = up)}))\n"
            "print (<{v}>)\n"
        )
        self.assertEqual(output, "3\n")

    def test_matrix_values_cannot_be_scalar_results(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "cannot be used as scalar math_run results"):
            self.run_source("math_run matrix_mul([[1,2],[3,4]], [[1,0],[0,1]]) = ?\n")



    #%% ---- v5: solve_linear ----%%
    def test_solve_linear_two_by_two(self) -> None:
        _interpreter, output, _errors = self.run_source(
            "push ({x} = solve_linear([[1,1],[2,3]], [3,8]))\n"
            "math_run array_at({x}, 0) = ?\n"
            "if up = (ok) goto (push ({a} = {(in = up)}))\n"
            "math_run array_at({x}, 1) = ?\n"
            "if up = (ok) goto (push ({b} = {(in = up)}))\n"
            "print (<{a}>)\n"
            "print (<{b}>)\n"
        )
        self.assertEqual(output, "1\n2\n")

    def test_solve_linear_three_by_three(self) -> None:
        _interpreter, output, _errors = self.run_source(
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
        self.assertEqual(output, "2\n3\n-1\n")

    def test_solve_linear_accepts_named_arrays(self) -> None:
        _interpreter, output, _errors = self.run_source(
            "array A = [[1,1],[2,3]]\n"
            "array B = [3,8]\n"
            "push ({x} = solve_linear(A, B))\n"
            "math_run array_at({x}, 1) = ?\n"
            "if up = (ok) goto (push ({a} = {(in = up)}))\n"
            "print (<{a}>)\n"
        )
        self.assertEqual(output, "2\n")

    def test_solve_linear_rejects_singular_systems(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "matrix is singular and cannot be solved"):
            self.run_source("push ({x} = solve_linear([[1,2],[2,4]], [1,2]))\n")

    def test_solve_linear_rejects_mismatched_dimensions(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "square matrix"):
            self.run_source("push ({x} = solve_linear([[1,2,3],[4,5,6]], [1,2]))\n")
        with self.assertRaisesRegex(RuntimeError, "one value per row"):
            self.run_source("push ({x} = solve_linear([[1,2],[3,4]], [1,2,3]))\n")



    #%% ---- v5: statistics and numerical integration ----%%
    def test_median_handles_odd_and_even_lengths(self) -> None:
        self.assertEqual(self.run_math("median([3,1,2])", mode="float64"), "2")
        self.assertEqual(self.run_math("median([4,1,3,2])", mode="float64"), "2.5")

    def test_variance_is_population_variance(self) -> None:
        self.assertEqual(self.run_math("variance([1,2,3,4])", mode="float64"), "1.25")

    def test_correlation_of_proportional_arrays(self) -> None:
        self.assertEqual(self.run_math("correlation([1,2,3],[2,4,6])", mode="float64"), "1")
        self.assertEqual(self.run_math("correlation([1,2,3],[6,4,2])", mode="float64"), "-1")

    def test_correlation_rejects_constant_arrays(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "undefined for a constant array"):
            self.run_source("math_run correlation([1,1,1],[1,2,3]) = ?\n")

    def test_percentile_interpolates(self) -> None:
        self.assertEqual(self.run_math("percentile([1,2,3,4,5], 50)", mode="float64"), "3")
        self.assertEqual(self.run_math("percentile([1,2,3,4], 25)", mode="float64"), "1.75")
        self.assertEqual(self.run_math("percentile([1,2,3,4], 0)", mode="float64"), "1")
        self.assertEqual(self.run_math("percentile([1,2,3,4], 100)", mode="float64"), "4")

    def test_percentile_rejects_out_of_range_percentages(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "between 0 and 100"):
            self.run_source("math_run percentile([1,2,3], 101) = ?\n")

    def test_trapz_and_simpson_integration(self) -> None:
        self.assertEqual(self.run_math("trapz([0,1,4,9], 1)", mode="float64"), "9.5")
        self.assertEqual(self.run_math("simpson([0,1,4,9,16], 1)", mode="float64"), "21.3333333333333")

    def test_simpson_requires_an_odd_sample_count(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "odd number of samples"):
            self.run_source("math_run simpson([0,1,4,9], 1) = ?\n")



    #%% ---- v5: special functions ----%%
    def run_float_math(self, expression: str) -> float:
        return float(self.run_math(expression, mode="float64"))

    def test_lgamma_and_erfc(self) -> None:
        self.assertAlmostEqual(self.run_float_math("lgamma(5)"), math.lgamma(5.0), places=12)
        self.assertAlmostEqual(self.run_float_math("erfc(0.5)"), math.erfc(0.5), places=12)
        self.assertAlmostEqual(self.run_float_math("erfc(0)"), 1.0, places=12)

    def test_beta_matches_the_gamma_ratio(self) -> None:
        self.assertAlmostEqual(self.run_float_math("beta(2,3)"), 1.0 / 12.0, places=12)
        self.assertAlmostEqual(
            self.run_float_math("beta(0.5,0.5)"), math.pi, places=12
        )

    def test_zeta_matches_closed_forms(self) -> None:
        self.assertAlmostEqual(self.run_float_math("zeta(2)"), math.pi**2 / 6, places=12)
        self.assertAlmostEqual(self.run_float_math("zeta(4)"), math.pi**4 / 90, places=12)
        self.assertAlmostEqual(
            self.run_float_math("zeta(3)"), 1.2020569031595943, places=12
        )

    def test_zeta_rejects_arguments_at_or_below_one(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "greater than 1"):
            self.run_source("math_run zeta(1) = ? -float64\n")



    #%% ---- v5: examples stay byte-identical to the compiled binaries ----%%
    def test_bitops_example(self) -> None:
        example = Path(__file__).resolve().parent.parent / "examples" / "bitops.cos"
        _interpreter, output, _errors = self.run_source(example.read_text(encoding="utf-8"))
        self.assertEqual(
            output.split(),
            ["8", "14", "6", "1024", "4294967290", "878082066",
             "1", "4294967295", "2147483647", "ff", "beef", "6", "99"],
        )

    def test_linear_algebra_example(self) -> None:
        example = Path(__file__).resolve().parent.parent / "examples" / "linear_algebra.cos"
        _interpreter, output, _errors = self.run_source(example.read_text(encoding="utf-8"))
        values = output.split()
        expected = ["2", "3", "6", "3", "2", "25", "100", "3", "6", "0.6", "0.4", "-24"]
        self.assertEqual(values[:12], expected)
        self.assertAlmostEqual(float(values[12]), 1.0, places=12)
        self.assertEqual(values[13:], ["1", "0", "2", "1", "2", "2", "3", "-1"])

    def test_numerical_example(self) -> None:
        example = Path(__file__).resolve().parent.parent / "examples" / "numerical.cos"
        _interpreter, output, _errors = self.run_source(example.read_text(encoding="utf-8"))
        self.assertEqual(
            output.split(),
            ["22", "21.3333333333", "44", "22", "18", "15.5", "151.666666667",
             "12.3153021346", "4", "42", "6", "4", "15.5", "42", "1", "-1",
             "3.17805383035", "1", "0.0833333333333", "1.64493406685",
             "1.08232323371", "0.34375", "0.3359375", "0.333333333333"],
        )



    def test_named_one_dimensional_array_is_not_a_matrix(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "value is not a matrix"):
            self.run_source("array flat = [1,2,3,4,5,6]\nmath_run matrix_rows(flat) = ?\n")



if __name__ == "__main__":
    unittest.main()
