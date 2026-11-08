from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cosmic_interpreter.compiler import CompileError, compile_source
from cosmic_interpreter.c_backend import generate_c
from cosmic_interpreter.parser import parse


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

    def test_compiler_rejects_uninitialized_braced_variable_reference(self) -> None:
        completed = self.compile_and_run("math_run {missing} + 1 = ?\n")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(b"numeric variable is empty or killed", completed.stderr)

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



if __name__ == "__main__":
    unittest.main()
