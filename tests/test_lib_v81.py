"""Tests for the v8.1 standard library, packages and CSV example support.

Every parity test runs the same source through the interpreter and through the
GCC backend, then asserts byte-for-byte equal stdout.
"""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from cosmic_interpreter.compiler import compile_source
from cosmic_interpreter.parser import parse
from cosmic_interpreter.runtime import Interpreter
from cosmic_interpreter.source_loader import load_source

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "lib"
PACKAGES_DIR = REPO_ROOT / "packages"


def include_path(target: Path) -> str:
    """Path for ``#%include`` from a temporary directory created under tests/."""
    return os.path.relpath(target, REPO_ROOT / "tests" / "probe").replace("\\", "/")


class LibraryParityTestCase(unittest.TestCase):
    def run_both(self, source: str, *, name: str = "probe.cos") -> tuple[str, str]:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT / "tests") as temporary:
            directory = Path(temporary)
            source_path = directory / name
            source_path.write_text(source, encoding="utf-8")

            interpreted_output = io.StringIO()
            Interpreter(
                output=interpreted_output, error_output=io.StringIO()
            ).run(parse(load_source(source_path)))

            _c_path, executable = compile_source(
                source_path, output_path=directory / "probe.exe"
            )
            completed = subprocess.run(
                [str(executable)], capture_output=True, timeout=120, check=False
            )
        self.assertEqual(
            completed.returncode, 0, completed.stderr.decode(errors="replace")
        )
        compiled = completed.stdout.decode(errors="replace").replace("\r\n", "\n")
        return interpreted_output.getvalue(), compiled

    def assert_parity(self, source: str, expected: list[str]) -> None:
        interpreted, compiled = self.run_both(source)
        self.assertEqual(compiled, interpreted)
        self.assertEqual(interpreted.splitlines(), expected)


class NumTheoryTests(LibraryParityTestCase):
    def test_gcd_is_prime_comb_and_friends_match_compiled(self) -> None:
        source = (
            f'#%include = "{include_path(LIB_DIR / "numtheory.cos")}"\n'
            "math_run gcd(48, 18) = ?\nprint (<{(in = up)}>)\n"
            "math_run lcm(4, 6) = ?\nprint (<{(in = up)}>)\n"
            "math_run is_prime(97) = ?\nprint (<{(in = up)}>)\n"
            "math_run is_prime(91) = ?\nprint (<{(in = up)}>)\n"
            "math_run factor_count(12) = ?\nprint (<{(in = up)}>)\n"
            "math_run comb(5, 2) = ?\nprint (<{(in = up)}>)\n"
            "math_run perm(5, 2) = ?\nprint (<{(in = up)}>)\n"
        )
        self.assert_parity(source, ["6", "12", "1", "0", "6", "10", "20"])


class AlgoTests(LibraryParityTestCase):
    def test_sorted_median_and_sorted_total_match_compiled(self) -> None:
        include = include_path(LIB_DIR / "algo.cos")
        source = (
            f'#%include = "{include}"\n'
            "math_run sorted_median(3, 1, 2) = ?\nprint (<{(in = up)}>)\n"
            "math_run sorted_median4(8, 2, 5, 1) = ?\nprint (<{(in = up)}>)\n"
            "math_run sorted_total(3, 1, 2) = ?\nprint (<{(in = up)}>)\n"
            "math_run has_value(3, 1, 2, 7) = ?\nprint (<{(in = up)}>)\n"
        )
        self.assert_parity(source, ["2", "3.5", "6", "0"])


class TextTests(LibraryParityTestCase):
    def test_char_code_helpers_match_compiled(self) -> None:
        include = include_path(LIB_DIR / "text.cos")
        source = (
            f'#%include = "{include}"\n'
            "math_run char_to_upper(97) = ?\nprint (<{(in = up)}>)\n"
            "math_run char_to_lower(90) = ?\nprint (<{(in = up)}>)\n"
            "math_run char_is_hex(70) = ?\nprint (<{(in = up)}>)\n"
            "math_run digit_value(102) = ?\nprint (<{(in = up)}>)\n"
        )
        self.assert_parity(source, ["65", "122", "1", "15"])

    def test_text_wrappers_match_compiled(self) -> None:
        # v9：文本参数/返回终于可以写成真包装（两条后端一致）。
        include = include_path(LIB_DIR / "text.cos")
        source = (
            f'#%include = "{include}"\n'
            'push ({T} = {"Hello Cosmic World"})\n'
            "math_run text_len({T}) = ?\nprint (<{(in = up)}>)\n"
            "math_run text_has({T}, \"Cosmic\") = ?\nprint (<{(in = up)}>)\n"
            "math_run text_upper({T}) = ?\nprint (<{(in = up)}>)\n"
            "math_run text_field({\"a,b,c\"}, \",\", 2) = ?\nprint (<{(in = up)}>)\n"
            "math_run text_fields({\"a,b,c\"}, \",\") = ?\nprint (<{(in = up)}>)\n"
            "math_run text_repeat(\"-\", 3) = ?\nprint (<{(in = up)}>)\n"
            "math_run text_starts({T}, \"Hello\") = ?\nprint (<{(in = up)}>)\n"
        )
        self.assert_parity(
            source,
            ["18", "1", "HELLO COSMIC WORLD", "b", "3", "---", "1"],
        )


class DataTests(LibraryParityTestCase):
    def test_csv_rows_and_csv_cols_match_compiled(self) -> None:
        include = include_path(LIB_DIR / "data.cos")
        source = (
            'push ({CSV_TEXT} = {"name,score\\nann,10\\nbob,30\\ncid,20"})\n'
            'push ({CSV_SEP} = {","})\n'
            f'#%include = "{include}"\n'
            "print (<{csv_rows}>)\n"
            "print (<{csv_cols}>)\n"
            "print (<{csv_data_rows}>)\n"
        )
        self.assert_parity(source, ["4", "2", "3"])


class PackageTests(LibraryParityTestCase):
    def test_algokit_and_datakit_match_compiled(self) -> None:
        algokit = include_path(PACKAGES_DIR / "algokit" / "lib.cos")
        datakit = include_path(PACKAGES_DIR / "datakit" / "lib.cos")
        source = (
            f'#%include = "{algokit}"\n'
            f'#%include = "{datakit}"\n'
            'push ({CSV} = {"name,score\\nann,10\\nbob,30"})\n'
            "math_run sorted_sum(3, 1, 2) = ?\nprint (<{(in = up)}>)\n"
            "math_run contains(3, 1, 2, 7) = ?\nprint (<{(in = up)}>)\n"
            "math_run csv_row_count(line_count({CSV})) = ?\nprint (<{(in = up)}>)\n"
            'math_run csv_col_count(count_text(line_at({CSV}, 1), ",")) = ?\n'
            "print (<{(in = up)}>)\n"
        )
        self.assert_parity(source, ["6", "0", "3", "2"])


class RandomTests(LibraryParityTestCase):
    def test_secure_random_ranges_in_interpreter_and_compiled(self) -> None:
        include = include_path(LIB_DIR / "random.cos")
        source = (
            f'#%include = "{include}"\n'
            "math_run secure_percent() = ?\nprint (<{(in = up)}>)\n"
            "math_run secure_bool() = ?\nprint (<{(in = up)}>)\n"
            "math_run secure_dice() = ?\nprint (<{(in = up)}>)\n"
        )
        interpreted, compiled = self.run_both(source)
        for output in (interpreted, compiled):
            lines = output.splitlines()
            self.assertEqual(len(lines), 3)
            self.assertIn(int(lines[0]), range(0, 101))
            self.assertIn(int(lines[1]), range(0, 2))
            self.assertIn(int(lines[2]), range(1, 7))


if __name__ == "__main__":
    unittest.main()
