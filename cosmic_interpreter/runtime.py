from __future__ import annotations

import ast
import cmath
import ctypes
import ctypes.util
import math
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, TextIO

from .expression_syntax import transform_braced_expression
from .numeric_syntax import normalize_rag_calls
from .parser import Program, Statement


MathValue = int | float | complex | str | list[float | complex]


class ExitRequest(Exception):
    """Raised by exit() to stop the program with a status code."""

    def __init__(self, code: int = 0) -> None:
        super().__init__(f"exit({code})")
        self.code = code


class RuntimeError(Exception):
    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"line {line}: {message}")
        self.line = line
        self.message = message


@dataclass
class Matrix:
    """Row-major matrix with an explicit shape."""

    rows: int
    columns: int
    data: list[float | complex]

    def at(self, row: int, column: int) -> float | complex:
        return self.data[row * self.columns + column]

    def copy(self) -> "Matrix":
        return Matrix(self.rows, self.columns, self.data.copy())


@dataclass
class Variable:
    value: bytes | None = None
    encoding: str | None = None
    killed: bool = False
    array_value: list[float | complex] | None = None
    matrix_value: Matrix | None = None

    def text(self) -> str | None:
        if self.value is None or self.array_value is not None or self.matrix_value is not None:
            return None
        return self.value.decode(self.encoding or "utf-8")


@dataclass
class Result:
    status: str = "ok"
    value: str | None = None


@dataclass
class Tape:
    pointer: int = 0
    cells: dict[int, int] = field(default_factory=dict)

    def read(self) -> int:
        return self.cells.get(self.pointer, 0)

    def write(self, symbol: int) -> None:
        self.cells[self.pointer] = symbol


@dataclass
class Context:
    variables: dict[str, Variable] = field(default_factory=dict)
    imports: set[str] = field(default_factory=set)
    tapes: dict[str, Tape] = field(default_factory=dict)
    arrays: dict[str, list[float | complex]] = field(default_factory=dict)
    matrix_arrays: dict[str, Matrix] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)
    lock_depth: int = field(default=0, repr=False, compare=False)
    lock_owner: str | None = field(default=None, repr=False, compare=False)

    def inherit(self) -> Context:
        return Context(
            variables={
                name: Variable(
                    value.value,
                    value.encoding,
                    value.killed,
                    value.array_value.copy() if value.array_value is not None else None,
                    value.matrix_value.copy() if value.matrix_value is not None else None,
                )
                for name, value in self.variables.items()
            },
            imports=set(self.imports),
            tapes=dict(self.tapes),
            arrays={name: values.copy() for name, values in self.arrays.items()},
            matrix_arrays={
                name: value.copy() for name, value in self.matrix_arrays.items()
            },
        )


_NEVER_STOP = threading.Event()


class AtomBlock:
    """Runtime state of one execution block running under the atom component."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.condition = threading.Condition()
        self.status = "idle"
        self.waiting = False
        self.stop = threading.Event()
        self.context: Context | None = None
        self.parent_name: str | None = None
        self.thread: threading.Thread | None = None

    def state(self) -> str:
        with self.condition:
            if self.status in {"idle", "running"} and self.waiting:
                return "idle"
            return self.status

    def set_state(self, status: str, waiting: bool = False) -> None:
        with self.condition:
            self.status = status
            self.waiting = waiting
            self.condition.notify_all()

    def wait_until(self, predicate: Callable[[], bool], timeout: float | None) -> bool:
        with self.condition:
            if predicate():
                return True
            return self.condition.wait_for(predicate, timeout)


_DLL_PARAM_TYPES: dict[str, object] = {
    "INT": ctypes.c_int,
    "CHAR": ctypes.c_int,
    "UINT": ctypes.c_uint,
    "DWORD": ctypes.c_uint,
    "WORD": ctypes.c_uint,
    "BYTE": ctypes.c_uint,
    "LONG": ctypes.c_long,
    "FLOAT": ctypes.c_float,
    "DOUBLE": ctypes.c_double,
    "STR": ctypes.c_char_p,
    "LPCSTR": ctypes.c_char_p,
    "PTR": ctypes.c_void_p,
    "HWND": ctypes.c_void_p,
    "VOID": ctypes.c_void_p,
}

_DLL_INTEGRAL_TYPES = {"INT", "CHAR", "UINT", "DWORD", "WORD", "BYTE", "LONG"}
_DLL_STRING_TYPES = {"STR", "LPCSTR"}
_DLL_POINTER_TYPES = {"PTR", "HWND", "VOID"}


def _find_library(name: str) -> str | None:
    try:
        return ctypes.util.find_library(name)
    except OSError:
        return None


def _dll_library_candidates(name: str) -> list[str]:
    if os.name == "nt":
        candidates = [name]
        if not name.lower().endswith((".dll", ".drv")):
            candidates.append(name + ".dll")
        candidates.append(_find_library(name))
        # Python 3.8 起 ctypes 不再搜索当前工作目录，
        # 也不搜索 PATH。显式把 cwd / PATH / 脚本目录全试一遍。
        base = name if name.lower().endswith(".dll") else name + ".dll"
        probe_dirs = [os.getcwd()]
        for entry in os.environ.get("PATH", "").split(os.pathsep):
            if entry:
                probe_dirs.append(entry)
        for directory in probe_dirs:
            full = os.path.join(directory, base)
            if os.path.isfile(full):
                candidates.append(full)
    else:
        # Linux 上 libc.so 往往是一个 ASCII 链接脚本（不是 ELF），
        # 真正的共享库是 libc.so.6。find_library('c') 能拿到正确名字。
        found = _find_library(name)
        # 名字带 lib 前缀时，去掉前缀再查一次（libc -> c）
        if name.startswith("lib"):
            stripped = _find_library(name[3:])
            if stripped:
                found = stripped
        candidates = []
        if found:
            candidates.append(found)
        candidates.append(name)
        if not name.startswith("lib"):
            candidates.append(f"lib{name}.so")
        else:
            candidates.append(f"{name}.so")
        if not found and not name.startswith("lib"):
            candidates.append(name)
    unique: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in unique:
            unique.append(candidate)
    return unique


def _dll_load(name: str, *, use_stdcall_windows: bool) -> object:
    last_error = f"cannot load library '{name}'"
    for candidate in _dll_library_candidates(name):
        try:
            if os.name == "nt":
                return ctypes.WinDLL(candidate) if use_stdcall_windows else ctypes.CDLL(candidate)
            return ctypes.CDLL(candidate)
        except OSError as error:
            last_error = f"cannot load library '{candidate}': {error}"
    raise OSError(last_error)


def _dll_convert_argument(
    value: object,
    type_name: str,
    function: str,
    position: int,
) -> object:
    def reject(expected: str) -> ValueError:
        if isinstance(value, str):
            return ValueError(
                f"argument {position} of '{function}' expects {expected} but got text {value!r}"
            )
        return ValueError(
            f"argument {position} of '{function}' expects {expected} but got {value!r}"
        )

    if type_name in _DLL_INTEGRAL_TYPES:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if not value.is_integer():
                raise ValueError(
                    f"argument {position} of '{function}' expects {type_name} but got "
                    f"non-integral number {value!r}"
                )
            return int(value)
        if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value):
            return int(value)
        raise reject(type_name)
    if type_name in {"FLOAT", "DOUBLE"}:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        raise reject(type_name)
    if type_name in _DLL_STRING_TYPES:
        if isinstance(value, str):
            return value.encode("utf-8")
        if isinstance(value, int) and not isinstance(value, bool) and value == 0:
            return None
        raise reject(f"{type_name} (text or 0)")
    if type_name in _DLL_POINTER_TYPES:
        if isinstance(value, int) and not isinstance(value, bool):
            return None if value == 0 else int(value)
        if isinstance(value, float):
            if not value.is_integer():
                raise reject(type_name)
            return None if value == 0 else int(value)
        raise reject(type_name)
    raise ValueError(f"argument {position} of '{function}' has unsupported type {type_name}")


def _dll_resolve_export(handle: object, function_name: str) -> Callable[..., object] | None:
    # The parser lowercases names while ctypes keeps the export's real casing,
    # so try the common shapes before falling back to a table scan.
    first, rest = function_name[:1], function_name[1:]
    for candidate in (function_name, first.upper() + rest, function_name.upper()):
        try:
            return getattr(handle, candidate)
        except (AttributeError, TypeError):
            continue
    if os.name == "nt":
        for exported in _dll_windows_export_names(handle):
            if exported.lower() == function_name:
                try:
                    return getattr(handle, exported)
                except (AttributeError, TypeError):
                    break
        return None
    # ELF/Mach-O loaders already resolve case-sensitively and the parser's
    # lowercase form matches the POSIX convention (getpid, strlen, ...).
    return None


def _dll_windows_export_names(handle: object) -> list[str]:
    """Return the export names of a loaded Windows module via its PE header.

    Only reads the mapped image; no function is ever invoked.
    """
    try:
        base = getattr(handle, "_handle", 0) or 0
    except (TypeError, AttributeError):
        base = 0
    if not base:
        return []
    try:
        module_address = int(base)
        nt_headers = module_address + ctypes.c_int32.from_address(module_address + 0x3C).value
        export_directory_rva = ctypes.c_uint32.from_address(
            nt_headers + (0x88 if _dll_is_64bit() else 0x78)
        ).value
        if not export_directory_rva:
            return []
        export_directory = module_address + export_directory_rva
        name_count = ctypes.c_uint32.from_address(export_directory + 24).value
        name_table_rva = ctypes.c_uint32.from_address(export_directory + 32).value
        if not name_count or not name_table_rva:
            return []
        names: list[str] = []
        for index in range(min(int(name_count), 65536)):
            name_rva = ctypes.c_uint32.from_address(
                module_address + name_table_rva + index * 4
            ).value
            if not name_rva:
                continue
            names.append(
                ctypes.string_at(module_address + name_rva).decode("ascii", "replace")
            )
        return names
    except (OSError, ValueError, AttributeError):
        return []


def _dll_is_64bit() -> bool:
    return ctypes.sizeof(ctypes.c_void_p) == 8


def _dll_format_result(value: object, return_type: str, precision: int) -> str:
    if return_type == "VOID":
        return "ok"
    if return_type in _DLL_STRING_TYPES:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)
    if return_type in _DLL_POINTER_TYPES:
        return "0" if not value else str(int(value))  # type: ignore[call-overload]
    if return_type in _DLL_INTEGRAL_TYPES:
        return str(int(value))  # type: ignore[call-overload]
    if return_type in {"FLOAT", "DOUBLE"}:
        return _format_numeric(float(value), precision)  # type: ignore[arg-type]
    return str(value)


def _unicode_character(codepoint: float) -> str:
    if not math.isfinite(codepoint) or not codepoint.is_integer():
        raise ValueError("chr requires an integer Unicode code point")
    value = int(codepoint)
    if not 0 <= value <= 0x10FFFF or 0xD800 <= value <= 0xDFFF:
        raise ValueError("chr code point must be a valid Unicode scalar value")
    if value == 0:
        raise ValueError("chr does not support NUL characters")
    return chr(value)


def _unicode_codepoint(value: str) -> int:
    if len(value) != 1:
        raise ValueError("ord requires exactly one Unicode character")
    return ord(value)


def _unicode_length(value: str) -> int:
    return len(value)


def _unicode_at(value: str, index: float) -> str:
    if not math.isfinite(index) or not index.is_integer():
        raise ValueError("char_at index must be an integer")
    position = int(index)
    if not 0 <= position < len(value):
        raise ValueError("char_at index is outside the string")
    return value[position]


def _eval_array_literal(
    node: ast.expr,
    values: dict[str, MathValue],
    *,
    complex_mode: bool,
    functions: dict[str, tuple[tuple[str, ...], str, int]] | None = None,
) -> list[float | complex]:
    if isinstance(node, ast.Name) and isinstance(values.get(node.id), list):
        return values[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        function_name = node.func.id.lower()
        if function_name in {
            "matrix_mul", "matrix_transpose", "matrix2_inverse", "matrix3_inverse",
        }:
            matrix_value = _eval_matrix_root(
                node, values, complex_mode=complex_mode, functions=functions
            )
            if matrix_value is None:
                raise ValueError(f"{function_name} did not produce a matrix")
            return matrix_value.data
        if function_name == "solve_linear":
            result = _eval_matrix_builtin(
                function_name,
                node,
                values,
                complex_mode=complex_mode,
                functions=functions,
            )
            if isinstance(result, Matrix):
                raise ValueError("solve_linear did not produce a solution vector")
            return list(result)
        if function_name == "array_set":
            if len(node.args) != 3 or node.keywords:
                raise ValueError("array_set expects an array, an index, and a value")
            result = _eval_array_literal(
                node.args[0], values, complex_mode=complex_mode, functions=functions
            )
            if not (
                isinstance(node.args[0], ast.Name)
                and isinstance(values.get(node.args[0].id), list)
            ):
                result = result.copy()
            index = _eval_numeric_node(
                node.args[1], values, complex_mode=complex_mode, functions=functions
            )
            value = _eval_numeric_node(
                node.args[2], values, complex_mode=complex_mode, functions=functions
            )
            if isinstance(index, (str, complex)) or not math.isfinite(index) or not float(index).is_integer():
                raise ValueError("array index must be an integer")
            position = int(index)
            if not 0 <= position < len(result):
                raise ValueError("array index is outside the array")
            if isinstance(value, (str, list)) or (
                isinstance(value, complex) and not complex_mode
            ):
                raise ValueError("mutable array operations support real values only outside complex mode")
            result[position] = value
            return result
        if function_name == "array_push":
            if len(node.args) != 2 or node.keywords:
                raise ValueError("array_push expects an array and a value")
            result = _eval_array_literal(
                node.args[0], values, complex_mode=complex_mode, functions=functions
            )
            if not (
                isinstance(node.args[0], ast.Name)
                and isinstance(values.get(node.args[0].id), list)
            ):
                result = result.copy()
            value = _eval_numeric_node(
                node.args[1], values, complex_mode=complex_mode, functions=functions
            )
            if isinstance(value, (str, list)) or (
                isinstance(value, complex) and not complex_mode
            ):
                raise ValueError("mutable array operations support real values only outside complex mode")
            if len(result) >= 1_000_000:
                raise ValueError("arrays may contain at most one million items")
            result.append(value)
            return result
    if not isinstance(node, ast.List) or len(node.elts) > 1_000_000:
        raise ValueError("array argument must be a list literal or an array_set/array_push result")
    result: list[float | complex] = []
    for element in node.elts:
        if isinstance(element, ast.List):
            result.extend(
                _eval_array_literal(
                    element, values, complex_mode=complex_mode, functions=functions
                )
            )
            continue
        value = _eval_numeric_node(element, values, complex_mode=complex_mode, functions=functions)
        if isinstance(value, str) or isinstance(value, list):
            raise ValueError("array elements must be numeric")
        result.append(value)
    return result


def _roll_die(maximum: float, minimum: float) -> int:
    bounds = (minimum, maximum)
    # int 没有 is_integer()（Python 3.12 之前），先统一转 float 再检查
    if any(not math.isfinite(float(bound)) or float(bound) != int(bound) for bound in bounds):
        raise ValueError("rag bounds must be integers")
    lower, upper = int(minimum), int(maximum)
    if not -(2**31) <= lower <= upper <= 2**31 - 1:
        raise ValueError("rag requires int32 bounds with min <= max")
    return random.SystemRandom().randint(lower, upper)


def _fixed_precision(precision: int) -> int:
    if isinstance(precision, bool) or not isinstance(precision, int):
        raise ValueError("precision must be an integer")
    if not 0 <= precision <= 1000:
        raise ValueError("precision must be between 0 and 1000")
    return precision


def _atan_reciprocal_scaled(denominator: int, scale: int) -> int:
    total = 0
    power = denominator
    index = 0
    while True:
        term = scale // (power * (2 * index + 1))
        if term == 0:
            return total
        total = total + term if index % 2 == 0 else total - term
        power *= denominator * denominator
        index += 1


def _fixed_constant_digits(name: str, precision: int) -> int:
    precision = _fixed_precision(precision)
    guard = 12
    scale = 10 ** (precision + guard)
    if name == "pi":
        scaled = 16 * _atan_reciprocal_scaled(5, scale)
        scaled -= 4 * _atan_reciprocal_scaled(239, scale)
    elif name == "e":
        scaled = scale
        term = scale
        for divisor in range(1, precision + guard + 2):
            term //= divisor
            if term == 0:
                break
            scaled += term
    elif name == "sqrt2":
        scaled = math.isqrt(2 * scale * scale)
    else:
        raise ValueError(f"unknown fixed-point constant '{name}'")
    return scaled // (10 ** guard)


def _fixed_constant_string(name: str, precision: int) -> str:
    digits = _fixed_precision(precision)
    value = str(_fixed_constant_digits(name, digits))
    if digits == 0:
        return value
    value = value.zfill(digits + 1)
    return value[:-digits] + "." + value[-digits:]


_MATH_FUNCTIONS: dict[str, Callable[..., int | float | complex | str]] = {
    "abs": abs,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "rag_range": _roll_die,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "exp": math.exp,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "floor": math.floor,
    "ceil": math.ceil,
    "pow": math.pow,
    "fmod": math.fmod,
    "hypot": math.hypot,
    "erf": math.erf,
    "erfc": math.erfc,
    "gamma": math.gamma,
    "lgamma": math.lgamma,
    "beta": lambda a, b: math.exp(
        math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    ),
    "zeta": lambda s: _riemann_zeta(s),
    "pi_digits": lambda n: _fixed_constant_digits("pi", n),
    "pi_str": lambda n: _fixed_constant_string("pi", n),
    "e_digits": lambda n: _fixed_constant_digits("e", n),
    "e_str": lambda n: _fixed_constant_string("e", n),
    "sqrt2_digits": lambda n: _fixed_constant_digits("sqrt2", n),
    "sqrt2_str": lambda n: _fixed_constant_string("sqrt2", n),
    "real": lambda value: value.real if isinstance(value, complex) else value,
    "imag": lambda value: value.imag if isinstance(value, complex) else 0,
    "conj": complex.conjugate,
    "cabs": abs,
}


def _riemann_zeta(s: float) -> float:
    """Riemann zeta for real s > 1 via a direct series plus Euler-Maclaurin tail.

    The sample count and correction terms are fixed so the interpreter and the
    compiled backend evaluate the identical expression.
    """
    if not math.isfinite(s):
        raise ValueError("zeta requires a finite argument")
    if s <= 1.0:
        raise ValueError("zeta currently supports real arguments greater than 1")
    samples = 1000
    total = 0.0
    for n in range(1, samples + 1):
        total += math.pow(float(n), -s)
    n = float(samples)
    total += math.pow(n, 1.0 - s) / (s - 1.0)
    total -= 0.5 * math.pow(n, -s)
    total += (s / 12.0) * math.pow(n, -s - 1.0)
    total -= (s * (s + 1.0) * (s + 2.0) / 720.0) * math.pow(n, -s - 3.0)
    return total


def _matrix_from_node(
    node: ast.expr,
    values: dict[str, MathValue],
    *,
    complex_mode: bool,
    functions: dict[str, tuple[tuple[str, ...], str, int]] | None,
) -> Matrix:
    """Read a matrix argument as a shape-tagged value.

    Accepted shapes are a value already known to be a matrix, a list of
    equally long row lists, or a flat list whose length is a perfect square
    (interpreted as a square matrix).
    """
    if isinstance(node, ast.Name):
        known = values.get(node.id)
        if isinstance(known, Matrix):
            return known
        if isinstance(known, list):
            raise ValueError("value is not a matrix")
    known_result = _eval_matrix_root(
        node, values, complex_mode=complex_mode, functions=functions
    )
    if known_result is not None:
        return known_result
    flat = _eval_array_literal(node, values, complex_mode=complex_mode, functions=functions)
    if (
        isinstance(node, ast.List)
        and node.elts
        and all(isinstance(element, ast.List) for element in node.elts)
    ):
        rows = [len(element.elts) for element in node.elts]
        if len(set(rows)) != 1:
            raise ValueError("matrix rows must all have the same length")
        width = rows[0]
        if width == 0:
            raise ValueError("matrix rows must not be empty")
        return Matrix(len(rows), width, list(flat))
    size = len(flat)
    side = int(round(math.sqrt(size)))
    if side * side != size:
        raise ValueError("matrix must be a list of rows or a flat square list")
    return Matrix(side, side, list(flat))


def _matrix_validate(matrix: Matrix) -> None:
    if matrix.rows <= 0 or matrix.columns <= 0:
        raise ValueError("matrix must have at least one row and one column")
    if len(matrix.data) != matrix.rows * matrix.columns:
        raise ValueError("matrix has an inconsistent number of elements")


def _eval_matrix_root(
    node: ast.expr,
    values: dict[str, MathValue],
    *,
    complex_mode: bool,
    functions: dict[str, tuple[tuple[str, ...], str, int]] | None,
) -> Matrix | None:
    """Return the matrix value of a matrix-producing expression, else None."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        function_name = node.func.id.lower()
        if function_name in {
            "matrix_mul", "matrix_transpose", "matrix2_inverse", "matrix3_inverse",
        }:
            result = _eval_matrix_builtin(
                function_name,
                node,
                values,
                complex_mode=complex_mode,
                functions=functions,
            )
            if not isinstance(result, Matrix):
                raise ValueError(f"{function_name} did not produce a matrix")
            return result
    return None


def _eval_numeric_stats_builtin(
    function_name: str,
    node: ast.Call,
    values: dict[str, MathValue],
    *,
    complex_mode: bool,
    functions: dict[str, tuple[tuple[str, ...], str, int]] | None,
    call_depth: int = 0,
    bitwise_allowed: bool = False,
    uint32: bool = False,
) -> float:
    def real_array(position: int, label: str) -> list[float]:
        array = _eval_array_literal(
            node.args[position], values, complex_mode=complex_mode, functions=functions
        )
        if not array:
            raise ValueError(f"{function_name} requires a non-empty {label}")
        if any(isinstance(value, complex) for value in array):
            raise ValueError(f"{function_name} currently supports real-valued arrays only")
        return [float(value) for value in array]

    def numeric_argument(position: int, label: str) -> float:
        raw = _eval_numeric_node(
            node.args[position],
            values,
            complex_mode=complex_mode,
            functions=functions,
            call_depth=call_depth,
            bitwise_allowed=bitwise_allowed,
            uint32=uint32,
        )
        if isinstance(raw, (str, complex, Matrix, list)) or not math.isfinite(raw):
            raise ValueError(f"{function_name} {label} must be a finite number")
        return float(raw)

    if function_name in {"median", "variance"}:
        if len(node.args) != 1 or node.keywords:
            raise ValueError(f"{function_name} expects one array")
        data = real_array(0, "array")
        if function_name == "median":
            ordered = sorted(data)
            middle = len(ordered) // 2
            if len(ordered) % 2:
                return ordered[middle]
            return (ordered[middle - 1] + ordered[middle]) / 2.0
        average = sum(data) / len(data)
        return sum((value - average) ** 2 for value in data) / len(data)

    if function_name == "correlation":
        if len(node.args) != 2 or node.keywords:
            raise ValueError("correlation expects two arrays")
        left = real_array(0, "array")
        right = real_array(1, "array")
        if len(left) != len(right):
            raise ValueError("correlation arrays must have equal lengths")
        left_mean = sum(left) / len(left)
        right_mean = sum(right) / len(right)
        numerator = sum(
            (a - left_mean) * (b - right_mean) for a, b in zip(left, right)
        )
        left_variance = sum((a - left_mean) ** 2 for a in left)
        right_variance = sum((b - right_mean) ** 2 for b in right)
        if left_variance == 0.0 or right_variance == 0.0:
            raise ValueError("correlation is undefined for a constant array")
        return numerator / math.sqrt(left_variance * right_variance)

    if function_name == "percentile":
        if len(node.args) != 2 or node.keywords:
            raise ValueError("percentile expects an array and a percentage")
        data = real_array(0, "array")
        percentage = numeric_argument(1, "percentage")
        if not 0.0 <= percentage <= 100.0:
            raise ValueError("percentile percentage must be between 0 and 100")
        ordered = sorted(data)
        if len(ordered) == 1:
            return ordered[0]
        position = (len(ordered) - 1) * percentage / 100.0
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return ordered[lower]
        weight = position - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

    data = real_array(0, "array")
    step = numeric_argument(1, "step")
    if step <= 0.0:
        raise ValueError(f"{function_name} step must be positive")
    if function_name == "trapz":
        if len(data) < 2:
            raise ValueError("trapz requires at least two samples")
        total = (data[0] + data[-1]) / 2.0
        total += sum(data[1:-1])
        return total * step
    if len(data) < 3 or len(data) % 2 == 0:
        raise ValueError("simpson requires an odd number of samples (at least three)")
    total = data[0] + data[-1]
    total += 4.0 * sum(data[1:-1:2])
    total += 2.0 * sum(data[2:-1:2])
    return total * step / 3.0


def _solve_linear(matrix: Matrix, vector: list[float | complex]) -> list[float]:
    """Gauss-Jordan elimination with partial pivoting on the augmented system."""
    order = matrix.rows
    if matrix.columns != order:
        raise ValueError("solve_linear requires a square matrix")
    if len(vector) != order:
        raise ValueError("solve_linear requires a right-hand side with one value per row")
    if any(isinstance(value, complex) for value in matrix.data + vector):
        raise ValueError("solve_linear supports real-valued systems only")
    augmented = [
        [float(matrix.at(row, column)) for column in range(order)] + [float(vector[row])]
        for row in range(order)
    ]
    for pivot in range(order):
        best = max(range(pivot, order), key=lambda row: abs(augmented[row][pivot]))
        if abs(augmented[best][pivot]) < 1e-12:
            raise ValueError("matrix is singular and cannot be solved")
        augmented[pivot], augmented[best] = augmented[best], augmented[pivot]
        scale = augmented[pivot][pivot]
        augmented[pivot] = [value / scale for value in augmented[pivot]]
        for row in range(order):
            if row == pivot:
                continue
            factor = augmented[row][pivot]
            if factor == 0.0:
                continue
            augmented[row] = [
                value - factor * base
                for value, base in zip(augmented[row], augmented[pivot])
            ]
    return [augmented[row][order] for row in range(order)]


def _eval_matrix_builtin(
    function_name: str,
    node: ast.Call,
    values: dict[str, MathValue],
    *,
    complex_mode: bool,
    functions: dict[str, tuple[tuple[str, ...], str, int]] | None,
    call_depth: int = 0,
    bitwise_allowed: bool = False,
    uint32: bool = False,
) -> MathValue:
    def integer_argument(position: int, label: str) -> int:
        raw = _eval_numeric_node(
            node.args[position],
            values,
            complex_mode=complex_mode,
            functions=functions,
            call_depth=call_depth,
            bitwise_allowed=bitwise_allowed,
            uint32=uint32,
        )
        if isinstance(raw, (str, complex, Matrix, list)) or not math.isfinite(raw) or not float(raw).is_integer():
            raise ValueError(f"matrix {label} must be an integer")
        return int(raw)

    def matrix_argument(position: int) -> Matrix:
        matrix = _matrix_from_node(
            node.args[position],
            values,
            complex_mode=complex_mode,
            functions=functions,
        )
        _matrix_validate(matrix)
        return matrix

    if function_name in {"matrix_rows", "matrix_cols"}:
        if len(node.args) != 1 or node.keywords:
            raise ValueError(f"{function_name} expects one matrix")
        matrix = matrix_argument(0)
        return matrix.rows if function_name == "matrix_rows" else matrix.columns

    if function_name == "matrix_at":
        if len(node.args) != 3 or node.keywords:
            raise ValueError("matrix_at expects a matrix, a row, and a column")
        matrix = matrix_argument(0)
        row = integer_argument(1, "row")
        column = integer_argument(2, "column")
        if not 0 <= row < matrix.rows or not 0 <= column < matrix.columns:
            raise ValueError("matrix index is outside the matrix")
        return matrix.at(row, column)

    if function_name == "matrix_mul":
        if len(node.args) != 2 or node.keywords:
            raise ValueError("matrix_mul expects two matrices")
        left = matrix_argument(0)
        right = matrix_argument(1)
        if left.columns != right.rows:
            raise ValueError("matrix_mul inner dimensions do not match")
        result: list[float | complex] = []
        for row in range(left.rows):
            for column in range(right.columns):
                total: float | complex = 0
                for k in range(left.columns):
                    total += left.at(row, k) * right.at(k, column)
                result.append(total)
        return Matrix(left.rows, right.columns, result)

    if function_name == "matrix_transpose":
        if len(node.args) != 1 or node.keywords:
            raise ValueError("matrix_transpose expects one matrix")
        matrix = matrix_argument(0)
        return Matrix(
            matrix.columns,
            matrix.rows,
            [
                matrix.at(row, column)
                for column in range(matrix.columns)
                for row in range(matrix.rows)
            ],
        )

    if function_name in {"matrix2_inverse", "matrix3_inverse"}:
        if len(node.args) != 1 or node.keywords:
            raise ValueError(f"{function_name} expects one matrix")
        order = 2 if function_name == "matrix2_inverse" else 3
        matrix = matrix_argument(0)
        if matrix.rows != order or matrix.columns != order:
            raise ValueError(f"{function_name} requires a {order} by {order} matrix")
        if complex_mode or any(isinstance(value, complex) for value in matrix.data):
            raise ValueError(f"{function_name} supports real-valued matrices only")
        return Matrix(order, order, _matrix_inverse([float(value) for value in matrix.data], order))

    if function_name == "solve_linear":
        if len(node.args) != 2 or node.keywords:
            raise ValueError("solve_linear expects a square matrix and a right-hand side")
        matrix = matrix_argument(0)
        vector = _eval_array_literal(
            node.args[1], values, complex_mode=complex_mode, functions=functions
        )
        return _solve_linear(matrix, vector)

    raise ValueError(f"unsupported matrix function {function_name}")


def _matrix_inverse(data: list[float], order: int) -> list[float]:
    """Gauss-Jordan inversion with partial pivoting."""
    augmented = [
        [data[row * order + column] for column in range(order)]
        + [1.0 if row == column else 0.0 for column in range(order)]
        for row in range(order)
    ]
    for pivot in range(order):
        best = max(range(pivot, order), key=lambda row: abs(augmented[row][pivot]))
        if abs(augmented[best][pivot]) < 1e-12:
            raise ValueError("matrix is singular and cannot be inverted")
        augmented[pivot], augmented[best] = augmented[best], augmented[pivot]
        scale = augmented[pivot][pivot]
        augmented[pivot] = [value / scale for value in augmented[pivot]]
        for row in range(order):
            if row == pivot:
                continue
            factor = augmented[row][pivot]
            if factor == 0.0:
                continue
            augmented[row] = [
                value - factor * base
                for value, base in zip(augmented[row], augmented[pivot])
            ]
    return [
        augmented[row][order + column]
        for row in range(order)
        for column in range(order)
    ]


def _eval_numeric_node(
    node: ast.expr,
    values: dict[str, MathValue],
    *,
    complex_mode: bool = False,
    functions: dict[str, tuple[tuple[str, ...], str, int]] | None = None,
    call_depth: int = 0,
    error_count: int = 0,
    bitwise_allowed: bool = False,
    uint32: bool = False,
) -> MathValue:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in values and isinstance(values[node.id], list):
            return values[node.id]
        constants = {"pi": math.pi, "e": math.e, "tau": math.tau}
        if node.id.lower() in constants:
            return constants[node.id.lower()]
        if node.id in values:
            return values[node.id]
        raise ValueError(f"unknown numeric value '{node.id}'")
    if isinstance(node, ast.BinOp):
        left = _eval_numeric_node(node.left, values, complex_mode=complex_mode, functions=functions, call_depth=call_depth, bitwise_allowed=bitwise_allowed, uint32=uint32)
        right = _eval_numeric_node(node.right, values, complex_mode=complex_mode, functions=functions, call_depth=call_depth, bitwise_allowed=bitwise_allowed, uint32=uint32)
        if isinstance(left, (str, list)) or isinstance(right, (str, list)):
            raise ValueError("arithmetic operators require numeric values")
        if isinstance(node.op, ast.Add):
            result = left + right
        elif isinstance(node.op, ast.Sub):
            result = left - right
        elif isinstance(node.op, ast.Mult):
            result = left * right
        elif isinstance(node.op, ast.Div):
            result = left / right
        elif isinstance(node.op, ast.FloorDiv):
            result = left // right
        elif isinstance(node.op, ast.Mod):
            result = left % right
        elif isinstance(node.op, ast.Pow):
            if abs(right) > 10000:
                raise ValueError("exponent is too large")
            if isinstance(left, int) and isinstance(right, int):
                if max(1, left.bit_length()) * max(1, abs(right)) > 1_000_000:
                    raise ValueError("integer result is too large")
            result = left**right
        elif isinstance(
            node.op,
            (ast.BitAnd, ast.BitOr, ast.BitXor, ast.LShift, ast.RShift),
        ):
            if complex_mode or not bitwise_allowed:
                raise ValueError(
                    "bitwise operators require an integer math mode "
                    "(int32, int64, uint32, or bigint)"
                )
            if type(left) is not int or type(right) is not int:
                raise ValueError(
                    "bitwise operators require integer operands; use an integer math mode"
                )
            if isinstance(node.op, ast.BitAnd):
                result = left & right
            elif isinstance(node.op, ast.BitOr):
                result = left | right
            elif isinstance(node.op, ast.BitXor):
                result = left ^ right
            else:
                if right < 0:
                    raise ValueError("shift count must not be negative")
                if right > 1_000_000:
                    raise ValueError("shift count is too large")
                if isinstance(node.op, ast.LShift):
                    if max(1, left.bit_length()) + right > 1_000_000:
                        raise ValueError("integer result is too large")
                    result = left << right
                elif uint32:
                    result = (left & 0xFFFFFFFF) >> right
                else:
                    result = left >> right
        else:
            raise ValueError("unsupported arithmetic operator")
        if uint32 and isinstance(result, int):
            result &= 0xFFFFFFFF
        if isinstance(result, int) and result.bit_length() > 1_000_000:
            raise ValueError("integer result is too large")
        return result
    if isinstance(node, ast.UnaryOp):
        value = _eval_numeric_node(node.operand, values, complex_mode=complex_mode, functions=functions, call_depth=call_depth, bitwise_allowed=bitwise_allowed, uint32=uint32)
        if isinstance(value, (str, list)):
            raise ValueError("unary arithmetic operators require numeric values")
        if isinstance(node.op, ast.UAdd):
            return value
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.Invert):
            if complex_mode or not bitwise_allowed:
                raise ValueError(
                    "bitwise operators require an integer math mode "
                    "(int32, int64, uint32, or bigint)"
                )
            if type(value) is not int:
                raise ValueError(
                    "bitwise operators require integer operands; use an integer math mode"
                )
            result = ~value
            return (result & 0xFFFFFFFF) if uint32 else result
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        function_name = node.func.id.lower()
        if function_name in {"array_len", "mean", "std", "min", "max"}:
            if len(node.args) != 1 or node.keywords:
                raise ValueError(f"{function_name} expects one array")
            array = _eval_array_literal(
                node.args[0], values, complex_mode=complex_mode, functions=functions
            )
            if function_name == "array_len":
                return len(array)
            if not array:
                raise ValueError(f"{function_name} requires a non-empty array")
            if any(isinstance(value, complex) for value in array):
                raise ValueError(f"{function_name} currently supports real-valued arrays only")
            real_values = [float(value) for value in array]
            if function_name == "mean":
                return sum(real_values) / len(real_values)
            if function_name == "std":
                average = sum(real_values) / len(real_values)
                return math.sqrt(
                    sum((value - average) ** 2 for value in real_values) / len(real_values)
                )
            return min(real_values) if function_name == "min" else max(real_values)
        if function_name in {
            "median", "variance", "correlation", "percentile", "trapz", "simpson",
        }:
            return _eval_numeric_stats_builtin(
                function_name,
                node,
                values,
                complex_mode=complex_mode,
                functions=functions,
                call_depth=call_depth,
                bitwise_allowed=bitwise_allowed,
                uint32=uint32,
            )
        if function_name in {"array_set", "array_push"}:
            return _eval_array_literal(
                node, values, complex_mode=complex_mode, functions=functions
            )
        if function_name == "array_sum":
            if len(node.args) != 1 or node.keywords:
                raise ValueError("array_sum expects one array")
            return sum(_eval_array_literal(node.args[0], values, complex_mode=complex_mode, functions=functions))
        if function_name == "array_at":
            if len(node.args) != 2 or node.keywords:
                raise ValueError("array_at expects an array and an index")
            array = _eval_array_literal(node.args[0], values, complex_mode=complex_mode, functions=functions)
            index = _eval_numeric_node(node.args[1], values, complex_mode=complex_mode, functions=functions, call_depth=call_depth, bitwise_allowed=bitwise_allowed, uint32=uint32)
            if isinstance(index, (str, complex)) or not float(index).is_integer():
                raise ValueError("array index must be an integer")
            position = int(index)
            if not 0 <= position < len(array):
                raise ValueError("array index is outside the array")
            return array[position]
        if function_name == "dot":
            if len(node.args) != 2 or node.keywords:
                raise ValueError("dot expects two arrays")
            left = _eval_array_literal(node.args[0], values, complex_mode=complex_mode, functions=functions)
            right = _eval_array_literal(node.args[1], values, complex_mode=complex_mode, functions=functions)
            if len(left) != len(right):
                raise ValueError("dot arrays must have equal lengths")
            return sum(a * b for a, b in zip(left, right))
        if function_name == "blas_dot":
            if len(node.args) != 2 or node.keywords:
                raise ValueError("blas_dot expects two arrays")
            left = _eval_array_literal(node.args[0], values, complex_mode=complex_mode, functions=functions)
            right = _eval_array_literal(node.args[1], values, complex_mode=complex_mode, functions=functions)
            if complex_mode or any(isinstance(value, complex) for value in left + right):
                raise ValueError("blas_dot currently supports real-valued arrays only")
            if len(left) != len(right):
                raise ValueError("dot arrays must have equal lengths")
            return sum(a * b for a, b in zip(left, right))
        if function_name in {"matrix2_det", "matrix2_mul_entry", "lapack_determinant2"}:
            expected = 4 if function_name == "matrix2_mul_entry" else 1
            if len(node.args) != expected or node.keywords:
                raise ValueError(f"{function_name} expects {expected} argument(s)")
            matrices = [
                _eval_array_literal(row, values, complex_mode=complex_mode, functions=functions)
                for row in node.args[0].elts
            ] if isinstance(node.args[0], ast.List) else []
            if len(matrices) != 2 or any(len(row) != 2 for row in matrices):
                raise ValueError(f"{function_name} requires a 2 by 2 matrix literal")
            if function_name in {"matrix2_det", "lapack_determinant2"}:
                if function_name == "lapack_determinant2" and (
                    complex_mode
                    or any(isinstance(value, complex) for row in matrices for value in row)
                ):
                    raise ValueError("lapack_determinant2 currently supports real-valued matrices only")
                return matrices[0][0] * matrices[1][1] - matrices[0][1] * matrices[1][0]
            right_matrix = [
                _eval_array_literal(row, values, complex_mode=complex_mode, functions=functions)
                for row in node.args[1].elts
            ] if isinstance(node.args[1], ast.List) else []
            if len(right_matrix) != 2 or any(len(row) != 2 for row in right_matrix):
                raise ValueError("matrix2_mul_entry requires two 2 by 2 matrix literals")
            indices = [
                _eval_numeric_node(argument, values, complex_mode=complex_mode, functions=functions, call_depth=call_depth, bitwise_allowed=bitwise_allowed, uint32=uint32)
                for argument in node.args[2:]
            ]
            if any(
                isinstance(index, (str, complex)) or not float(index).is_integer()
                for index in indices
            ):
                raise ValueError("matrix row and column indices must be integers")
            row, column = (int(index) for index in indices)
            if row not in (0, 1) or column not in (0, 1):
                raise ValueError("matrix row or column is outside the 2 by 2 matrix")
            return sum(matrices[row][k] * right_matrix[k][column] for k in range(2))
        if function_name in {
            "matrix_rows", "matrix_cols", "matrix_at", "matrix_mul",
            "matrix_transpose", "matrix2_inverse", "matrix3_inverse",
        }:
            return _eval_matrix_builtin(
                function_name,
                node,
                values,
                complex_mode=complex_mode,
                functions=functions,
                call_depth=call_depth,
                bitwise_allowed=bitwise_allowed,
                uint32=uint32,
            )
        if function_name == "read_text":
            if len(node.args) != 1 or node.keywords:
                raise ValueError("read_text expects a file path")
            path = _eval_numeric_node(node.args[0], values, complex_mode=complex_mode, functions=functions, call_depth=call_depth, bitwise_allowed=bitwise_allowed, uint32=uint32)
            if not isinstance(path, str):
                raise ValueError("read_text path must be a string")
            try:
                with open(path, "rb") as file:
                    raw = file.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise ValueError("text file exceeds the 16 MiB limit")
                text = raw.decode("utf-8")
            except (OSError, UnicodeError) as error:
                raise ValueError(f"cannot read UTF-8 text file: {error}") from error
            if "\0" in text:
                raise ValueError("text file contains an unsupported NUL character")
            return text
        if function_name == "write_text":
            if len(node.args) != 2 or node.keywords:
                raise ValueError("write_text expects a path and text")
            path, text = (
                _eval_numeric_node(argument, values, complex_mode=complex_mode, functions=functions, call_depth=call_depth, bitwise_allowed=bitwise_allowed, uint32=uint32)
                for argument in node.args
            )
            if not isinstance(path, str) or not isinstance(text, str):
                raise ValueError("write_text expects string arguments")
            if "\0" in path or "\0" in text:
                raise ValueError("write_text does not support NUL characters")
            try:
                with open(path, "w", encoding="utf-8", newline="") as file:
                    return file.write(text)
            except (OSError, UnicodeError) as error:
                raise ValueError(f"cannot write UTF-8 text file: {error}") from error
        if function_name == "complex":
            if not 1 <= len(node.args) <= 2 or node.keywords:
                raise ValueError("complex expects one or two numeric arguments")
            arguments = [
                _eval_numeric_node(argument, values, complex_mode=complex_mode, functions=functions, call_depth=call_depth, bitwise_allowed=bitwise_allowed, uint32=uint32)
                for argument in node.args
            ]
            if any(isinstance(argument, (str, list)) for argument in arguments):
                raise ValueError("complex expects numeric arguments")
            return complex(*arguments)
        if function_name == "err_count":
            if node.args or node.keywords:
                raise ValueError("err_count takes no arguments")
            return error_count
        if function_name in {"bytes_len", "bytes_get", "bytes_set"}:
            if function_name == "bytes_len":
                if len(node.args) != 1 or node.keywords:
                    raise ValueError("bytes_len expects one bytes array")
                array = _eval_array_literal(node.args[0], values, complex_mode=False, functions=functions)
                return len(array)
            if function_name == "bytes_get":
                if len(node.args) != 2 or node.keywords:
                    raise ValueError("bytes_get expects an array and an index")
                array = _eval_array_literal(node.args[0], values, complex_mode=False, functions=functions)
                index = _eval_numeric_node(node.args[1], values, functions=functions, call_depth=call_depth)
                if not isinstance(index, int) or not 0 <= index < len(array):
                    raise ValueError("bytes index is outside the array")
                return int(array[index]) & 0xFF
            if len(node.args) != 3 or node.keywords:
                raise ValueError("bytes_set expects an array, an index, and a value")
            array = _eval_array_literal(node.args[0], values, complex_mode=False, functions=functions)
            index = _eval_numeric_node(node.args[1], values, functions=functions, call_depth=call_depth)
            new_value = _eval_numeric_node(node.args[2], values, functions=functions, call_depth=call_depth)
            if not isinstance(index, int) or not 0 <= index < len(array):
                raise ValueError("bytes index is outside the array")
            if not isinstance(new_value, int) or not 0 <= new_value <= 255:
                raise ValueError("bytes values must be integers in 0..255")
            array[index] = float(new_value)
            return new_value
        if function_name == "hex":
            if node.keywords or len(node.args) not in {1, 2}:
                raise ValueError("hex expects a value and an optional width")
            value = _eval_numeric_node(node.args[0], values, complex_mode=complex_mode, functions=functions, call_depth=call_depth)
            if isinstance(value, (str, list, complex)) or type(value) is not int:
                raise ValueError("hex expects an integer value")
            digits = format(value, "x")
            if len(node.args) == 1:
                return digits
            width_value = _eval_numeric_node(node.args[1], values, complex_mode=complex_mode, functions=functions, call_depth=call_depth)
            if isinstance(width_value, (str, list, complex)) or not float(width_value).is_integer():
                raise ValueError("hex width must be an integer")
            width = int(width_value)
            if not 1 <= width <= 1_000_000:
                raise ValueError("hex width must be between 1 and 1000000")
            negative = digits.startswith("-")
            body = digits[1:] if negative else digits
            if len(body) > width:
                raise ValueError("hex value does not fit the requested width")
            padded = body.rjust(width, "0")
            return ("-" + padded) if negative else padded
        if function_name == "unhex":
            if node.keywords or len(node.args) != 1:
                raise ValueError("unhex expects exactly one hexadecimal string")
            text = _eval_numeric_node(node.args[0], values, complex_mode=complex_mode, functions=functions, call_depth=call_depth)
            if not isinstance(text, str):
                raise ValueError("unhex expects a hexadecimal string")
            body = text.strip()
            negative = body.startswith("-")
            if negative:
                body = body[1:]
            if not body or not re.fullmatch(r"[0-9a-fA-F]+", body):
                raise ValueError("unhex requires hexadecimal digits")
            if len(body) * 4 > 1_000_000:
                raise ValueError("integer result is too large")
            value = int(body, 16)
            return -value if negative else value
        if function_name == "chr":
            if len(node.args) != 1 or node.keywords:
                raise ValueError("chr expects exactly one integer code point")
            codepoint = _eval_numeric_node(node.args[0], values, complex_mode=complex_mode, functions=functions, call_depth=call_depth, bitwise_allowed=bitwise_allowed, uint32=uint32)
            if isinstance(codepoint, str):
                raise ValueError("chr expects an integer Unicode code point")
            return _unicode_character(float(codepoint))
        if function_name == "ord":
            if len(node.args) != 1 or node.keywords:
                raise ValueError("ord expects exactly one Unicode character")
            character = _eval_numeric_node(node.args[0], values, complex_mode=complex_mode, functions=functions, call_depth=call_depth, bitwise_allowed=bitwise_allowed, uint32=uint32)
            if not isinstance(character, str):
                raise ValueError("ord expects a string containing one Unicode character")
            return _unicode_codepoint(character)
        if function_name == "strlen":
            if len(node.args) != 1 or node.keywords:
                raise ValueError("strlen expects exactly one string")
            value = _eval_numeric_node(node.args[0], values, complex_mode=complex_mode, functions=functions, call_depth=call_depth, bitwise_allowed=bitwise_allowed, uint32=uint32)
            if not isinstance(value, str):
                raise ValueError("strlen expects a string")
            return _unicode_length(value)
        if function_name == "char_at":
            if len(node.args) != 2 or node.keywords:
                raise ValueError("char_at expects a string and an index")
            value = _eval_numeric_node(node.args[0], values, complex_mode=complex_mode, functions=functions, call_depth=call_depth, bitwise_allowed=bitwise_allowed, uint32=uint32)
            index = _eval_numeric_node(node.args[1], values, complex_mode=complex_mode, functions=functions, call_depth=call_depth, bitwise_allowed=bitwise_allowed, uint32=uint32)
            if not isinstance(value, str) or isinstance(index, str):
                raise ValueError("char_at expects a string and a numeric index")
            return _unicode_at(value, float(index))
        if function_name == "choose":
            if len(node.args) != 3 or node.keywords:
                raise ValueError("choose expects a condition and two values")
            branch = node.args[1] if _condition_value(node.args[0], values, functions=functions) else node.args[2]
            return _eval_numeric_node(branch, values, complex_mode=complex_mode, functions=functions, call_depth=call_depth, bitwise_allowed=bitwise_allowed, uint32=uint32)
        if functions and function_name in functions:
            if node.keywords:
                raise ValueError("user-defined functions do not accept keyword arguments")
            parameters, expression, definition_line = functions[function_name]
            if len(node.args) != len(parameters):
                raise ValueError(f"{function_name} expects {len(parameters)} argument(s)")
            if call_depth >= 256:
                raise ValueError("function recursion depth exceeded")
            arguments = [
                _eval_numeric_node(
                    argument,
                    values,
                    complex_mode=complex_mode,
                    functions=functions,
                    call_depth=call_depth,
                )
                for argument in node.args
            ]
            local_values = dict(values)
            local_values.update(zip(parameters, arguments))
            transformed, _variables = transform_braced_expression(expression)
            try:
                function_node = ast.parse(transformed, mode="eval").body
            except SyntaxError as error:
                raise ValueError(f"invalid expression in function declared at line {definition_line}") from error
            if isinstance(function_node, (ast.Compare, ast.BoolOp)):
                return _condition_value(
                    function_node,
                    local_values,
                    functions=functions,
                )
            return _eval_numeric_node(
                function_node,
                local_values,
                complex_mode=complex_mode,
                functions=functions,
                call_depth=call_depth + 1,
                bitwise_allowed=bitwise_allowed, uint32=uint32,
            )
        complex_functions = {
                "sqrt": cmath.sqrt,
                "sin": cmath.sin,
                "cos": cmath.cos,
                "tan": cmath.tan,
                "asin": cmath.asin,
                "acos": cmath.acos,
                "atan": cmath.atan,
                "sinh": cmath.sinh,
                "cosh": cmath.cosh,
                "tanh": cmath.tanh,
                "exp": cmath.exp,
                "log": cmath.log,
                "log10": cmath.log10,
                "log2": lambda value: cmath.log(value, 2),
                "pow": pow,
            }
        function = _MATH_FUNCTIONS.get(function_name)
        if complex_mode and function_name in complex_functions:
            function = complex_functions[function_name]
        if function is None or node.keywords:
            raise ValueError(f"unsupported scientific function '{node.func.id}'")
        arguments = [
            _eval_numeric_node(argument, values, complex_mode=complex_mode, functions=functions, call_depth=call_depth, bitwise_allowed=bitwise_allowed, uint32=uint32)
            for argument in node.args
        ]
        if any(isinstance(argument, (str, list)) for argument in arguments):
            raise ValueError(f"{node.func.id} expects numeric arguments")
        return function(*arguments)
    raise ValueError("expression contains unsupported syntax")


def _numeric_values(
    expression: str,
    context: Context,
    line: int,
) -> tuple[str, dict[str, MathValue]]:
    transformed, _variables = transform_braced_expression(expression)
    transformed = re.sub(r"tape\s*\(\s*([A-Za-z_]\w*)\s*\)", r"tape_\1", transformed, flags=re.I)
    transformed = normalize_rag_calls(transformed)
    tree = ast.parse(transformed, mode="eval")
    string_variables = {
        argument.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id.lower() in {"ord", "strlen", "char_at", "read_text", "write_text"}
        and node.args
        for argument in (
            node.args if node.func.id.lower() == "write_text" else (node.args[0],)
        )
        if isinstance(argument, ast.Name) and argument.id.startswith("var_")
    }
    values: dict[str, MathValue] = {}
    for array_name, array_values in context.arrays.items():
        values[array_name] = array_values
    for matrix_name, matrix_value in context.matrix_arrays.items():
        values[matrix_name] = matrix_value
    for token in set(re.findall(r"\b(?:var_[A-Za-z_]\w*|tape_[A-Za-z_]\w*)\b", transformed)):
        if token.startswith("var_"):
            name = token[4:]
            variable = context.variables.get(name)
            if variable is None or variable.killed:
                raise ValueError(f"variable '{name}' is not available")
            if variable.matrix_value is not None:
                values[token] = variable.matrix_value
                continue
            if variable.array_value is not None:
                values[token] = variable.array_value
                continue
            text = variable.text()
            if text is None:
                raise ValueError(f"variable '{name}' is empty")
            if token in string_variables:
                values[token] = text
            elif re.fullmatch(r"[+-]?\d+", text):
                values[token] = int(text)
            elif re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[+-](?:\d+(?:\.\d*)?|\.\d+))?i", text):
                values[token] = complex(text[:-1] + "j")
            else:
                try:
                    values[token] = float(text)
                except ValueError:
                    values[token] = text
        else:
            name = token[5:]
            tape = context.tapes.get(name)
            if tape is None:
                raise ValueError(f"tape '{name}' is not declared")
            values[token] = tape.read()
    return transformed, values


def _evaluate_expression(
    expression: str,
    context: Context,
    line: int,
    *,
    math_mode: str = "float64",
    functions: dict[str, tuple[tuple[str, ...], str, int]] | None = None,
    error_count: int = 0,
) -> int | float | complex | str:
    try:
        transformed, values = _numeric_values(expression, context, line)
        transformed = normalize_rag_calls(transformed)
        if len(transformed) > 512:
            raise ValueError("math expression is too long")
        tree = ast.parse(transformed, mode="eval")
        if (
            isinstance(tree.body, ast.List)
            or isinstance(tree.body, ast.Name) and isinstance(values.get(tree.body.id), list)
            or isinstance(tree.body, ast.Call)
            and isinstance(tree.body.func, ast.Name)
            and tree.body.func.id.lower() in {
                "array_set", "array_push", "matrix_mul", "matrix_transpose",
                "matrix2_inverse", "matrix3_inverse",
            }
        ):
            raise ValueError(
                "array values cannot be used as scalar math_run results; pass the array to an array function"
            )
        value = _eval_numeric_node(
            tree.body,
            values,
            complex_mode=math_mode == "complex",
            functions=functions,
            error_count=error_count,
            bitwise_allowed=math_mode in {"int32", "int64", "uint32", "bigint"},
            uint32=math_mode == "uint32",
        )
        if isinstance(value, Matrix):
            raise ValueError("matrix values must be passed to a matrix function")
        if isinstance(value, list):
            raise ValueError("array values must be passed to an array function")
        if isinstance(value, complex) and math_mode != "complex":
            raise ValueError("complex values require complex math mode")
        if not isinstance(value, str):
            if math_mode == "complex" and isinstance(value, (int, float)):
                value = complex(value)
            finite = (
                math.isfinite(value.real) and math.isfinite(value.imag)
                if isinstance(value, complex)
                else True if isinstance(value, int) else math.isfinite(float(value))
            )
            if not finite:
                raise ValueError("numeric result is not finite")
        if not isinstance(value, (str, complex)) and math_mode in {"int32", "int64"}:
            if not float(value).is_integer():
                raise ValueError(f"{math_mode} mode requires an integer result")
            numeric = int(value)
            bits = 32 if math_mode == "int32" else 64
            minimum, maximum = -(2 ** (bits - 1)), 2 ** (bits - 1) - 1
            if not minimum <= numeric <= maximum:
                raise ValueError(f"result overflows {math_mode}")
            value = numeric
        if math_mode == "uint32" and not isinstance(value, (str, complex)):
            if not float(value).is_integer():
                raise ValueError("uint32 mode requires an integer result")
            value = int(value) & 0xFFFFFFFF
        if math_mode == "bigint":
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                raise ValueError("bigint mode requires integer-only expressions")
            if isinstance(value, int) and value.bit_length() > 1_000_000:
                raise ValueError("integer result is too large")
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError) as error:
        raise RuntimeError(line, f"invalid math expression: {error}") from error
    return value


def _format_numeric(value: int | float | complex | str, precision: int = 15) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, complex):
        return f"{format(value.real, f'.{precision}g')}{format(value.imag, f'+.{precision}g')}i"
    return format(value, f".{precision}g")


def _condition_value(
    node: ast.expr,
    values: dict[str, MathValue],
    *,
    functions: dict[str, tuple[tuple[str, ...], str, int]] | None = None,
) -> bool:
    if isinstance(node, ast.Compare):
        left = _eval_numeric_node(node.left, values, functions=functions)
        for operator_node, comparator in zip(node.ops, node.comparators):
            right = _eval_numeric_node(comparator, values, functions=functions)
            if isinstance(operator_node, ast.Eq):
                matched = left == right
            elif isinstance(operator_node, ast.NotEq):
                matched = left != right
            elif isinstance(operator_node, ast.Lt):
                matched = left < right
            elif isinstance(operator_node, ast.LtE):
                matched = left <= right
            elif isinstance(operator_node, ast.Gt):
                matched = left > right
            elif isinstance(operator_node, ast.GtE):
                matched = left >= right
            else:
                raise ValueError("unsupported comparison")
            if not matched:
                return False
            left = right
        return True
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_condition_value(value, values, functions=functions) for value in node.values)
        if isinstance(node.op, ast.Or):
            return any(_condition_value(value, values, functions=functions) for value in node.values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _condition_value(node.operand, values, functions=functions)
    return bool(_eval_numeric_node(node, values, functions=functions))


def _evaluate_condition(
    expression: str,
    context: Context,
    line: int,
    functions: dict[str, tuple[tuple[str, ...], str, int]] | None = None,
) -> bool:
    try:
        transformed, values = _numeric_values(expression, context, line)
        tree = ast.parse(transformed, mode="eval")
        return _condition_value(tree.body, values, functions=functions)
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError) as error:
        raise RuntimeError(line, f"invalid condition: {error}") from error


ExternalRunner = Callable[[str], tuple[str, int]]


class Interpreter:
    def __init__(
        self,
        *,
        allow_external_commands: bool = True,
        output: TextIO | None = None,
        error_output: TextIO | None = None,
        sleep: Callable[[float], None] = time.sleep,
        external_runner: ExternalRunner | None = None,
        max_loop_iterations: int | None = None,
    ) -> None:
        self.allow_external_commands = allow_external_commands
        self.output = output or sys.stdout
        self.error_output = error_output or sys.stderr
        self.sleep = sleep
        self.external_runner = external_runner or self._run_external
        self.max_loop_iterations = max_loop_iterations
        self.context = Context()
        self.last = Result()
        self.last_block_status: dict[str, str] = {}
        self.pending_inheritance = False
        self.warnings: list[str] = []
        self.math_mode = "float64"
        self.output_precision = 15
        self.functions: dict[str, tuple[tuple[str, ...], str, int]] = {}
        self.pending_jump_line: int | None = None
        self.error_count = 0
        self.dll_libraries: dict[str, object] = {}
        self.dll_states: dict[str, str] = {}
        self.extern_functions: dict[str, tuple[tuple[str, ...], str, str]] = {}
        self.atom_blocks: dict[str, Statement] = {}
        self.atom_father_blocks: set[str] = set()
        self.byte_arrays: set[str] = set()
        self.atom_runtime: dict[str, AtomBlock] = {}
        self.atom_chains: dict[str, tuple[str, ...]] = {}
        self.atom_lock = threading.RLock()
        self.output_lock = threading.Lock()
        self._atom_local = threading.local()

    def run(self, program: Program) -> Result:
        self.math_mode = program.math_mode
        self.output_precision = program.output_precision
        self.extern_functions = {
            name: (tuple(params), ret, symbol, conv)
            for name, params, ret, symbol, conv in program.extern_functions
        }
        for statement in program.statements:
            self._execute(statement, self.context, block_mode=None)
        return self.last

    def _execute(self, statement: Statement, context: Context, block_mode: str | None) -> None:
        kind = statement.kind
        if kind == "var_declare":
            name = str(statement.data["name"])
            existing = context.variables.get(name)
            if existing is not None and (
                existing.value is not None or existing.array_value is not None
            ):
                raise RuntimeError(statement.line, f"variable '{name}' is already declared")
            value = self._resolve_value(statement.data["value"], context, statement.line)
            encoding = existing.encoding if existing else None
            try:
                encoded = value.encode(encoding or "utf-8")
            except (LookupError, UnicodeEncodeError) as error:
                raise RuntimeError(statement.line, f"value cannot be stored: {error}") from error
            context.variables[name] = Variable(encoded, encoding)
            self.last = Result("ok", value)
            return
        if kind == "array_declare":
            name = str(statement.data["name"])
            try:
                transformed, values = _numeric_values(
                    str(statement.data["expression"]), context, statement.line
                )
                expression_tree = ast.parse(transformed, mode="eval")
                body = expression_tree.body
                is_array_expression = isinstance(body, ast.List) or (
                    isinstance(body, ast.Call)
                    and isinstance(body.func, ast.Name)
                    and body.func.id.lower()
                    in {"array_set", "array_push", "matrix_mul", "matrix_transpose",
                        "matrix2_inverse", "matrix3_inverse", "solve_linear"}
                )
                if not is_array_expression:
                    raise ValueError(
                        "array declaration requires a numeric list literal or array_set/array_push result"
                    )
                matrix_value = _eval_matrix_root(
                    body,
                    values,
                    complex_mode=self.math_mode == "complex",
                    functions=self.functions,
                )
                if matrix_value is None and (
                    isinstance(body, ast.List)
                    and body.elts
                    and all(isinstance(element, ast.List) for element in body.elts)
                ):
                    matrix_value = _matrix_from_node(
                        body,
                        values,
                        complex_mode=self.math_mode == "complex",
                        functions=self.functions,
                    )
                if matrix_value is not None:
                    if any(isinstance(value, complex) for value in matrix_value.data) and self.math_mode != "complex":
                        raise ValueError("complex matrix values require complex math mode")
                    context.matrix_arrays[name] = matrix_value.copy()
                    context.arrays.pop(name, None)
                    self.last = Result("ok", name)
                    return
                array = _eval_array_literal(
                    expression_tree.body,
                    values,
                    complex_mode=self.math_mode == "complex",
                    functions=self.functions,
                )
                if any(isinstance(value, complex) for value in array) and self.math_mode != "complex":
                    raise ValueError("complex array values require complex math mode")
                context.arrays[name] = [
                    value if isinstance(value, complex) else float(value)
                    for value in array
                ]
                context.matrix_arrays.pop(name, None)
            except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError) as error:
                raise RuntimeError(statement.line, f"invalid array declaration: {error}") from error
            self.last = Result("ok", name)
            return
        if kind in {"bytes_declare", "bytes_set", "bytes_get", "bytes_len"}:
            self._bytes_statement(statement, context)
            return
        if kind == "import":
            component = str(statement.data["component"])
            if component == "pe":
                warning = f"line {statement.line}: pe is recognized but not implemented"
                self.warnings.append(warning)
                print(f"warning: {warning}", file=self.error_output)
            else:
                context.imports.add(component)
            self.last = Result("ok", component)
            return
        if kind == "encoding":
            name, encoding = str(statement.data["name"]), str(statement.data["encoding"])
            try:
                "".encode(encoding)
            except LookupError as error:
                raise RuntimeError(statement.line, f"unknown encoding: {encoding}") from error
            variable = context.variables.get(name, Variable())
            text = variable.text()
            if text is not None:
                try:
                    variable.value = text.encode(encoding)
                except UnicodeEncodeError as error:
                    raise RuntimeError(
                        statement.line,
                        f"value cannot be stored using {encoding}: {error}",
                    ) from error
            variable.encoding = encoding
            context.variables[name] = variable
            self.last = Result("ok", encoding)
            return
        if kind == "push":
            self._assign(statement, context)
            return
        if kind == "call":
            component = str(statement.data["component"])
            if component not in context.imports and component not in {"execute", "math"}:
                raise RuntimeError(statement.line, f"component '{component}' has not been imported")
            if component == "pe":
                raise RuntimeError(statement.line, "pe is recognized but not implemented")
            self.last = Result("ok", component)
            return
        if kind == "father":
            self.pending_inheritance = True
            self.last = Result("ok")
            return
        if kind == "start":
            self._run_block(statement, context)
            return
        if kind == "jump":
            if block_mode is None:
                raise RuntimeError(statement.line, "jump is only valid inside an execution block")
            target_line = int(statement.data["line"])
            self.pending_jump_line = target_line
            self.last = Result("ok", str(target_line))
            return
        if kind == "dog":
            name = str(statement.data["name"])
            actual = self.last_block_status.get("status") if name == "status" else self.last_block_status.get(name)
            if name != "status":
                runtime_block = self.atom_runtime.get(name)
                if runtime_block is not None:
                    if runtime_block.state() == "running":
                        runtime_block.wait_until(
                            lambda: runtime_block.status not in {"running"}
                            or runtime_block.waiting,
                            None,
                        )
                    live = runtime_block.state()
                    if live in {"running", "stopped"} or (
                        live == "idle" and actual is None
                    ):
                        actual = live
            mappings = statement.data.get("mappings")
            if isinstance(mappings, tuple):
                matched = next(
                    (
                        outcome
                        for accepted, outcome in mappings
                        if actual == ("ok" if accepted == "pass" else accepted)
                    ),
                    None,
                )
                if matched is None:
                    allowed = ", ".join(accepted for accepted, _ in mappings)
                    raise RuntimeError(
                        statement.line,
                        f"Loop_Deadlock: dog({name}) status {actual!r} is not in whitelist ({allowed})",
                    )
                outcome = "ok" if matched == "pass" else matched
                self.last = Result(outcome, actual)
                return
            required = str(statement.data["condition"])
            if actual != required:
                raise RuntimeError(
                    statement.line,
                    f"Loop_Deadlock: dog({name}) expected {required!r}, last block status was {actual!r}",
                )
            self.last = Result("ok", actual)
            return
        if kind == "nop":
            self.last = Result("ok")
            return
        if kind == "math":
            mode = str(statement.data.get("precision") or self.math_mode)
            if mode == "complex":
                pass
            elif mode == "bigint":
                pass
            value = _evaluate_expression(
                str(statement.data["expression"]),
                context,
                statement.line,
                math_mode=mode,
                functions=self.functions,
                error_count=self.error_count,
            )
            self.last = Result("ok", _format_numeric(value, self.output_precision))
            return
        if kind == "function":
            name = str(statement.data["name"]).lower()
            parameters = tuple(str(parameter) for parameter in statement.data["parameters"])
            if name in self.functions:
                raise RuntimeError(statement.line, f"function '{name}' is already defined")
            if name in _MATH_FUNCTIONS or name in {
                "complex", "choose", "read_text", "write_text", "strlen",
                "char_at", "chr", "ord", "array_sum", "array_at", "dot",
                "matrix2_det", "matrix2_mul_entry", "blas_dot",
                "lapack_determinant2", "array_len", "array_set", "array_push",
                "mean", "std", "min", "max", "err_count",
                "matrix_rows", "matrix_cols", "matrix_at", "matrix_mul",
                "matrix_transpose", "matrix2_inverse", "matrix3_inverse",
                "solve_linear", "hex", "unhex", "bytes_len", "bytes_get",
                "bytes_set", "median", "variance", "correlation", "percentile",
                "trapz", "simpson",
                "pi_digits", "pi_str", "e_digits", "e_str",
                "sqrt2_digits", "sqrt2_str",
            }:
                raise RuntimeError(statement.line, f"function name '{name}' is reserved")
            self.functions[name] = (
                parameters,
                str(statement.data["expression"]),
                statement.line,
            )
            self.last = Result("ok", name)
            return
        if kind == "if_expr":
            body = list(statement.body)
            else_index = next(
                (index for index, child in enumerate(body) if child.kind == "else_marker"),
                len(body),
            )
            if _evaluate_condition(str(statement.data["condition"]), context, statement.line, self.functions):
                selected = body[:else_index]
            else:
                selected = body[else_index + 1 :] if else_index < len(body) else []
            self._execute_sequence(selected, context, block_mode)
            return
        if kind == "while":
            iterations = 0
            while _evaluate_condition(str(statement.data["condition"]), context, statement.line, self.functions):
                if self.max_loop_iterations is not None and iterations >= self.max_loop_iterations:
                    raise RuntimeError(statement.line, "loop iteration limit exceeded")
                self._execute_sequence(statement.body, context, block_mode)
                if self.pending_jump_line is not None:
                    break
                iterations += 1
                if self.last.status == "stop":
                    break
            if self.last.status != "stop":
                self.last = Result("ok")
            return
        if kind == "tape_create":
            name = str(statement.data["name"])
            context.tapes[name] = Tape()
            self.last = Result("ok", "0")
            return
        if kind == "tape_move":
            name, direction = str(statement.data["name"]), str(statement.data["direction"])
            tape = self._get_tape(context, name, statement.line)
            tape.pointer += -1 if direction == "left" else 1
            self.last = Result("ok", str(tape.pointer))
            return
        if kind == "tape_write":
            tape = self._get_tape(context, str(statement.data["name"]), statement.line)
            symbol = int(statement.data["symbol"])
            if not 0 <= symbol <= 255:
                raise RuntimeError(statement.line, "tape symbol must be in range 0..255")
            tape.write(symbol)
            self.last = Result("ok", str(symbol))
            return
        if kind == "tape_read":
            tape = self._get_tape(context, str(statement.data["name"]), statement.line)
            self.last = Result("ok", str(tape.read()))
            return
        if kind == "exit":
            raise ExitRequest(int(statement.data["code"]))
        if kind == "sleep":
            self.sleep(float(statement.data["seconds"]))
            self.last = Result("ok")
            return
        if kind == "print":
            self._print(statement, context)
            return
        if kind == "kill":
            name = str(statement.data["name"])
            variable = context.variables.get(name)
            if variable is None:
                self.last = Result("error", "error")
            else:
                variable.killed = True
                self.last = Result("ok", "killer")
            return
        if kind == "if":
            self._if(statement, context, block_mode)
            return
        if kind == "command":
            self._command(statement, context)
            return
        if kind == "lock":
            self._atom_lock_block(statement, context)
            return
        if kind == "n_bomb":
            self._atom_n_bomb(statement, context)
            return
        if kind == "neutron":
            self._require_atom(context, statement.line, "neutron")
            parent_name = str(statement.data["parent"])
            child_name = str(statement.data["child"])
            self._atom_block(parent_name, statement.line)
            child = self._atom_block(child_name, statement.line)
            with child.condition:
                child.parent_name = parent_name
            self.last = Result("ok", child_name)
            return
        if kind == "proton":
            self._require_atom(context, statement.line, "proton")
            chain_name = str(statement.data["chain"])
            blocks = tuple(str(block) for block in statement.data["blocks"])  # type: ignore[union-attr]
            for block_name in blocks:
                self._atom_block(block_name, statement.line)
            self.atom_chains[chain_name] = blocks
            self.last = Result("ok", chain_name)
            return
        if kind == "nuclear_reactor":
            self._atom_reactor(statement, context)
            return
        if kind == "calldll":
            self._calldll(statement, context)
            return
        if kind == "cdll":
            self._cdll(statement, context)
            return
        raise RuntimeError(statement.line, f"unsupported statement kind: {kind}")

    def _bytes_statement(self, statement: Statement, context: Context) -> None:
        kind = statement.kind
        if kind == "bytes_declare":
            name = str(statement.data["name"])
            try:
                transformed, values = _numeric_values(
                    str(statement.data["expression"]), context, statement.line
                )
                tree = ast.parse(transformed, mode="eval")
                raw = _eval_array_literal(
                    tree.body,
                    values,
                    complex_mode=False,
                    functions=self.functions,
                )
            except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError) as error:
                raise RuntimeError(
                    statement.line, f"invalid bytes declaration: {error}"
                ) from error
            masked: list[float] = []
            for value in raw:
                if isinstance(value, complex) or not float(value).is_integer():
                    raise RuntimeError(
                        statement.line, "bytes elements must be integers in 0..255"
                    )
                if not 0 <= int(value) <= 255:
                    raise RuntimeError(
                        statement.line, "bytes elements must be integers in 0..255"
                    )
                masked.append(float(int(value)))
            context.arrays[name] = masked
            self.byte_arrays.add(name)
            self.last = Result("ok", name)
            return
        arguments = tuple(str(item) for item in statement.data["arguments"])
        name = arguments[0]
        if name.startswith("{"):
            name = name.strip("{} ").strip()
        values = context.arrays.get(name)
        if values is None:
            raise RuntimeError(statement.line, f"bytes array '{name}' is not declared")
        index = self._bytes_index(statement, context, arguments[1] if len(arguments) > 1 else None)
        if kind == "bytes_len":
            self.last = Result("ok", str(len(values)))
            return
        if kind == "bytes_get":
            self.last = Result("ok", str(int(values[index])))
            return
        new_value = self._bytes_value(statement, context, arguments[2])
        values[index] = float(new_value)
        self.last = Result("ok", str(new_value))

    def _bytes_index(
        self, statement: Statement, context: Context, argument: str | None
    ) -> int:
        if argument is None:
            return 0
        try:
            value = _evaluate_expression(
                argument, context, statement.line,
                math_mode="int64", functions=self.functions,
                error_count=self.error_count,
            )
        except RuntimeError as error:
            raise RuntimeError(statement.line, f"bytes index is invalid: {error.message}") from error
        if not isinstance(value, int):
            raise RuntimeError(statement.line, "bytes index must be an integer")
        name = str(statement.data["arguments"][0]).strip("{} ").strip()
        length = len(context.arrays.get(name, []))
        if not 0 <= value < length:
            raise RuntimeError(statement.line, "bytes index is outside the array")
        return value

    def _bytes_value(self, statement: Statement, context: Context, argument: str) -> int:
        try:
            value = _evaluate_expression(
                argument, context, statement.line,
                math_mode="int64", functions=self.functions,
                error_count=self.error_count,
            )
        except RuntimeError as error:
            raise RuntimeError(statement.line, f"bytes value is invalid: {error.message}") from error
        if not isinstance(value, int) or not 0 <= int(value) <= 255:
            raise RuntimeError(statement.line, "bytes values must be integers in 0..255")
        return int(value)

    def _require_atom(self, context: Context, line: int, what: str) -> None:
        if "atom" in context.imports:
            return
        raise RuntimeError(line, f"component 'atom' has not been imported (required by {what})")

    def _current_atom_stop(self) -> threading.Event:
        block = getattr(self._atom_local, "block", None)
        if block is None:
            return _NEVER_STOP
        return block.stop

    def _atom_block(self, name: str, line: int) -> AtomBlock:
        with self.atom_lock:
            runtime = self.atom_runtime.get(name)
            if runtime is None:
                raise RuntimeError(line, f"unknown execution block '{name}'")
            return runtime

    def _run_block(self, statement: Statement, parent: Context) -> None:
        name = str(statement.data["name"])
        mode = str(statement.data["mode"])
        with self.atom_lock:
            self.atom_blocks.setdefault(name, statement)
            if self.pending_inheritance:
                self.atom_father_blocks.add(name)
            runtime_block = self.atom_runtime.get(name)
            if runtime_block is None:
                runtime_block = AtomBlock(name)
                self.atom_runtime[name] = runtime_block
        direct_statements = {child.line: child for child in statement.body}

        def validate_jumps(children: tuple[Statement, ...]) -> None:
            for child in children:
                if child.kind == "jump":
                    target = direct_statements.get(int(child.data["line"]))
                    if target is None or target.kind in {"start", "while", "if_expr"}:
                        raise RuntimeError(
                            child.line,
                            f"jump target line {child.data['line']} must be a direct, non-control statement in this execution block",
                        )
                elif child.kind in {"while", "if_expr"}:
                    validate_jumps(child.body)
                elif child.kind == "start":
                    continue

        validate_jumps(statement.body)
        else_body = statement.data.get("else_body", ())
        if isinstance(else_body, tuple):
            def first_jump(children: tuple[Statement, ...]) -> Statement | None:
                for child in children:
                    if child.kind == "jump":
                        return child
                    if child.kind in {"while", "if_expr"}:
                        nested_jump = first_jump(child.body)
                        if nested_jump is not None:
                            return nested_jump
                return None

            jump = first_jump(else_body)
            if jump is not None:
                raise RuntimeError(jump.line, "jump is not supported inside an execution block else branch")
            validate_jumps(else_body)
        context = parent.inherit() if self.pending_inheritance else Context()
        self.pending_inheritance = False
        if "atom" in parent.imports:
            context.imports.add("atom")
        if "atom" in context.imports or "atom" in self.context.imports:
            runtime_block.context = context
        status = "ok"
        iterations = 0
        self.last = Result("ok", name)
        while True:
            if (
                mode == "loop"
                and self.max_loop_iterations is not None
                and iterations >= self.max_loop_iterations
            ):
                raise RuntimeError(statement.line, f"Loop_Deadlock: block '{name}' exceeded iteration limit")
            self._execute_sequence(statement.body, context, block_mode=mode)
            if self.pending_jump_line is not None:
                target_line = self.pending_jump_line
                self.pending_jump_line = None
                raise RuntimeError(
                    statement.line,
                    f"jump target line {target_line} is not a direct statement in execution block '{name}'",
                )
            status = self.last.status
            stopped = status == "stop"
            if stopped:
                break
            if mode != "loop":
                break
            iterations += 1
        if status != "ok":
            if isinstance(else_body, tuple) and else_body:
                self._execute_sequence(else_body, context, block_mode=mode)
                status = self.last.status
        self.pending_inheritance = False
        self.last_block_status[name] = status
        self.last_block_status["status"] = status
        self.last = Result(status, name)

    def _execute_sequence(
        self,
        statements: list[Statement] | tuple[Statement, ...],
        context: Context,
        block_mode: str | None,
    ) -> None:
        index = 0
        while index < len(statements):
            if self.pending_jump_line is not None:
                target_index = next(
                    (
                        candidate
                        for candidate, statement in enumerate(statements)
                        if statement.line == self.pending_jump_line
                    ),
                    None,
                )
                if target_index is None:
                    return
                self.pending_jump_line = None
                index = target_index
            if self._current_atom_stop().is_set():
                self.last = Result("stop", "stopped")
                break
            child = statements[index]
            self._execute(child, context, block_mode)
            if self.last.status == "stop":
                break
            index += 1

    @staticmethod
    def _get_tape(context: Context, name: str, line: int) -> Tape:
        tape = context.tapes.get(name)
        if tape is None:
            raise RuntimeError(line, f"tape '{name}' is not declared")
        return tape

    def _assign(self, statement: Statement, context: Context) -> None:
        name = str(statement.data["name"])
        value_spec = statement.data["value"]
        if not isinstance(value_spec, dict):
            raise RuntimeError(statement.line, "invalid assignment value")
        if value_spec.get("kind") == "array_expression":
            expression = str(value_spec.get("expression", ""))
            try:
                transformed, values = _numeric_values(expression, context, statement.line)
                tree = ast.parse(transformed, mode="eval")
                matrix_value = _eval_matrix_root(
                    tree.body,
                    values,
                    complex_mode=self.math_mode == "complex",
                    functions=self.functions,
                )
                array_value = (
                    None
                    if matrix_value is not None
                    else _eval_array_literal(
                        tree.body,
                        values,
                        complex_mode=self.math_mode == "complex",
                        functions=self.functions,
                    )
                )
            except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError) as error:
                raise RuntimeError(statement.line, f"invalid array assignment: {error}") from error
            existing = context.variables.get(name)
            encoding = statement.data.get("encoding")
            if encoding is None:
                encoding = existing.encoding if existing else None
            context.variables[name] = Variable(
                encoding=str(encoding) if encoding else None,
                array_value=None if array_value is None else array_value.copy(),
                matrix_value=None if matrix_value is None else matrix_value.copy(),
            )
            self.last = Result("ok")
            return
        if value_spec.get("kind") == "variable":
            source_name = str(value_spec.get("name", ""))
            source = context.variables.get(source_name)
            if source is not None and source.killed:
                raise RuntimeError(statement.line, f"variable '{source_name}' was killed")
            if source is not None and (
                source.array_value is not None or source.matrix_value is not None
            ):
                context.variables[name] = Variable(
                    encoding=(
                        str(statement.data["encoding"])
                        if statement.data.get("encoding")
                        else (context.variables[name].encoding if name in context.variables else source.encoding)
                    ),
                    array_value=(
                        None if source.array_value is None else source.array_value.copy()
                    ),
                    matrix_value=(
                        None if source.matrix_value is None else source.matrix_value.copy()
                    ),
                )
                self.last = Result("ok")
                return
        value = self._resolve_value(value_spec, context, statement.line)
        encoding = statement.data.get("encoding")
        if encoding is None:
            existing = context.variables.get(name)
            encoding = existing.encoding if existing else None
        if encoding:
            try:
                encoded_value = value.encode(str(encoding))
            except (LookupError, UnicodeEncodeError) as error:
                raise RuntimeError(statement.line, f"value cannot be encoded as {encoding}: {error}") from error
        else:
            encoded_value = value.encode("utf-8")
        context.variables[name] = Variable(encoded_value, str(encoding) if encoding else None)
        self.last = Result("ok", value)

    def _resolve_value(self, spec: dict[str, object], context: Context, line: int) -> str:
        kind = spec.get("kind")
        if kind == "literal":
            return str(spec.get("value", ""))
        if kind == "concat":
            parts = spec.get("parts")
            if not isinstance(parts, list):
                raise RuntimeError(line, "invalid string concatenation")
            return "".join(self._resolve_value(part, context, line) for part in parts)
        if kind == "up":
            if self.last.value is None:
                raise RuntimeError(line, "up has no value on the previous line")
            return self.last.value
        if kind == "variable":
            name = str(spec.get("name", ""))
            variable = context.variables.get(name)
            if variable is None:
                raise RuntimeError(line, f"empty variable '{name}'")
            if variable.killed:
                raise RuntimeError(line, f"variable '{name}' was killed")
            value = variable.text()
            if value is None:
                raise RuntimeError(line, f"empty variable '{name}'")
            return value
        raise RuntimeError(line, "unsupported value expression")

    def _print(self, statement: Statement, context: Context) -> None:
        expression = str(statement.data["expression"]).strip()
        encoding = statement.data.get("encoding")
        value_spec = statement.data.get("value")
        if isinstance(value_spec, dict) and value_spec.get("kind") == "variable":
            name = str(value_spec.get("name", ""))
            variable = context.variables.get(name)
            if variable is None or variable.value is None:
                self.error_count += 1
                self.last = Result("error", "error")
                return
            if variable.killed:
                self.error_count += 1
                self.last = Result("error", "killer")
                return
            value = variable.text()
            if value is None:
                self.error_count += 1
                self.last = Result("error", "error")
                return
            encoding = encoding or variable.encoding
        elif isinstance(value_spec, dict):
            value = self._resolve_value(value_spec, context, statement.line)
        else:
            value = expression
        if encoding:
            try:
                encoded_value = value.encode(str(encoding))
            except (LookupError, UnicodeEncodeError) as error:
                raise RuntimeError(statement.line, f"cannot print using {encoding}: {error}") from error
            buffer = getattr(self.output, "buffer", None)
            if buffer is not None:
                buffer.write(encoded_value + b"\n")
                buffer.flush()
            else:
                print(value, file=self.output)
        else:
            print(value, file=self.output)
        self.last = Result("ok", value)

    def _if(self, statement: Statement, context: Context, block_mode: str | None) -> None:
        selector = str(statement.data["selector"])
        expected = str(statement.data["expected"])
        if selector == "status":
            actual = self.last.status
        elif selector == "*":
            actual = self.last.value
        else:
            variable = context.variables.get(selector)
            if variable is None:
                actual = None
            elif variable.killed:
                actual = "killer"
            else:
                actual = variable.text()
        target = statement.data["target"]
        if actual is not None and actual.lower() == expected.lower():
            if not isinstance(target, dict):
                raise RuntimeError(statement.line, "invalid goto target")
            if target.get("kind") == "stop":
                if block_mode is None:
                    raise RuntimeError(statement.line, "goto stop is only valid inside an execution block")
                self.last = Result("stop")
                return
            if target.get("kind") == "push":
                assignment = Statement(
                    "push",
                    statement.line,
                    {
                        "name": target["name"],
                        "value": target["value"],
                        "encoding": target.get("encoding"),
                    },
                )
                self._assign(assignment, context)
                return
            raise RuntimeError(statement.line, "unsupported goto target")
        self.last = Result("ok")

    def _command(self, statement: Statement, context: Context) -> None:
        if not self.allow_external_commands:
            raise RuntimeError(statement.line, "external command execution is disabled")
        command = str(statement.data["command"])
        try:
            output, return_code = self.external_runner(command)
        except (OSError, subprocess.SubprocessError) as error:
            self.last = Result("error", str(error))
            print(f"line {statement.line}: external command failed: {error}", file=self.error_output)
            return
        if return_code != 0:
            print(
                f"line {statement.line}: external command exited with status {return_code}",
                file=self.error_output,
            )
        output = output.rstrip("\r\n")
        self.last = Result("ok" if return_code == 0 else "error", output)
        name = str(statement.data["name"])
        context.variables[name] = Variable(output.encode("utf-8"), "utf-8")

    def _dll_fail(self, line: int, message: str) -> None:
        self.error_count += 1
        print(f"line {line}: {message}", file=self.error_output)
        self.last = Result("error", message)

    @staticmethod
    def _require_dll_import(statement: Statement, context: Context) -> None:
        if "dll" not in context.imports:
            raise RuntimeError(statement.line, "component 'dll' has not been imported")

    def _dll_stdcall_windows(self) -> bool:
        return os.name == "nt" and any(
            convention == "stdcall"
            for _params, _return, _symbol, convention in self.extern_functions.values()
        )

    def _atom_lock_block(self, statement: Statement, context: Context) -> None:
        self._require_atom(context, statement.line, "lock")
        name = str(statement.data["name"])
        timeout = statement.data.get("timeout")
        target = self.atom_runtime.get(name)
        if target is None:
            target_context = context
        else:
            target_context = target.context or context
        target_context.imports.add("atom")
        holder = getattr(self._atom_local, "block", None)
        holder_name = holder.name if holder is not None else f"main-{threading.get_ident()}"
        deadline = (
            time.monotonic() + float(timeout) if timeout is not None else None
        )
        while True:
            target_context.lock.acquire()
            if (
                target_context.lock_owner is None
                or target_context.lock_owner == holder_name
            ):
                target_context.lock_owner = holder_name
                target_context.lock_depth += 1
                break
            target_context.lock.release()
            if target is not None:
                with target.condition:
                    target.waiting = True
                    target.condition.notify_all()
            try:
                if deadline is None:
                    time.sleep(0.001)
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self.error_count += 1
                        raise RuntimeError(
                            statement.line,
                            f"lock timeout: execution block '{name}' is still locked",
                        )
                    time.sleep(min(0.001, remaining))
            finally:
                if target is not None:
                    with target.condition:
                        target.waiting = False
                        target.condition.notify_all()
        try:
            self._execute_sequence(statement.body, target_context, block_mode=None)
        finally:
            target_context.lock_depth -= 1
            if target_context.lock_depth <= 0:
                target_context.lock_depth = 0
                target_context.lock_owner = None
            target_context.lock.release()

    def _atom_n_bomb(self, statement: Statement, context: Context) -> None:
        self._require_atom(context, statement.line, "n-bomb")
        target = str(statement.data["target"])
        if bool(statement.data.get("all")):
            with self.atom_lock:
                blocks = list(self.atom_runtime.values())
        else:
            blocks = [self._atom_block(target, statement.line)]
        for block in blocks:
            block.stop.set()
            block.set_state("stopped")
        self.last = Result("ok", target)

    def _atom_reactor(self, statement: Statement, context: Context) -> None:
        self._require_atom(context, statement.line, "nuclear_reactor")
        parent_name = str(statement.data["parent"])
        chain_name = str(statement.data["chain"])
        chain = self.atom_chains.get(chain_name)
        if chain is None:
            raise RuntimeError(statement.line, f"unknown execution chain '{chain_name}'")
        parent = self.atom_runtime.get(parent_name)
        parent_context = parent.context if parent is not None else None
        if parent_context is None:
            parent_context = context
        for block_name in dict.fromkeys(chain):
            block = self._atom_block(block_name, statement.line)
            with block.condition:
                if block.status == "running":
                    raise RuntimeError(
                        statement.line,
                        f"execution block '{block_name}' is already running",
                    )
                block.status = "running"
                block.waiting = False
                block.stop.clear()
        thread = threading.Thread(
            target=self._atom_run_chain,
            args=(chain, parent_context if parent is not None else context, context),
            name="atom-chain",
            daemon=True,
        )
        thread.start()
        self.last = Result("ok", chain_name)

    def _atom_run_chain(
        self, chain: tuple[str, ...], parent_context: Context, caller: Context
    ) -> None:
        # A chain shares one context across its steps so later steps observe
        # earlier results. Blocks declared after ``father = up`` reuse the
        # caller context directly, so lock guards the caller's variables.
        running_context = parent_context.inherit()
        running_context.imports.add("atom")
        for block_name in chain:
            block = self._atom_block(block_name, 0)
            context = (
                parent_context
                if block_name in self.atom_father_blocks
                else running_context
            )
            block.context = context
            self._atom_run(block, context)

    def _atom_run(self, block: AtomBlock, context: Context) -> None:
        status = "ok"
        self._atom_local.block = block
        try:
            statement = self.atom_blocks[block.name]
            self._execute_sequence(statement.body, context, block_mode="normal")
            if block.stop.is_set():
                status = "stopped"
        except RuntimeError as error:
            with self.output_lock:
                print(f"line {error.line}: {error.message}", file=self.error_output)
            self.error_count += 1
            status = "stopped" if block.stop.is_set() else "error"
        except Exception as error:  # noqa: BLE001 - background threads must not escape
            with self.output_lock:
                print(f"atom block '{block.name}' failed: {error}", file=self.error_output)
            self.error_count += 1
            status = "stopped" if block.stop.is_set() else "error"
        finally:
            for _ in range(64):
                if not context.lock._is_owned():
                    break
                context.lock.release()
        with self.atom_lock:
            self.last_block_status[block.name] = status
            self.last_block_status["status"] = status
        block.set_state("stopped" if block.stop.is_set() else status)

    def _calldll(self, statement: Statement, context: Context) -> None:
        self._require_dll_import(statement, context)
        library_name = str(statement.data["library"])
        key = library_name.lower()
        if key in self.dll_libraries:
            self.dll_states[key] = "loaded"
            self.last = Result("ok", library_name)
            return
        try:
            handle = _dll_load(library_name, use_stdcall_windows=self._dll_stdcall_windows())
        except OSError as error:
            self.dll_states[key] = "missing"
            self._dll_fail(statement.line, f"calldll failed: {error}")
            return
        self.dll_libraries[key] = handle
        self.dll_states[key] = "loaded"
        self.last = Result("ok", library_name)

    def _cdll(self, statement: Statement, context: Context) -> None:
        self._require_dll_import(statement, context)
        operation = str(statement.data["operation"])
        library_name = str(statement.data["library"])
        key = library_name.lower()
        if operation == "status":
            # Only libraries this program loaded ("loaded") or released via `out`
            # ("unloaded") are tracked; anything else reports "missing" without a
            # probe so status never has the side effect of mapping a module.
            if key in self.dll_libraries:
                state = "loaded"
            elif self.dll_states.get(key) == "unloaded":
                state = "unloaded"
            else:
                state = "missing"
            self.dll_states[key] = state
            self.last = Result("ok", state)
            return
        if operation == "out":
            if key not in self.dll_libraries:
                self._dll_fail(
                    statement.line, f"DLL library '{library_name}' is not loaded"
                )
                return
            # The handle is dropped rather than FreeLibrary/dlclose'd: another
            # ctypes handle (or Python itself) may still reference the same module.
            del self.dll_libraries[key]
            self.dll_states[key] = "unloaded"
            self.last = Result("ok", library_name)
            return
        if operation == "come":
            self._cdll_come(statement, context, library_name, key)
            return
        raise RuntimeError(statement.line, f"unsupported cdll operation: {operation}")

    def _cdll_come(
        self,
        statement: Statement,
        context: Context,
        library_name: str,
        key: str,
    ) -> None:
        if key not in self.dll_libraries:
            self._dll_fail(statement.line, f"DLL library '{library_name}' is not loaded")
            return
        function_name = str(statement.data["function"]).lower()
        declaration = self.extern_functions.get(function_name)
        if declaration is None:
            self._dll_fail(
                statement.line, f"DLL function '{function_name}' has no #extern declaration"
            )
            return
        parameter_types, return_type, export_symbol, _convention = declaration
        raw_arguments = tuple(str(argument) for argument in statement.data.get("arguments", ()))
        if len(raw_arguments) != len(parameter_types):
            self._dll_fail(
                statement.line,
                f"'{function_name}' expects {len(parameter_types)} argument(s), "
                f"got {len(raw_arguments)}",
            )
            return
        converted: list[object] = []
        for position, (raw, type_name) in enumerate(zip(raw_arguments, parameter_types), 1):
            try:
                value = _evaluate_expression(
                    raw,
                    context,
                    statement.line,
                    math_mode=self.math_mode,
                    functions=self.functions,
                )
                converted.append(
                    _dll_convert_argument(value, type_name, function_name, position)
                )
            except RuntimeError as error:
                self._dll_fail(statement.line, f"cdll = come failed: {error.message}")
                return
            except ValueError as error:
                self._dll_fail(statement.line, f"cdll = come failed: {error}")
                return
        handle = self.dll_libraries[key]
        function = _dll_resolve_export(handle, export_symbol)
        if function is None:
            self._dll_fail(
                statement.line,
                f"cdll = come failed: '{function_name}' is not exported by "
                f"'{library_name}'",
            )
            return
        argtypes = [_DLL_PARAM_TYPES[type_name] for type_name in parameter_types]
        restype = _DLL_PARAM_TYPES[return_type]
        try:
            function.argtypes = argtypes
            function.restype = restype
            value = function(*converted)
        except (ValueError, TypeError, OverflowError, OSError, ctypes.ArgumentError) as error:
            # A wrong native signature can still crash the process; ctypes cannot
            # catch access violations raised inside the callee.
            detail = str(error) or type(error).__name__
            self._dll_fail(statement.line, f"cdll = come failed: {detail}")
            return
        self.last = Result("ok", _dll_format_result(value, return_type, self.output_precision))

    @staticmethod
    def _run_external(command: str) -> tuple[str, int]:
        parts = shlex.split(command, posix=os.name != "nt")
        if not parts:
            raise OSError("empty external command")
        if os.name == "nt" and parts[0].lower() in {"dir", "get-childitem"}:
            executable = shutil.which("powershell.exe") or shutil.which("pwsh")
            if not executable:
                raise OSError("PowerShell was not found")
            completed = subprocess.run(
                [
                    executable,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); Get-ChildItem",
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        else:
            completed = subprocess.run(
                parts,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        output = completed.stdout
        if completed.stderr:
            print(completed.stderr.rstrip(), file=sys.stderr)
        return output, completed.returncode
