from __future__ import annotations

import ast
import json
import platform
import re
import warnings
from dataclasses import dataclass, field

from .expression_syntax import transform_braced_expression
from . import hop

class ParseError(ValueError):
    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"line {line}: {message}")
        self.line = line
        self.message = message


@dataclass(frozen=True)
class Statement:
    kind: str
    line: int
    data: dict[str, object] = field(default_factory=dict)
    body: tuple[Statement, ...] = ()


@dataclass(frozen=True)
class Program:
    statements: tuple[Statement, ...]
    math_mode: str = "float64"
    output_precision: int = 15
    extern_functions: tuple[tuple[str, tuple[str, ...], str, str, str], ...] = ()
    link_libraries: tuple[str, ...] = ()
    target: str = "host"
    records: tuple[tuple[str, tuple[str, ...]], ...] = ()
    gc_mode: str = "semi"
    features: frozenset[str] = frozenset()
    memory_mode: str = "heap"
    maxlen: int = 0
    on_oom: str = "abort"


def _iter_expression_texts(statement: Statement):
    for key in ("expression", "condition"):
        value = statement.data.get(key)
        if isinstance(value, str):
            yield value, statement.line
    value_spec = statement.data.get("value")
    if isinstance(value_spec, dict):
        expression = value_spec.get("expression")
        if isinstance(expression, str):
            yield expression, statement.line
    for child in statement.body:
        yield from _iter_expression_texts(child)
    for key in ("else_body", "catch_body", "finally_body"):
        nested = statement.data.get(key)
        if isinstance(nested, tuple):
            for child in nested:
                yield from _iter_expression_texts(child)
    body = statement.data.get("body")
    if isinstance(body, tuple):
        for child in body:
            yield from _iter_expression_texts(child)


def _iter_statements(statements: tuple[Statement, ...]):
    for statement in statements:
        yield statement
        yield from _iter_statements(statement.body)
        for key in ("else_body", "catch_body", "finally_body"):
            nested = statement.data.get(key)
            if isinstance(nested, tuple):
                yield from _iter_statements(nested)


def _host_asm_arch() -> str | None:
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64", "x64"}:
        return "x86_64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return None


def _rewrite_qualified_names(
    statement: Statement,
    declared_functions: set[str],
    namespace_prefixes: set[str],
) -> None:
    pattern = re.compile(r"\b([A-Za-z_]\w*)::([A-Za-z_]\w*)")

    def rewrite(text: str) -> str:
        def replace(match: re.Match[str]) -> str:
            namespace = match.group(1).lower()
            symbol = match.group(2).lower()
            if namespace not in namespace_prefixes:
                return match.group(0)
            candidate = f"{namespace}__{symbol}"
            return candidate if candidate in declared_functions else match.group(0)

        return pattern.sub(replace, text)

    for key in ("expression", "condition"):
        value = statement.data.get(key)
        if isinstance(value, str):
            statement.data[key] = rewrite(value)
    value_spec = statement.data.get("value")
    if isinstance(value_spec, dict):
        expression = value_spec.get("expression")
        if isinstance(expression, str):
            value_spec["expression"] = rewrite(expression)
    for child in statement.body:
        _rewrite_qualified_names(child, declared_functions, namespace_prefixes)
    for key in ("else_body", "catch_body", "finally_body"):
        nested = statement.data.get(key)
        if isinstance(nested, tuple):
            for child in nested:
                _rewrite_qualified_names(child, declared_functions, namespace_prefixes)
    body = statement.data.get("body")
    if isinstance(body, tuple):
        for child in body:
            _rewrite_qualified_names(child, declared_functions, namespace_prefixes)


class _ExpressionNameReplacer(ast.NodeTransformer):
    def __init__(self, name: str, expression: str) -> None:
        self.name = name
        self.expression = ast.parse(expression, mode="eval").body

    def visit_Name(self, node: ast.Name) -> ast.expr:
        if node.id == self.name:
            replacement = ast.parse(
                ast.unparse(self.expression), mode="eval"
            ).body
            return ast.copy_location(replacement, node)
        return node


_START_RE = re.compile(r"start\s*\(\s*(loop|normal)\s*\)\s*=\s*\{\s*([A-Za-z_]\w*)\s*\}", re.I)
_DOG_RE = re.compile(r"dog\s*\(\s*([A-Za-z_]\w*)\s*\)\s*=\s*\{\s*([A-Za-z_]\w*)\s*\}", re.I)
_DOG_WHITELIST_RE = re.compile(
    r"dog\s*\(\s*([A-Za-z_]\w*)\s*\)\s*=\s*"
    r"\(\s*(stop|pass|ok|error|idle|running|stopped)\s*:\s*(stop|pass|ok|error|idle|running|stopped)\s*\)"
    r"(?:\s*\(\s*(stop|pass|ok|error|idle|running|stopped)\s*:\s*(stop|pass|ok|error|idle|running|stopped)\s*\))*",
    re.I,
)
_JUMP_RE = re.compile(r"jump\s*\(\s*(\d+)\s*\)", re.I)
_CALL_RE = re.compile(r"call\s*=\s*\(\s*([A-Za-z_]\w*)\s*\)", re.I)
_FATHER_RE = re.compile(r"father\s*=\s*\(?\s*(up)\s*\)?", re.I)
_IF_RE = re.compile(
    r"if\s+up\s*(?:\{\s*([^}]+?)\s*\})?\s*=\s*\((.*?)\)\s+goto\s+\((.*)\)\s*$",
    re.I,
)
_PUSH_RE = re.compile(r"push\s*\(\s*\{\s*([A-Za-z_]\w*)\s*\}\s*=\s*(.*?)\s*\)\s*$", re.I)
_REQUIRE_OK_RE = re.compile(r"require_ok(?:\s+(\d+))?\s*", re.I)
_VAR_RE = re.compile(r"var\s+([A-Za-z_]\w*)\s*=\s*(.+)\s*$", re.I)
_ENCODING_RE = re.compile(r"([A-Za-z_]\w*)\s*=\s*([A-Za-z0-9_-]+)\s*$", re.I)
_SLEEP_RE = re.compile(r"sleep\s*\(\s*(\d+(?:\.\d+)?)\s*\)\s*(?:\{\s*s\s*\})?", re.I)
_EXIT_RE = re.compile(r"exit\s*\(\s*(\d+)\s*\)", re.I)
_ASK_RE = re.compile(
    r"ask\s*\(\s*\{\s*(?P<name>[^\W\d]\w*)\s*\}\s*\)"
    r"(?:\s*\(\s*<(?P<prompt_first>.+)>\s*\))?"
    r"(?P<secret>\s+-secret\b)?"
    r"(?:\s*\(\s*<(?P<prompt_second>.+)>\s*\))?"
    r"\s*$",
    re.I,
)
_KILL_RE = re.compile(r"kill\s*\{\s*([A-Za-z_]\w*)\s*\}(?:\s+(-force))?", re.I)
_PRINT_RE = re.compile(r"print\s*\(\s*<(.+?)>\s*\)(?:\s+-([A-Za-z0-9_-]+))?", re.I)
_MATH_RE = re.compile(
    r"math_run\s+(.+?)\s*=\s*\?(?:\s+-\s*(int32|int64|uint32|float64|complex|bigint))?",
    re.I,
)
_ASSIGN_COMMAND_RE = re.compile(r"([A-Za-z_]\w*)\s*=\s*(.+)", re.I)
_IN_RE = re.compile(r"\{\s*\(?\s*in\s*=\s*up\s*\)?\s*\}", re.I)
_WHILE_RE = re.compile(r"while\s*\((.*)\)\s*$", re.I)
_IF_BLOCK_RE = re.compile(r"if\s*\((.*)\)\s*$", re.I)
_TAPE_CREATE_RE = re.compile(r"tape_create\s*\(\s*([A-Za-z_]\w*)\s*\)", re.I)
_LOCK_RE = re.compile(
    r"lock\s*\{\s*([A-Za-z_]\w*)\s*\}"
    r"(?:\s+-\s*timeout\s+(\d+(?:\.\d+)?))?\s*$",
    re.I,
)
_N_BOMB_RE = re.compile(r"n-bomb\s*\{\s*([A-Za-z_]\w*)\s*\}", re.I)
_NEUTRON_RE = re.compile(
    r"neutron\s*\{\s*([A-Za-z_]\w*)\s*\}\s*=\s*\{\s*([A-Za-z_]\w*)\s*\}\s*$",
    re.I,
)
_PROTON_RE = re.compile(r"proton\s*\{\s*([A-Za-z_]\w*)\s*\}\s*=\s*(.+)$", re.I)
_REACTOR_RE = re.compile(
    r"nuclear_reactor\s*\{\s*([A-Za-z_]\w*)\s*\}\s*=\s*\{\s*([A-Za-z_]\w*)\s*\}\s*$",
    re.I,
)
_CALLDLL_RE = re.compile(r"calldll\s*=\s*\(\s*([A-Za-z_]\w*)\s*\)\s*$", re.I)
_CDLL_RE = re.compile(
    r"cdll\s*=\s*(come|out|status)\s*\(\s*([A-Za-z_]\w*)\s*\)(.*)$",
    re.I,
)
_EXTERN_RE = re.compile(
    r"extern\s+([A-Za-z_]\w*)\s*\((.*?)\)\s*->\s*([A-Za-z_]\w*)"
    r"(?:\s+as\s+([A-Za-z_]\w*))?(\s+stdcall)?\s*$",
    re.I,
)
_LINK_RE = re.compile(r"link\s+([A-Za-z0-9_.+-]+)\s*$", re.I)
DLL_TYPES = {
    "VOID", "INT", "UINT", "LONG", "DWORD", "WORD", "BYTE",
    "CHAR", "PTR", "STR", "FLOAT", "DOUBLE", "LPCSTR", "HWND",
}
_TAPE_MOVE_RE = re.compile(
    r"tape_move\s*\(\s*([A-Za-z_]\w*)\s*\)\s*=\s*\(\s*(left|right)\s*\)",
    re.I,
)
_TAPE_WRITE_RE = re.compile(
    r"tape_write\s*\(\s*([A-Za-z_]\w*)\s*\)\s*=\s*\{\s*(.*?)\s*\}",
    re.I,
)
_TAPE_READ_RE = re.compile(r"tape_read\s*\(\s*([A-Za-z_]\w*)\s*\)\s*=\s*\?", re.I)
_ARRAY_RE = re.compile(r"array\s+([A-Za-z_]\w*)\s*=\s*(.+)\s*$", re.I)
_BYTES_RE = re.compile(r"bytes\s+([A-Za-z_]\w*)\s*=\s*(.+)\s*$", re.I)
_BYTES_CALL_RE = re.compile(
    r"bytes_(set|get|len)\s*\((.*)\)\s*(?:=\s*\?)?\s*$", re.I
)
_FUNCTION_RE = re.compile(
    r"(?:generic\s+)?function\s+([A-Za-z_]\w*)\s*\((.*?)\)\s*=\s*(.+)\s*$",
    re.I,
)
_FUNCTION_HEADER_RE = re.compile(
    r"(?:generic\s+)?function\s+([A-Za-z_]\w*)\s*\((.*?)\)\s*$",
    re.I,
)
_FUNCTION_LOCAL_RE = re.compile(r"let\s+([A-Za-z_]\w*)\s*=\s*(.+)\s*$", re.I)
_FUNCTION_SET_RE = re.compile(
    r"set\s+([A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)?)\s*=\s*(.+)\s*$", re.I
)
_ASM_REGISTERS = {
    "x86_64": frozenset(
        {
            "rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp", "rsp",
            "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15",
            "eax", "ebx", "ecx", "edx", "esi", "edi", "ebp", "esp",
            "r8d", "r9d", "r10d", "r11d", "r12d", "r13d", "r14d", "r15d",
            "xmm0", "xmm1", "xmm2", "xmm3", "xmm4", "xmm5", "xmm6", "xmm7",
        }
    ),
    "arm64": frozenset(
        {f"x{index}" for index in range(31)}
        | {f"w{index}" for index in range(31)}
        | {"sp", "lr", "x0"}
    ),
}
_SUPPORTED_FEATURES = frozenset({"no-bigint", "no-array", "no-matrix"})
_DISABLED_FEATURE_FUNCTIONS = {
    "no-bigint": frozenset(
        {"pi_digits", "pi_str", "e_digits", "e_str", "sqrt2_digits", "sqrt2_str"}
    ),
    "no-array": frozenset(
        {
            "array_at",
            "array_set",
            "array_len",
            "array_push",
            "array_sum",
            "array_fill",
            "array_copy",
            "dot",
            "mean",
            "std",
            "min",
            "max",
            "median",
            "variance",
            "correlation",
            "percentile",
            "trapz",
            "simpson",
        }
    ),
    "no-matrix": frozenset(
        {
            "matrix_rows",
            "matrix_cols",
            "matrix_at",
            "matrix_mul",
            "matrix_transpose",
            "matrix2_inverse",
            "matrix3_inverse",
            "matrix2_det",
            "matrix2_mul_entry",
            "solve_linear",
            "lapack_determinant2",
        }
    ),
}
_RECORD_RE = re.compile(r"record\s+([A-Za-z_]\w*)\s*\{([^{}]*)\}\s*$", re.I)
_OPERATOR_RE = re.compile(
    r"operator\s*([+\-*/])\s*([A-Za-z_]\w*)\s*=\s*\{(.*)\}\s*$", re.I
)
_FUNCTION_WHILE_RE = re.compile(r"while\s*\((.*)\)\s*$", re.I)
_FUNCTION_BREAK_RE = re.compile(r"break\s*$", re.I)
_FUNCTION_CONTINUE_RE = re.compile(r"continue\s*$", re.I)
_RETURN_RE = re.compile(r"return\s+(.+)\s*$", re.I)


class _Parser:
    def __init__(self, source: str, line_map: list[int] | None = None) -> None:
        self.lines: list[tuple[int, str, bool]] = []
        self.line_map = line_map
        self.math_mode = "float64"
        math_mode_declared = False
        self.output_precision = 15
        output_precision_declared = False
        self.extern_functions: dict[str, dict[str, object]] = {}
        self.link_libraries: list[str] = []
        self.records: dict[str, tuple[str, ...]] = {}
        self.operators: dict[tuple[str, str], tuple[str, ...]] = {}
        self.namespace: str | None = None
        self.aliases: dict[str, tuple[int, str]] = {}
        self.function_parameters: dict[str, tuple[str, ...]] = {}
        self.gc_mode = "semi"
        self.gc_mode_declared = False
        self.features: frozenset[str] = frozenset()
        features_declared = False
        self.memory_mode = "heap"
        self.memory_mode_line = 0
        self.maxlen = 0
        self.maxlen_line = 0
        self.on_oom = "abort"
        self.on_oom_line = 0
        self.record_temp_count = 0
        self.target = "host"
        target_declared = False
        for index, original in enumerate(source.splitlines()):
            number = self.line_map[index] if self.line_map else index + 1
            line = original.strip()
            if not line:
                continue
            target_directive = re.fullmatch(r"#%\s*target\s*=\s*([A-Za-z0-9_-]+)\s*", line, re.I)
            if target_directive:
                if target_declared:
                    raise ParseError(number, "target can only be declared once")
                target = target_directive.group(1).lower()
                if target not in {"host", "riscv64-linux-gnu", "aarch64-linux-gnu"}:
                    raise ParseError(
                        number,
                        f"unsupported target '{target}'; "
                        "target must be host, riscv64-linux-gnu, or aarch64-linux-gnu",
                    )
                self.target = target
                target_declared = True
                continue
            if re.match(r"#%\s*target\s*=", line, re.I):
                raise ParseError(number, "target must be host, riscv64-linux-gnu, or aarch64-linux-gnu")
            precision_directive = re.fullmatch(r"#%\s*precision\s*=\s*(\d+)\s*", line, re.I)
            if precision_directive:
                if output_precision_declared:
                    raise ParseError(number, "output precision can only be declared once")
                output_precision = int(precision_directive.group(1))
                if not 1 <= output_precision <= 17:
                    raise ParseError(number, "output precision must be between 1 and 17 significant digits")
                self.output_precision = output_precision
                output_precision_declared = True
                continue
            if re.match(r"#%\s*precision\s*=", line, re.I):
                raise ParseError(number, "output precision must be an integer between 1 and 17")
            directive = re.fullmatch(r"#%\s*math\s*=\s*(int32|int64|uint32|float64|complex|bigint)\s*", line, re.I)
            if directive:
                if math_mode_declared:
                    raise ParseError(number, "math mode can only be declared once")
                self.math_mode = directive.group(1).lower()
                math_mode_declared = True
                continue
            if re.match(r"#%\s*math\s*=", line, re.I):
                raise ParseError(number, "math mode must be int32, int64, float64, complex, or bigint")
            memory_directive = re.fullmatch(
                r"#%\s*memory\s*=\s*(heap|arena|static)\s*", line, re.I
            )
            if memory_directive:
                if self.memory_mode_line:
                    raise ParseError(number, "memory mode can only be declared once")
                self.memory_mode = memory_directive.group(1).lower()
                self.memory_mode_line = number
                continue
            if re.match(r"#%\s*memory\s*=", line, re.I):
                raise ParseError(number, "memory mode must be heap, arena, or static")
            maxlen_directive = re.fullmatch(r"#%\s*maxlen\s*=\s*(\d+)\s*", line, re.I)
            if maxlen_directive:
                if self.maxlen_line:
                    raise ParseError(number, "maxlen can only be declared once")
                value = int(maxlen_directive.group(1))
                if not 1 <= value <= 1_000_000:
                    raise ParseError(number, "maxlen must be between 1 and 1000000")
                self.maxlen = value
                self.maxlen_line = number
                continue
            if re.match(r"#%\s*maxlen\s*=", line, re.I):
                raise ParseError(number, "maxlen must be an integer between 1 and 1000000")
            on_oom_directive = re.fullmatch(
                r"#%\s*on_oom\s*=\s*(abort|error)\s*", line, re.I
            )
            if on_oom_directive:
                if self.on_oom_line:
                    raise ParseError(number, "on_oom can only be declared once")
                self.on_oom = on_oom_directive.group(1).lower()
                self.on_oom_line = number
                continue
            if re.match(r"#%\s*on_oom\s*=", line, re.I):
                raise ParseError(number, "on_oom must be abort or error")
            features_directive = re.fullmatch(r"#%\s*features\s*(?:=\s*(.*))?", line, re.I)
            if features_directive:
                if features_declared:
                    raise ParseError(number, "features can only be declared once")
                raw_features = (features_directive.group(1) or "").strip()
                declared_features = {
                    item.strip().lower()
                    for item in raw_features.split(",")
                    if item.strip()
                }
                unknown = declared_features - _SUPPORTED_FEATURES
                if unknown:
                    raise ParseError(
                        number,
                        f"unknown feature tag '{sorted(unknown)[0]}'; supported tags are "
                        + ", ".join(sorted(_SUPPORTED_FEATURES)),
                    )
                self.features = frozenset(declared_features)
                features_declared = True
                continue
            if re.fullmatch(r"#%\s*namespace\s*=\s*[A-Za-z_]\w*\s*", line, re.I):
                self.lines.append((number, line, True))
                continue
            if re.fullmatch(
                r"#%\s*asm\s*=\s*[A-Za-z0-9_]+\s*\(\s*clobber\s*:\s*[^)]*\)\s*",
                line,
                re.I,
            ):
                self.lines.append((number, line, True))
                continue
            if re.match(r"#%\s*asm\s*=", line, re.I):
                raise ParseError(
                    number,
                    "#%asm requires an architecture and clobber list: #%asm = x86_64 (clobber: rax, memory)",
                )
            gc_directive = re.fullmatch(r"#gc\s*=\s*(none|semi|mark)\s*", line, re.I)
            if gc_directive:
                if self.gc_mode_declared:
                    raise ParseError(number, "gc mode can only be declared once")
                mode = gc_directive.group(1).lower()
                self.gc_mode = mode
                self.gc_mode_declared = True
                continue
            if re.match(r"#gc\s*=", line, re.I):
                raise ParseError(number, "gc mode must be none, semi, or mark")
            if re.fullmatch(
                r"#%\s*use\s*=\s*[A-Za-z_]\w*\.[A-Za-z_]\w*\s+as\s+[A-Za-z_]\w*\s*",
                line,
                re.I,
            ):
                self.lines.append((number, line, True))
                continue
            if line.startswith("#%"):
                continue
            was_hash_prefixed = line.startswith("#")
            if line.startswith("#"):
                line = line[1:].strip()
                extern_match = _EXTERN_RE.fullmatch(line)
                if extern_match:
                    self._add_extern(extern_match, number)
                    continue
                link_match = _LINK_RE.fullmatch(line)
                if link_match:
                    library = link_match.group(1)
                    if library not in self.link_libraries:
                        self.link_libraries.append(library)
                    continue
            self.lines.append((number, line, was_hash_prefixed))
        self.index = 0
        self.anonymous_block_count = 0

    def _add_extern(self, match: re.Match[str], line: int) -> None:
        name = match.group(1).lower()
        raw_parameters = match.group(2).strip()
        parameters = [
            parameter.strip().upper()
            for parameter in raw_parameters.split(",")
            if parameter.strip()
        ]
        for parameter in parameters:
            if parameter not in DLL_TYPES:
                raise ParseError(line, f"unknown DLL type '{parameter}'")
        return_type = match.group(3).upper()
        if return_type not in DLL_TYPES:
            raise ParseError(line, f"unknown DLL return type '{return_type}'")
        if name in self.extern_functions:
            raise ParseError(line, f"DLL function '{name}' is already declared")
        self.extern_functions[name] = {
            "parameters": parameters,
            "return_type": return_type,
            # 未写 `as` 时用源文件里的原始大小写作为导出名。
            # Windows 的 GetProcAddress 区分大小写，降成小写会找不到
            # PlaySoundA / GetTickCount 这类驼峰导出。
            "symbol": match.group(4) or match.group(1),
            "calling_convention": "stdcall" if match.group(5) else "cdecl",
        }

    def parse(self) -> Program:
        statements = self._sequence(in_block=False)
        if self.index != len(self.lines):
            number, _, _ = self.lines[self.index]
            raise ParseError(number, "unexpected trailing input")
        features = set(self.features)
        if "no-array" in features:
            features.add("no-matrix")
        for statement in _iter_statements(tuple(statements)):
            if (
                "no-array" in features
                and statement.kind in {"array_declare", "bytes_declare"}
            ):
                raise ParseError(
                    statement.line,
                    f"'{statement.kind.rsplit('_', 1)[0]}' declarations are not available "
                    "because feature 'no-array' is disabled",
                )
        for feature in sorted(features):
            disabled = _DISABLED_FEATURE_FUNCTIONS[feature]
            pattern = re.compile(
                r"\b(" + "|".join(sorted(disabled)) + r")\s*\(", re.I
            )
            for statement in _iter_statements(tuple(statements)):
                for text, line in _iter_expression_texts(statement):
                    match = pattern.search(text)
                    if match:
                        raise ParseError(
                            line,
                            f"'{match.group(1).lower()}' is not available because "
                            f"feature '{feature}' is disabled",
                        )
        if "no-bigint" in features and self.math_mode == "bigint":
            raise ParseError(
                1,
                "#%math = bigint is not available because feature 'no-bigint' is disabled",
            )

        declared_functions = set(self.function_parameters)
        namespace_prefixes = {
            name.split("__")[0] for name in declared_functions if "__" in name
        }
        for statement in statements:
            _rewrite_qualified_names(statement, declared_functions, namespace_prefixes)
        for alias, (line, target) in self.aliases.items():
            if target not in self.function_parameters:
                raise ParseError(line, f"use alias target '{target.replace('__', '::')}' is not defined")
            if alias in self.function_parameters:
                raise ParseError(line, f"use alias '{alias}' conflicts with an existing function")
            parameters = self.function_parameters[target]
            statements.insert(
                0,
                Statement(
                    "function",
                    line,
                    {
                        "name": alias,
                        "parameters": list(parameters),
                        "expression": f"{target}(" + ", ".join(parameters) + ")",
                        "generic": False,
                        "alias_of": target,
                    },
                ),
            )
            self.function_parameters[alias] = parameters
        extern_functions = tuple(
            (
                name,
                tuple(str(parameter) for parameter in definition["parameters"]),
                str(definition["return_type"]),
                str(definition["symbol"]),
                str(definition["calling_convention"]),
            )
            for name, definition in sorted(self.extern_functions.items())
        )
        if self.memory_mode == "static" and not self.maxlen:
            raise ParseError(
                self.memory_mode_line or 0,
                "memory mode 'static' requires #%maxlen so array capacities are "
                "known at compile time",
            )
        if self.memory_mode != "heap":
            for statement in _iter_statements(tuple(statements)):
                if statement.kind == "gc_pin":
                    raise ParseError(
                        statement.line,
                        "gc_pin requires #%memory = heap: pinned buffers must "
                        "outlive the block arena",
                    )
                if statement.kind in {"lock", "n_bomb", "neutron", "proton", "nuclear_reactor"}:
                    raise ParseError(
                        statement.line,
                        f"memory mode '{self.memory_mode}' is not supported with "
                        "atom blocks: the temporary pool is process-wide and not "
                        "thread safe",
                    )
        return Program(
            tuple(statements),
            self.math_mode,
            self.output_precision,
            extern_functions,
            tuple(self.link_libraries),
            self.target,
            tuple(
                (name, fields) for name, fields in sorted(self.records.items())
            ),
            self.gc_mode,
            frozenset(features),
            self.memory_mode,
            self.maxlen,
            self.on_oom,
        )

    def _sequence(self, in_block: bool) -> list[Statement]:
        statements: list[Statement] = []
        while self.index < len(self.lines):
            number, line, was_hash_prefixed = self.lines[self.index]
            self.index += 1
            if line == "}":
                if not in_block:
                    raise ParseError(number, "unexpected closing brace")
                return statements
            namespace_match = re.fullmatch(
                r"#%\s*namespace\s*=\s*([A-Za-z_]\w*)\s*", line, re.I
            )
            if namespace_match:
                if in_block:
                    raise ParseError(
                        number, "namespace declarations are only valid at the top level"
                    )
                chosen = namespace_match.group(1).lower()
                self.namespace = None if chosen in {"global", "main"} else chosen
                continue
            asm_declaration = re.fullmatch(
                r"#%\s*asm\s*=\s*([A-Za-z0-9_]+)\s*\(\s*clobber\s*:\s*([^)]*)\)\s*",
                line,
                re.I,
            )
            if asm_declaration:
                if in_block:
                    raise ParseError(number, "asm blocks are only valid at the top level")
                arch = asm_declaration.group(1).lower()
                if arch not in {"x86_64", "arm64"}:
                    raise ParseError(number, f"unsupported asm architecture '{arch}'")
                clobbers = tuple(
                    item.strip().lower()
                    for item in asm_declaration.group(2).split(",")
                    if item.strip()
                )
                if not clobbers:
                    raise ParseError(number, "#%asm requires a clobber list")
                if "memory" not in clobbers:
                    raise ParseError(number, "#%asm clobber list must include memory")
                if self.target != "host":
                    raise ParseError(
                        number,
                        f"asm block at line {number} requires {arch}, current target is {self.target}",
                    )
                host_arch = _host_asm_arch()
                if host_arch is not None and host_arch != arch:
                    raise ParseError(
                        number,
                        f"asm block at line {number} requires {arch}, current target is {host_arch}",
                    )
                if self.index >= len(self.lines) or self.lines[self.index][1].lower() != "asm":
                    raise ParseError(number, "#%asm must be immediately followed by #asm")
                asm_line = self.lines[self.index][0]
                self.index += 1
                self._expect_open_brace(asm_line)
                asm_lines: list[str] = []
                while self.index < len(self.lines):
                    _, asm_text, _ = self.lines[self.index]
                    self.index += 1
                    if asm_text == "}":
                        break
                    asm_lines.append(asm_text)
                else:
                    raise ParseError(asm_line, "unterminated asm block")
                body_text = " ".join(asm_lines)
                missing_clobbers = sorted(
                    register
                    for register in _ASM_REGISTERS[arch]
                    if register not in clobbers
                    and re.search(rf"\b{re.escape(register)}\b", body_text, re.I)
                )
                if missing_clobbers:
                    warnings.warn(
                        f"asm block at line {asm_line} references "
                        f"{', '.join(missing_clobbers)} without listing it in the "
                        "clobber list",
                        UserWarning,
                        stacklevel=2,
                    )
                statements.append(
                    Statement(
                        "asm",
                        asm_line,
                        {
                            "arch": arch,
                            "clobber": clobbers,
                            "lines": tuple(asm_lines),
                            "declaration_line": number,
                        },
                    )
                )
                continue
            use_match = re.fullmatch(
                r"#%\s*use\s*=\s*([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s+as\s+([A-Za-z_]\w*)\s*",
                line,
                re.I,
            )
            if use_match:
                if in_block:
                    raise ParseError(
                        number, "use declarations are only valid at the top level"
                    )
                namespace, symbol, alias = use_match.groups()
                alias_name = alias.lower()
                if alias_name in self.aliases:
                    raise ParseError(
                        number, f"use alias '{alias_name}' is already declared"
                    )
                self.aliases[alias_name] = (
                    number,
                    f"{namespace.lower()}__{symbol.lower()}",
                )
                continue
            if line == "{":
                if in_block:
                    raise ParseError(number, "unexpected nested opening brace")
                body = self._sequence(in_block=True)
                name = f"anonymous_{self.anonymous_block_count}"
                self.anonymous_block_count += 1
                statements.append(
                    Statement("start", number, {"mode": "normal", "name": name}, tuple(body))
                )
                continue

            record_match = _RECORD_RE.fullmatch(line)
            if record_match:
                if in_block:
                    raise ParseError(number, "record declarations are only valid at the top level")
                record_name, raw_fields = record_match.groups()
                record_fields = [
                    field.strip() for field in raw_fields.split(",") if field.strip()
                ]
                if not record_fields:
                    raise ParseError(number, "record must declare at least one field")
                if any(
                    not re.fullmatch(r"[A-Za-z_]\w*", field) for field in record_fields
                ):
                    raise ParseError(number, "record fields must be identifiers")
                if len(set(record_fields)) != len(record_fields):
                    raise ParseError(number, "record field names must be unique")
                if record_name.lower() in {
                    existing.lower() for existing in self.records
                }:
                    raise ParseError(number, f"record '{record_name}' is already declared")
                self.records[record_name] = tuple(record_fields)
                statements.append(
                    Statement(
                        "record",
                        number,
                        {"name": record_name, "fields": tuple(record_fields)},
                    )
                )
                continue
            if line.lower().startswith("record"):
                raise ParseError(number, "invalid record; expected record Name { field, ... }")

            operator_match = _OPERATOR_RE.fullmatch(line)
            if operator_match:
                if in_block:
                    raise ParseError(number, "operator declarations are only valid at the top level")
                symbol, record_name, raw_fields = operator_match.groups()
                if record_name not in self.records:
                    raise ParseError(number, f"unknown record '{record_name}'")
                if (symbol, record_name) in self.operators:
                    raise ParseError(
                        number, f"operator '{symbol}' is already declared for record '{record_name}'"
                    )
                fields = self.records[record_name]
                field_expressions = [
                    expression.strip()
                    for expression in raw_fields.split(",")
                    if expression.strip()
                ]
                if len(field_expressions) != len(fields):
                    raise ParseError(
                        number,
                        f"operator '{symbol}' for record '{record_name}' must produce "
                        f"{len(fields)} value(s), got {len(field_expressions)}",
                    )
                normalized_fields: list[str] = []
                for expression in field_expressions:
                    self._validate_braced_expression(expression, number)
                    try:
                        tree = ast.parse(expression, mode="eval")
                    except SyntaxError as error:
                        raise ParseError(
                            number, f"invalid operator expression: {error.msg}"
                        ) from error
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                            if node.value.id not in {"left", "right"}:
                                raise ParseError(
                                    number,
                                    "operator expressions may only reference left and right",
                                )
                            if node.attr not in fields:
                                raise ParseError(
                                    number,
                                    f"record '{record_name}' has no field '{node.attr}'",
                                )
                            continue
                        if isinstance(node, ast.Name) and node.id in fields:
                            raise ParseError(
                                number,
                                "operator expressions must reference fields through left or right",
                            )
                    normalized_fields.append(expression)
                self.operators[(symbol, record_name)] = tuple(normalized_fields)
                statements.append(
                    Statement(
                        "operator",
                        number,
                        {
                            "symbol": symbol,
                            "record": record_name,
                            "fields": tuple(normalized_fields),
                        },
                    )
                )
                continue
            if line.lower().startswith("operator"):
                raise ParseError(
                    number, "invalid operator; expected operator + Record = { expression, ... }"
                )

            var_match = _VAR_RE.fullmatch(line)
            if var_match:
                value, encoding = self._parse_value(var_match.group(2).strip(), number)
                if encoding is not None or value.get("kind") != "literal":
                    raise ParseError(number, "var declaration requires a quoted string literal")
                statements.append(
                    Statement(
                        "var_declare",
                        number,
                        {"name": var_match.group(1), "value": value},
                    )
                )
                continue
            if line.lower().startswith("var"):
                raise ParseError(number, 'invalid variable declaration; expected var name = {"text"}')

            start_match = _START_RE.fullmatch(line)
            if start_match:
                name = start_match.group(2)
                if self.index >= len(self.lines) or self.lines[self.index][1] != "{":
                    raise ParseError(number, "expected '{' after start declaration")
                self.index += 1
                body = self._sequence(in_block=True)
                else_body: list[Statement] = []
                if self.index < len(self.lines) and self.lines[self.index][1].lower() == "else":
                    else_line = self.lines[self.index][0]
                    self.index += 1
                    self._expect_open_brace(else_line)
                    else_body = self._sequence(in_block=True)
                statements.append(
                    Statement(
                        "start",
                        number,
                        {"mode": start_match.group(1).lower(), "name": name, "else_body": tuple(else_body)},
                        tuple(body),
                    )
                )
                continue
            if line.lower().startswith("start"):
                raise ParseError(number, "invalid start declaration")

            if not in_block and re.fullmatch(r"try\s*", line, re.I):
                self._expect_open_brace(number)
                try_body = self._sequence(in_block=True)
                catch_body: tuple[Statement, ...] = ()
                finally_body: tuple[Statement, ...] = ()
                if self.index < len(self.lines) and re.fullmatch(
                    r"catch\s*", self.lines[self.index][1], re.I
                ):
                    catch_line = self.lines[self.index][0]
                    self.index += 1
                    self._expect_open_brace(catch_line)
                    catch_body = self._sequence(in_block=True)
                if self.index < len(self.lines) and re.fullmatch(
                    r"finally\s*", self.lines[self.index][1], re.I
                ):
                    finally_line = self.lines[self.index][0]
                    self.index += 1
                    self._expect_open_brace(finally_line)
                    finally_body = self._sequence(in_block=True)
                if not catch_body and not finally_body:
                    raise ParseError(number, "try requires a catch or finally block")
                statements.append(
                    Statement(
                        "try",
                        number,
                        {"catch_body": catch_body, "finally_body": finally_body},
                        tuple(try_body),
                    )
                )
                continue
            if line.lower().startswith("try"):
                raise ParseError(number, "invalid try; expected try { ... } with catch or finally")
            if line.lower().startswith(("catch", "finally")):
                raise ParseError(number, f"'{line.split()[0].lower()}' must follow a try block")

            lock_match = _LOCK_RE.fullmatch(line)
            if lock_match:
                self._expect_open_brace(number)
                body = self._sequence(in_block=True)
                statements.append(
                    Statement(
                        "lock",
                        number,
                        {
                            "name": lock_match.group(1),
                            "timeout": float(lock_match.group(2)) if lock_match.group(2) else None,
                        },
                        tuple(body),
                    )
                )
                continue
            if line.lower().startswith("lock"):
                raise ParseError(number, "invalid lock; expected lock {block} [-timeout N]")

            bomb_match = _N_BOMB_RE.fullmatch(line)
            if bomb_match:
                statements.append(
                    Statement("n_bomb", number, {"target": bomb_match.group(1), "all": bomb_match.group(1).lower() == "all"})
                )
                continue
            if line.lower().startswith("n-bomb"):
                raise ParseError(number, "invalid n-bomb; expected n-bomb {all} or n-bomb {block}")

            neutron_match = _NEUTRON_RE.fullmatch(line)
            if neutron_match:
                statements.append(
                    Statement(
                        "neutron",
                        number,
                        {"parent": neutron_match.group(1), "child": neutron_match.group(2)},
                    )
                )
                continue
            if line.lower().startswith("neutron"):
                raise ParseError(number, "invalid neutron; expected neutron {parent} = {child}")

            proton_match = _PROTON_RE.fullmatch(line)
            if proton_match:
                blocks = [
                    block.strip()
                    for block in proton_match.group(2).split("->")
                    if block.strip()
                ]
                blocks = [
                    re.sub(r"^\{|\}$", "", block).strip()
                    for block in blocks
                ]
                if not blocks or any(not re.fullmatch(r"[A-Za-z_]\w*", block) for block in blocks):
                    raise ParseError(number, "proton chain must be a -> separated list of block names")
                statements.append(
                    Statement(
                        "proton",
                        number,
                        {"chain": proton_match.group(1), "blocks": tuple(blocks)},
                    )
                )
                continue
            if line.lower().startswith("proton"):
                raise ParseError(number, "invalid proton; expected proton {chain} = {a} -> {b}")

            reactor_match = _REACTOR_RE.fullmatch(line)
            if reactor_match:
                statements.append(
                    Statement(
                        "nuclear_reactor",
                        number,
                        {"parent": reactor_match.group(1), "chain": reactor_match.group(2)},
                    )
                )
                continue
            if line.lower().startswith("nuclear_reactor"):
                raise ParseError(number, "invalid nuclear_reactor; expected nuclear_reactor {parent} = {chain}")

            calldll_match = _CALLDLL_RE.fullmatch(line)
            if calldll_match:
                statements.append(
                    Statement("calldll", number, {"library": calldll_match.group(1)})
                )
                continue
            if line.lower().startswith("calldll"):
                raise ParseError(number, "invalid calldll; expected calldll = (library)")

            cdll_match = _CDLL_RE.fullmatch(line)
            if cdll_match:
                operation = cdll_match.group(1).lower()
                data: dict[str, object] = {
                    "operation": operation,
                    "library": cdll_match.group(2),
                }
                if operation == "come":
                    rest = cdll_match.group(3)
                    calls = re.findall(r"\(\s*(.*?)\s*\)", rest, re.S)
                    if not calls:
                        raise ParseError(number, "cdll = come requires a function name in parentheses")
                    function_name = calls[0].strip().lower()
                    if not re.fullmatch(r"[A-Za-z_]\w*", function_name):
                        raise ParseError(number, "cdll function name must be an identifier")
                    if function_name not in self.extern_functions:
                        raise ParseError(
                            number,
                            f"DLL function '{function_name}' has no #extern declaration",
                        )
                    arguments = calls[1:]
                    expected = len(self.extern_functions[function_name]["parameters"])  # type: ignore[arg-type]
                    if len(arguments) != expected:
                        raise ParseError(
                            number,
                            f"'{function_name}' expects {expected} argument(s), got {len(arguments)}",
                        )
                    for argument in arguments:
                        self._validate_braced_expression(argument, number)
                    data["function"] = function_name
                    data["arguments"] = tuple(arguments)
                elif cdll_match.group(3).strip():
                    raise ParseError(number, f"cdll = {operation} does not take arguments")
                statements.append(Statement("cdll", number, data))
                continue
            if line.lower().startswith("cdll"):
                raise ParseError(number, "invalid cdll; expected cdll = come/out/status (library)")

            while_match = _WHILE_RE.fullmatch(line)
            if while_match:
                condition = while_match.group(1).strip()
                self._validate_braced_expression(condition, number)
                self._expect_open_brace(number)
                body = self._sequence(in_block=True)
                statements.append(
                    Statement("while", number, {"condition": condition}, tuple(body))
                )
                continue
            if line.lower().startswith("while"):
                raise ParseError(number, "invalid while condition")

            if_block_match = _IF_BLOCK_RE.fullmatch(line)
            if if_block_match:
                condition = if_block_match.group(1).strip()
                self._validate_braced_expression(condition, number)
                self._expect_open_brace(number)
                body = self._sequence(in_block=True)
                otherwise: list[Statement] = []
                if self.index < len(self.lines) and self.lines[self.index][1].lower() == "else":
                    else_line = self.lines[self.index][0]
                    self.index += 1
                    self._expect_open_brace(else_line)
                    otherwise = self._sequence(in_block=True)
                statements.append(
                    Statement(
                        "if_expr",
                        number,
                        {"condition": condition},
                        tuple(body + [Statement("else_marker", number)] + otherwise),
                    )
                )
                continue

            dog_whitelist_match = _DOG_WHITELIST_RE.fullmatch(line)
            if dog_whitelist_match:
                clauses = re.findall(
                    r"\(\s*(stop|pass|ok|error|idle|running|stopped)\s*:\s*(stop|pass|ok|error|idle|running|stopped)\s*\)",
                    line[line.find("=") + 1 :],
                    re.I,
                )
                if not clauses:
                    raise ParseError(number, "dog whitelist must contain at least one status mapping")
                statuses = [status.lower() for status, _ in clauses]
                if len(set(statuses)) != len(statuses):
                    raise ParseError(number, "dog whitelist statuses must not be repeated")
                statements.append(
                    Statement(
                        "dog",
                        number,
                        {
                            "name": dog_whitelist_match.group(1),
                            "mappings": tuple(
                                (status.lower(), outcome.lower())
                                for status, outcome in clauses
                            ),
                        },
                    )
                )
                continue

            dog_match = _DOG_RE.fullmatch(line)
            if dog_match:
                statements.append(
                    Statement("dog", number, {"name": dog_match.group(1), "condition": dog_match.group(2).lower()})
                )
                continue
            if line.lower().startswith("dog"):
                raise ParseError(number, "invalid dog declaration or status whitelist")

            jump_match = _JUMP_RE.fullmatch(line)
            if jump_match:
                statements.append(
                    Statement("jump", number, {"line": int(jump_match.group(1))})
                )
                continue
            if line.lower().startswith("jump"):
                raise ParseError(number, "invalid jump; expected jump(source_line)")

            call_match = _CALL_RE.fullmatch(line)
            if call_match:
                statements.append(Statement("call", number, {"component": call_match.group(1).lower()}))
                continue
            if line.lower().startswith("call"):
                raise ParseError(number, "invalid call declaration")

            father_match = _FATHER_RE.fullmatch(line)
            if father_match:
                statements.append(Statement("father", number))
                continue
            if line.lower().startswith("father"):
                raise ParseError(number, "father must refer to up")

            require_ok_match = _REQUIRE_OK_RE.fullmatch(line)
            if require_ok_match:
                step = int(require_ok_match.group(1)) if require_ok_match.group(1) else 0
                statements.append(Statement("require_ok", number, {"step": step}))
                continue
            if line.lower().startswith("require_ok"):
                raise ParseError(number, "require_ok takes an optional step number: require_ok 2")

            if_match = _IF_RE.fullmatch(line)
            if if_match:
                selector = (if_match.group(1) or "status").strip().lower()
                target = self._parse_goto(if_match.group(3).strip(), number)
                statements.append(
                    Statement(
                        "if",
                        number,
                        {"selector": selector, "expected": if_match.group(2).strip(), "target": target},
                    )
                )
                continue
            if line.lower().startswith("if"):
                raise ParseError(number, "invalid if/goto expression")

            if re.fullmatch(r"[A-Za-z_]\w*", line):
                if was_hash_prefixed:
                    if line.lower() == "pe":
                        raise ParseError(
                            number,
                            "component 'pe' is not supported (removed in v6)",
                        )
                    statements.append(Statement("import", number, {"component": line.lower()}))
                else:
                    statements.append(Statement("nop", number, {"name": line}))
                continue

            push_match = _PUSH_RE.fullmatch(line)
            if push_match:
                value, encoding = self._parse_value(push_match.group(2).strip(), number)
                statements.append(
                    Statement("push", number, {"name": push_match.group(1), "value": value, "encoding": encoding})
                )
                continue

            encoding_match = _ENCODING_RE.fullmatch(line)
            if encoding_match:
                name, encoding = encoding_match.groups()
                statements.append(Statement("encoding", number, {"name": name, "encoding": encoding}))
                continue

            if line.lower().startswith("math_run"):
                math_match = _MATH_RE.fullmatch(line)
                if not math_match:
                    raise ParseError(number, "invalid math_run expression")
                expression = math_match.group(1).strip()
                self._validate_braced_expression(expression, number)
                statements.append(
                    Statement(
                        "math",
                        number,
                        {
                            "expression": expression,
                            "precision": math_match.group(2).lower() if math_match.group(2) else None,
                        },
                    )
                )
                continue

            bytes_match = _BYTES_RE.fullmatch(line)
            if bytes_match:
                expression = bytes_match.group(2).strip()
                self._validate_braced_expression(expression, number)
                statements.append(
                    Statement(
                        "bytes_declare",
                        number,
                        {"name": bytes_match.group(1), "expression": expression},
                    )
                )
                continue
            if line.lower().startswith("bytes ") :
                raise ParseError(number, "invalid bytes declaration; expected bytes name = [values]")

            bytes_call = _BYTES_CALL_RE.fullmatch(line)
            if bytes_call:
                operation = bytes_call.group(1).lower()
                arguments = [
                    argument.strip()
                    for argument in re.split(r",(?![^()]*\))", bytes_call.group(2))
                    if argument.strip()
                ]
                expected = {"set": 3, "get": 2, "len": 1}[operation]
                if len(arguments) != expected:
                    raise ParseError(
                        number,
                        f"bytes_{operation} expects {expected} argument(s)",
                    )
                for argument in arguments:
                    self._validate_braced_expression(argument, number)
                statements.append(
                    Statement(
                        "bytes_" + operation,
                        number,
                        {"arguments": tuple(arguments)},
                    )
                )
                continue
            if line.lower().startswith("bytes_set") or line.lower().startswith("bytes_get") or line.lower().startswith("bytes_len"):
                raise ParseError(number, "invalid bytes call")

            array_match = _ARRAY_RE.fullmatch(line)
            if array_match:
                expression = array_match.group(2).strip()
                self._validate_braced_expression(expression, number)
                statements.append(
                    Statement(
                        "array_declare",
                        number,
                        {"name": array_match.group(1), "expression": expression},
                    )
                )
                continue
            if line.lower().startswith("array"):
                raise ParseError(number, "invalid array declaration; expected array name = [values]")

            function_match = _FUNCTION_RE.fullmatch(line)
            if function_match:
                if in_block:
                    raise ParseError(
                        number,
                        "function declarations must be at the top level; "
                        "nested functions are only supported inside function bodies",
                    )
                name, raw_parameters, expression = function_match.groups()
                is_generic = line.lower().startswith("generic ")
                self._validate_braced_expression(expression, number)
                parameters = [
                    parameter.strip()
                    for parameter in raw_parameters.split(",")
                    if parameter.strip()
                ]
                if any(not re.fullmatch(r"[A-Za-z_]\w*", parameter) for parameter in parameters):
                    raise ParseError(number, "function parameters must be identifiers")
                if len(set(parameters)) != len(parameters):
                    raise ParseError(number, "function parameter names must be unique")
                declared_name = (
                    f"{self.namespace}__{name}" if self.namespace else name
                )
                self.function_parameters[declared_name.lower()] = tuple(parameters)
                statements.append(
                    Statement(
                        "function",
                        number,
                        {
                            "name": declared_name,
                            "symbol": name,
                            "parameters": parameters,
                            "expression": expression.strip(),
                            "generic": is_generic,
                        },
                    )
                )
                continue
            function_header = _FUNCTION_HEADER_RE.fullmatch(line)
            if function_header:
                if in_block:
                    raise ParseError(
                        number,
                        "function declarations must be at the top level; "
                        "nested functions are only supported inside function bodies",
                    )
                name, raw_parameters = function_header.groups()
                parameters = [
                    parameter.strip()
                    for parameter in raw_parameters.split(",")
                    if parameter.strip()
                ]
                if any(not re.fullmatch(r"[A-Za-z_]\w*", parameter) for parameter in parameters):
                    raise ParseError(number, "function parameters must be identifiers")
                if len(set(parameters)) != len(parameters):
                    raise ParseError(number, "function parameter names must be unique")
                is_generic = line.lower().startswith("generic ")
                if self.index >= len(self.lines) or self.lines[self.index][1] != "{":
                    raise ParseError(number, "expected '{' after function declaration")
                self.index += 1
                declared_name = f"{self.namespace}__{name}" if self.namespace else name
                self.function_parameters[declared_name.lower()] = tuple(parameters)
                if self._function_body_needs_statements():
                    body = self._parse_statement_function_body(parameters, number)
                    statements.append(
                        Statement(
                            "function",
                            number,
                            {
                                "name": declared_name,
                                "symbol": name,
                                "parameters": parameters,
                                "body": body,
                                "generic": is_generic,
                            },
                        )
                    )
                    continue
                locals_by_name: dict[str, str] = {}
                return_expression: str | None = None

                def normalize_function_expression(
                    expression: str,
                    expression_line: int,
                    additional_names: set[str] | None = None,
                ) -> str:
                    transformed, _ = transform_braced_expression(expression)
                    names = set(parameters) | set(locals_by_name) | (additional_names or set())
                    for local in sorted(names, key=len, reverse=True):
                        transformed = re.sub(
                            rf"\bvar_{re.escape(local)}\b", local, transformed
                        )
                    try:
                        tree = ast.parse(transformed, mode="eval")
                    except SyntaxError as error:
                        raise ParseError(
                            expression_line, f"invalid function expression: {error.msg}"
                        ) from error
                    captured = sorted(
                        {
                            node.id[4:]
                            for node in ast.walk(tree)
                            if isinstance(node, ast.Name)
                            and node.id.startswith("var_")
                        }
                    )
                    if captured:
                        raise ParseError(
                            expression_line,
                            "function bodies cannot access caller variables; pass values as parameters",
                        )
                    return transformed

                def parse_return_branch(
                    branch_line: int, branch_name: str
                ) -> str:
                    if self.index >= len(self.lines) or self.lines[self.index][1] != "{":
                        raise ParseError(branch_line, f"expected '{{' after function {branch_name}")
                    self.index += 1
                    branch_locals: dict[str, str] = {}
                    while self.index < len(self.lines):
                        return_line, branch_statement, _ = self.lines[self.index]
                        self.index += 1
                        if branch_statement == "}":
                            raise ParseError(
                                return_line,
                                "function conditional branches must end with a return expression",
                            )
                        local_match = _FUNCTION_LOCAL_RE.fullmatch(branch_statement)
                        if local_match:
                            local_name, expression = local_match.groups()
                            if (
                                local_name in parameters
                                or local_name in locals_by_name
                                or local_name in branch_locals
                            ):
                                raise ParseError(
                                    return_line, f"local '{local_name}' is already declared"
                                )
                            normalized = normalize_function_expression(
                                expression, return_line, set(branch_locals)
                            )
                            try:
                                tree = ast.parse(normalized, mode="eval")
                                for previous_name, previous_expression in (
                                    locals_by_name | branch_locals
                                ).items():
                                    tree = _ExpressionNameReplacer(
                                        previous_name, f"({previous_expression})"
                                    ).visit(tree)
                                branch_locals[local_name] = ast.unparse(
                                    ast.fix_missing_locations(tree)
                                )
                            except SyntaxError as error:
                                raise ParseError(
                                    return_line,
                                    f"invalid function local expression: {error.msg}",
                                ) from error
                            continue
                        conditional = _IF_BLOCK_RE.fullmatch(branch_statement)
                        if conditional:
                            visible_locals = locals_by_name | branch_locals
                            condition = normalize_function_expression(
                                conditional.group(1).strip(),
                                return_line,
                                set(branch_locals),
                            )
                            try:
                                condition_tree = ast.parse(condition, mode="eval")
                                for previous_name, previous_expression in visible_locals.items():
                                    condition_tree = _ExpressionNameReplacer(
                                        previous_name, f"({previous_expression})"
                                    ).visit(condition_tree)
                                condition = ast.unparse(
                                    ast.fix_missing_locations(condition_tree)
                                )
                            except SyntaxError as error:
                                raise ParseError(
                                    return_line,
                                    f"invalid function condition: {error.msg}",
                                ) from error
                            nested_then = parse_return_branch(return_line, "if")
                            if (
                                self.index >= len(self.lines)
                                or self.lines[self.index][1].lower() != "else"
                            ):
                                raise ParseError(
                                    return_line,
                                    "function if requires an else branch and both branches must return",
                                )
                            self.index += 1
                            nested_else_line = self.lines[self.index - 1][0]
                            nested_else = parse_return_branch(
                                nested_else_line, "else"
                            )
                            branch_expression = (
                                f"choose(({condition}), ({nested_then}), ({nested_else}))"
                            )
                            if (
                                self.index >= len(self.lines)
                                or self.lines[self.index][1] != "}"
                            ):
                                raise ParseError(
                                    return_line,
                                    "function conditional branches must contain only local lets before return",
                                )
                            self.index += 1
                            return branch_expression
                        branch_return = _RETURN_RE.fullmatch(branch_statement)
                        if not branch_return:
                            raise ParseError(
                                return_line,
                                "function conditional branches accept local let statements and one return",
                            )
                        if self.index >= len(self.lines) or self.lines[self.index][1] != "}":
                            raise ParseError(
                                return_line,
                                "function conditional branches must contain only local lets before return",
                            )
                        self.index += 1
                        normalized = normalize_function_expression(
                            branch_return.group(1).strip(),
                            return_line,
                            set(branch_locals),
                        )
                        try:
                            tree = ast.parse(normalized, mode="eval")
                            for previous_name, previous_expression in (
                                locals_by_name | branch_locals
                            ).items():
                                tree = _ExpressionNameReplacer(
                                    previous_name, f"({previous_expression})"
                                ).visit(tree)
                            return ast.unparse(ast.fix_missing_locations(tree))
                        except SyntaxError as error:
                            raise ParseError(
                                return_line,
                                f"invalid function return expression: {error.msg}",
                            ) from error
                    raise ParseError(branch_line, "unterminated function return branch")

                while self.index < len(self.lines):
                    body_line_number, body_line, _ = self.lines[self.index]
                    self.index += 1
                    if body_line == "}":
                        break
                    if return_expression is not None:
                        raise ParseError(body_line_number, "return must be the final statement in a function")
                    local_match = _FUNCTION_LOCAL_RE.fullmatch(body_line)
                    if local_match:
                        local_name, expression = local_match.groups()
                        if local_name in parameters or local_name in locals_by_name:
                            raise ParseError(body_line_number, f"local '{local_name}' is already declared")
                        self._validate_braced_expression(expression, body_line_number)
                        try:
                            tree = ast.parse(
                                normalize_function_expression(expression, body_line_number),
                                mode="eval",
                            )
                            for previous_name, previous_expression in locals_by_name.items():
                                replacement = f"({previous_expression})"
                                tree = _ExpressionNameReplacer(previous_name, replacement).visit(tree)
                            locals_by_name[local_name] = ast.unparse(ast.fix_missing_locations(tree))
                        except SyntaxError as error:
                            raise ParseError(body_line_number, f"invalid function local expression: {error.msg}") from error
                        continue
                    conditional = _IF_BLOCK_RE.fullmatch(body_line)
                    if conditional:
                        if return_expression is not None:
                            raise ParseError(
                                body_line_number,
                                "return must be the final statement in a function",
                            )
                        condition = normalize_function_expression(
                            conditional.group(1).strip(), body_line_number
                        )
                        try:
                            condition_tree = ast.parse(condition, mode="eval")
                            for previous_name, previous_expression in locals_by_name.items():
                                condition_tree = _ExpressionNameReplacer(
                                    previous_name, f"({previous_expression})"
                                ).visit(condition_tree)
                            condition = ast.unparse(
                                ast.fix_missing_locations(condition_tree)
                            )
                        except SyntaxError as error:
                            raise ParseError(
                                body_line_number,
                                f"invalid function condition: {error.msg}",
                            ) from error
                        then_expression = parse_return_branch(body_line_number, "if")
                        if (
                            self.index >= len(self.lines)
                            or self.lines[self.index][1].lower() != "else"
                        ):
                            raise ParseError(
                                body_line_number,
                                "function if requires an else branch and both branches must return",
                            )
                        self.index += 1
                        else_line = self.lines[self.index - 1][0]
                        else_expression = parse_return_branch(else_line, "else")
                        return_expression = (
                            f"choose(({condition}), ({then_expression}), ({else_expression}))"
                        )
                        continue
                    return_match = _RETURN_RE.fullmatch(body_line)
                    if return_match:
                        return_expression = return_match.group(1).strip()
                        self._validate_braced_expression(return_expression, body_line_number)
                        try:
                            tree = ast.parse(
                                normalize_function_expression(
                                    return_expression, body_line_number
                                ),
                                mode="eval",
                            )
                            for previous_name, previous_expression in locals_by_name.items():
                                tree = _ExpressionNameReplacer(
                                    previous_name, f"({previous_expression})"
                                ).visit(tree)
                            return_expression = ast.unparse(ast.fix_missing_locations(tree))
                        except SyntaxError as error:
                            raise ParseError(body_line_number, f"invalid return expression: {error.msg}") from error
                        continue
                    raise ParseError(body_line_number, "function bodies currently accept 'let name = expression' and 'return expression'")
                else:
                    raise ParseError(number, "unterminated function body")
                if return_expression is None:
                    raise ParseError(number, "function body must end with return expression")
                statements.append(
                    Statement(
                        "function",
                        number,
                        {
                            "name": declared_name,
                            "symbol": name,
                            "parameters": parameters,
                            "expression": return_expression,
                            "generic": is_generic,
                        },
                    )
                )
                continue
            if line.lower().startswith("return"):
                raise ParseError(number, "return is only valid inside a function")
            if line.lower().startswith(("function", "generic function")):
                raise ParseError(number, "invalid function definition")

            world_match = re.fullmatch(
                r"=?\s*the_world\s*\(\s*(stop|force|start)\s*\)\s*", line, re.I
            )
            if world_match:
                statements.append(
                    Statement(
                        "the_world",
                        number,
                        {"action": world_match.group(1).lower()},
                    )
                )
                continue
            if line.lower().startswith(("the_world", "= the_world", "=the_world")):
                raise ParseError(
                    number, "invalid The_world; expected The_world (stop|force|start)"
                )
            pin_match = re.fullmatch(
                r"gc_(pin|unpin)\s*\(\s*\{?\s*([A-Za-z_]\w*)\s*\}?\s*\)\s*", line, re.I
            )
            if pin_match:
                statements.append(
                    Statement(
                        "gc_pin",
                        number,
                        {
                            "name": pin_match.group(2),
                            "pinned": pin_match.group(1).lower() == "pin",
                        },
                    )
                )
                continue
            if line.lower().startswith(("gc_pin", "gc_unpin")):
                raise ParseError(
                    number, "invalid gc_pin; expected gc_pin ({name}) or gc_unpin ({name})"
                )

            exit_match = _EXIT_RE.fullmatch(line)
            if exit_match:
                statements.append(Statement("exit", number, {"code": int(exit_match.group(1))}))
                continue
            if line.lower().startswith("exit"):
                raise ParseError(number, "invalid exit; expected exit(code)")

            sleep_match = _SLEEP_RE.fullmatch(line)
            if sleep_match:
                statements.append(Statement("sleep", number, {"seconds": float(sleep_match.group(1))}))
                continue
            if line.lower().startswith("sleep"):
                raise ParseError(number, "invalid sleep expression")

            ask_match = _ASK_RE.fullmatch(line)
            if ask_match:
                prompt = ask_match.group("prompt_first") or ask_match.group("prompt_second")
                prompt_value = self._parse_value(prompt.strip(), number)[0] if prompt else None
                statements.append(
                    Statement(
                        "ask",
                        number,
                        {
                            "name": ask_match.group("name"),
                            "prompt": prompt_value,
                            # -secret 让读取时不回显输入内容
                            "secret": bool(ask_match.group("secret")),
                        },
                    )
                )
                continue
            if line.lower().startswith("ask"):
                raise ParseError(
                    number,
                    "invalid ask; expected ask ({name}) or ask ({name}) (<{prompt}>)"
                    " with optional -secret",
                )

            print_match = _PRINT_RE.fullmatch(line)
            if print_match:
                expression = print_match.group(1).strip()
                encoding = print_match.group(2)
                data: dict[str, object] = {"expression": expression, "encoding": encoding}
                if expression.startswith("{") and expression.endswith("}"):
                    data["value"] = self._parse_value(expression, number)[0]
                statements.append(
                    Statement("print", number, data)
                )
                continue
            if line.lower().startswith("print"):
                raise ParseError(number, "invalid print expression")

            kill_match = _KILL_RE.fullmatch(line)
            if kill_match:
                statements.append(Statement(
                    "kill", number,
                    {"name": kill_match.group(1), "force": bool(kill_match.group(2))},
                ))
                continue
            if line.lower().startswith("kill"):
                raise ParseError(number, "invalid kill expression")

            tape_create_match = _TAPE_CREATE_RE.fullmatch(line)
            if tape_create_match:
                statements.append(Statement("tape_create", number, {"name": tape_create_match.group(1)}))
                continue
            tape_move_match = _TAPE_MOVE_RE.fullmatch(line)
            if tape_move_match:
                statements.append(
                    Statement(
                        "tape_move",
                        number,
                        {"name": tape_move_match.group(1), "direction": tape_move_match.group(2).lower()},
                    )
                )
                continue
            tape_write_match = _TAPE_WRITE_RE.fullmatch(line)
            if tape_write_match:
                symbol = tape_write_match.group(2)
                if not re.fullmatch(r"-?\d+", symbol):
                    raise ParseError(number, "tape cells currently accept integer symbols")
                statements.append(
                    Statement(
                        "tape_write",
                        number,
                        {"name": tape_write_match.group(1), "symbol": int(symbol)},
                    )
                )
                continue
            tape_read_match = _TAPE_READ_RE.fullmatch(line)
            if tape_read_match:
                statements.append(Statement("tape_read", number, {"name": tape_read_match.group(1)}))
                continue

            if line.startswith("push"):
                raise ParseError(number, "invalid push expression")
            command = self._parse_command_assignment(line, number)
            if command is not None:
                statements.append(command)
                continue

            raise ParseError(number, f"unrecognized statement: {line}")

        if in_block:
            raise ParseError(self.lines[-1][0] if self.lines else 1, "unterminated execution block")
        return statements

    def _function_body_needs_statements(self) -> bool:
        depth = 0
        top_level: list[str] = []
        index = self.index
        while index < len(self.lines):
            text = self.lines[index][1]
            if text == "}":
                if depth == 0:
                    break
                depth -= 1
            elif text == "{":
                depth += 1
            else:
                lowered = text.lower()
                if lowered.startswith(
                    ("set ", "while ", "while(", "break", "continue", "function ", "generic function ")
                ):
                    return True
                if self.records and any(
                    re.search(rf"\b{re.escape(record)}\b", text) for record in self.records
                ):
                    return True
                if depth == 0:
                    top_level.append(lowered)
            index += 1
        for position, text in enumerate(top_level):
            if _IF_BLOCK_RE.fullmatch(text):
                following = top_level[position + 1] if position + 1 < len(top_level) else ""
                if following != "else":
                    return True
            if _RETURN_RE.fullmatch(text) and position != len(top_level) - 1:
                return True
        return False

    def _parse_statement_function_body(
        self, parameters: list[str], header_line: int
    ) -> tuple[Statement, ...]:
        return self._parse_function_statements(
            parameters,
            set(),
            {},
            loop_depth=0,
            owner_line=header_line,
            nested=False,
        )

    def _parse_function_statements(
        self,
        parameters: list[str],
        declared: set[str],
        record_locals: dict[str, str],
        *,
        loop_depth: int,
        owner_line: int,
        nested: bool,
    ) -> tuple[Statement, ...]:
        statements: list[Statement] = []
        terminated = False
        while self.index < len(self.lines):
            number, text, _ = self.lines[self.index]
            self.index += 1
            if text == "}":
                return tuple(statements)
            if terminated:
                raise ParseError(number, "return must be the final statement in a function")
            nested_match = _FUNCTION_RE.fullmatch(text)
            if nested_match:
                nested_name, raw_parameters, expression = nested_match.groups()
                nested_parameters = [
                    parameter.strip()
                    for parameter in raw_parameters.split(",")
                    if parameter.strip()
                ]
                if any(
                    not re.fullmatch(r"[A-Za-z_]\w*", parameter)
                    for parameter in nested_parameters
                ):
                    raise ParseError(number, "function parameters must be identifiers")
                if len(set(nested_parameters)) != len(nested_parameters):
                    raise ParseError(number, "function parameter names must be unique")
                if nested_name in parameters or nested_name in declared:
                    raise ParseError(number, f"local '{nested_name}' is already declared")
                normalized, captures = self._nested_function_expression(
                    expression.strip(), number, parameters, declared, nested_parameters
                )
                declared.add(nested_name)
                statements.append(
                    Statement(
                        "function_nested",
                        number,
                        {
                            "name": nested_name,
                            "parameters": nested_parameters,
                            "expression": normalized,
                            "captures": captures,
                        },
                    )
                )
                continue
            if text.lower().startswith(("function ", "generic function")):
                raise ParseError(
                    number,
                    "nested functions must use the single-line form function name(params) = expression",
                )
            local_match = _FUNCTION_LOCAL_RE.fullmatch(text)
            if local_match:
                name, expression = local_match.groups()
                if nested:
                    raise ParseError(
                        number,
                        "declare function local variables before any if or while statement",
                    )
                if name in parameters or name in declared:
                    raise ParseError(number, f"local '{name}' is already declared")
                value = self._function_body_expression(
                    expression.strip(),
                    number,
                    parameters,
                    declared,
                    record_locals,
                    allow_record=True,
                )
                declared.add(name)
                record_temps: list[Statement] = []
                record_type, field_expressions, copy_source = self._record_local_value(
                    value, number, record_locals, record_temps
                )
                statements.extend(record_temps)
                if record_type is not None:
                    record_locals[name] = record_type
                    statements.append(
                        Statement(
                            "function_record_local",
                            number,
                            {
                                "name": name,
                                "record": record_type,
                                "fields": field_expressions,
                                "record_fields": self.records[record_type],
                            },
                        )
                    )
                elif copy_source is not None:
                    record_locals[name] = record_locals[copy_source]
                    statements.append(
                        Statement(
                            "function_record_copy",
                            number,
                            {
                                "name": name,
                                "source": copy_source,
                                "record_fields": self.records[record_locals[copy_source]],
                            },
                        )
                    )
                else:
                    statements.append(
                        Statement("function_local", number, {"name": name, "expression": value})
                    )
                continue
            assignment = _FUNCTION_SET_RE.fullmatch(text)
            if assignment:
                name, expression = assignment.groups()
                name = re.sub(r"\s+", "", name)
                base, _, field = name.partition(".")
                if field:
                    if base not in record_locals:
                        raise ParseError(
                            number, f"unknown record local '{base}'; declare it with let first"
                        )
                    fields = self.records[record_locals[base]]
                    if field not in fields:
                        raise ParseError(
                            number,
                            f"record '{record_locals[base]}' has no field '{field}'",
                        )
                    value = self._function_body_expression(
                        expression.strip(), number, parameters, declared, record_locals
                    )
                    statements.append(
                        Statement(
                            "function_record_set",
                            number,
                            {"name": base, "field": field, "expression": value},
                        )
                    )
                    continue
                if name in record_locals:
                    source = self._function_body_expression(
                        expression.strip(),
                        number,
                        parameters,
                        declared,
                        record_locals,
                        allow_record=True,
                    )
                    try:
                        source_tree = ast.parse(source, mode="eval").body
                    except SyntaxError as error:
                        raise ParseError(
                            number, f"invalid expression: {error.msg}"
                        ) from error
                    if not (
                        isinstance(source_tree, ast.Name)
                        and source_tree.id in record_locals
                    ):
                        raise ParseError(
                            number,
                            f"record '{name}' can only be assigned another record value",
                        )
                    if record_locals[source_tree.id] != record_locals[name]:
                        raise ParseError(
                            number,
                            f"record '{name}' is {record_locals[name]} but '{source_tree.id}' "
                            f"is {record_locals[source_tree.id]}",
                        )
                    statements.append(
                        Statement(
                            "function_record_assign",
                            number,
                            {
                                "name": name,
                                "source": source_tree.id,
                                "record_fields": self.records[record_locals[name]],
                            },
                        )
                    )
                    continue
                if name not in parameters and name not in declared:
                    raise ParseError(
                        number, f"unknown local '{name}'; declare it with let first"
                    )
                value = self._function_body_expression(
                    expression.strip(), number, parameters, declared, record_locals
                )
                statements.append(
                    Statement("function_set", number, {"name": name, "expression": value})
                )
                continue
            if _FUNCTION_BREAK_RE.fullmatch(text):
                if loop_depth == 0:
                    raise ParseError(number, "break is only valid inside a function while loop")
                statements.append(Statement("function_break", number, {}))
                continue
            if _FUNCTION_CONTINUE_RE.fullmatch(text):
                if loop_depth == 0:
                    raise ParseError(
                        number, "continue is only valid inside a function while loop"
                    )
                statements.append(Statement("function_continue", number, {}))
                continue
            while_match = _FUNCTION_WHILE_RE.fullmatch(text)
            if while_match:
                condition = self._function_body_expression(
                    while_match.group(1).strip(), number, parameters, declared, record_locals
                )
                if self.index >= len(self.lines) or self.lines[self.index][1] != "{":
                    raise ParseError(number, "expected '{' after function while")
                self.index += 1
                body = self._parse_function_statements(
                    parameters,
                    declared,
                    record_locals,
                    loop_depth=loop_depth + 1,
                    owner_line=owner_line,
                    nested=True,
                )
                statements.append(
                    Statement("function_while", number, {"condition": condition}, body)
                )
                continue
            conditional = _IF_BLOCK_RE.fullmatch(text)
            if conditional:
                condition = self._function_body_expression(
                    conditional.group(1).strip(), number, parameters, declared, record_locals
                )
                if self.index >= len(self.lines) or self.lines[self.index][1] != "{":
                    raise ParseError(number, "expected '{' after function if")
                self.index += 1
                then_body = self._parse_function_statements(
                    parameters,
                    declared,
                    record_locals,
                    loop_depth=loop_depth,
                    owner_line=owner_line,
                    nested=True,
                )
                else_body: tuple[Statement, ...] = ()
                if self.index < len(self.lines) and self.lines[self.index][1].lower() == "else":
                    else_line = self.lines[self.index][0]
                    self.index += 1
                    if self.index >= len(self.lines) or self.lines[self.index][1] != "{":
                        raise ParseError(else_line, "expected '{' after function else")
                    self.index += 1
                    else_body = self._parse_function_statements(
                        parameters,
                        declared,
                        record_locals,
                        loop_depth=loop_depth,
                        owner_line=owner_line,
                        nested=True,
                    )
                statements.append(
                    Statement(
                        "function_if",
                        number,
                        {"condition": condition, "else_body": else_body},
                        then_body,
                    )
                )
                continue
            returned = _RETURN_RE.fullmatch(text)
            if returned:
                value = self._function_body_expression(
                    returned.group(1).strip(), number, parameters, declared, record_locals
                )
                statements.append(
                    Statement("function_return", number, {"expression": value})
                )
                terminated = True
                continue
            raise ParseError(
                number,
                "function body accepts let, set, if, while, break, continue, and return statements",
            )
        raise ParseError(owner_line, "unterminated function body")

    def _nested_function_expression(
        self,
        expression: str,
        line: int,
        outer_parameters: list[str],
        outer_locals: set[str],
        nested_parameters: list[str],
    ) -> tuple[str, tuple[str, ...]]:
        self._validate_braced_expression(expression, line)
        transformed, _ = transform_braced_expression(expression)
        visible = (set(outer_parameters) | outer_locals) - set(nested_parameters)
        for name in sorted(set(nested_parameters) | visible, key=len, reverse=True):
            transformed = re.sub(rf"\bvar_{re.escape(name)}\b", name, transformed)
        try:
            tree = ast.parse(transformed, mode="eval")
        except SyntaxError as error:
            raise ParseError(line, f"invalid function expression: {error.msg}") from error
        captured = sorted(
            {
                node.id[4:]
                for node in ast.walk(tree)
                if isinstance(node, ast.Name) and node.id.startswith("var_")
            }
        )
        if captured:
            raise ParseError(
                line,
                "function bodies cannot access caller variables; pass values as parameters",
            )
        captures = tuple(
            sorted(
                {
                    node.id
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Name) and node.id in visible
                }
            )
        )
        return transformed, captures

    def _function_body_expression(
        self,
        expression: str,
        line: int,
        parameters: list[str],
        declared: set[str],
        record_locals: dict[str, str] | None = None,
        *,
        allow_record: bool = False,
    ) -> str:
        record_locals = record_locals or {}
        self._validate_braced_expression(expression, line)
        transformed, _ = transform_braced_expression(expression)
        for name in sorted(set(parameters) | declared, key=len, reverse=True):
            transformed = re.sub(rf"\bvar_{re.escape(name)}\b", name, transformed)
        for name in sorted(record_locals, key=len, reverse=True):
            fields = self.records[record_locals[name]]

            def replace_field(match: re.Match[str]) -> str:
                field = match.group(1)
                if field not in fields:
                    raise ParseError(
                        line,
                        f"record '{record_locals[name]}' has no field '{field}'",
                    )
                return f"{name}__{field}"

            transformed = re.sub(
                rf"\b{re.escape(name)}\s*\.\s*([A-Za-z_]\w*)", replace_field, transformed
            )
        try:
            tree = ast.parse(transformed, mode="eval")
        except SyntaxError as error:
            raise ParseError(line, f"invalid function expression: {error.msg}") from error
        captured = sorted(
            {
                node.id[4:]
                for node in ast.walk(tree)
                if isinstance(node, ast.Name) and node.id.startswith("var_")
            }
        )
        if captured:
            raise ParseError(
                line,
                "function bodies cannot access caller variables; pass values as parameters",
            )
        whole_records = sorted(
            {
                node.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Name) and node.id in record_locals
            }
        )
        if whole_records and not allow_record:
            raise ParseError(
                line,
                f"record '{whole_records[0]}' cannot be used as a scalar value; use {whole_records[0]}.field",
            )
        return transformed

    def _record_operand(
        self,
        node: ast.expr,
        line: int,
        record_locals: dict[str, str],
        temps: list[Statement],
    ) -> tuple[str | None, str | None]:
        if isinstance(node, ast.Name) and node.id in record_locals:
            return node.id, record_locals[node.id]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            record_type = next(
                (name for name in self.records if name == node.func.id), None
            )
            if record_type is None:
                return None, None
            if node.keywords:
                raise ParseError(line, "record constructors do not accept keywords")
            fields = self.records[record_type]
            if len(node.args) != len(fields):
                raise ParseError(
                    line,
                    f"record '{record_type}' expects {len(fields)} value(s), got {len(node.args)}",
                )
            self.record_temp_count += 1
            temp = f"__record_{self.record_temp_count}"
            temps.append(
                Statement(
                    "function_record_local",
                    line,
                    {
                        "name": temp,
                        "record": record_type,
                        "fields": [
                            ast.unparse(ast.fix_missing_locations(argument))
                            for argument in node.args
                        ],
                        "record_fields": fields,
                    },
                )
            )
            record_locals[temp] = record_type
            return temp, record_type
        return None, None

    def _record_local_value(
        self,
        expression: str,
        line: int,
        record_locals: dict[str, str],
        temps: list[Statement] | None = None,
    ) -> tuple[str | None, list[str] | None, str | None]:
        temps = temps if temps is not None else []
        try:
            tree = ast.parse(expression, mode="eval").body
        except SyntaxError:
            return None, None, None
        if isinstance(tree, ast.Call) and isinstance(tree.func, ast.Name):
            record_type = next(
                (name for name in self.records if name == tree.func.id), None
            )
            if record_type is not None:
                if tree.keywords:
                    raise ParseError(line, "record constructors do not accept keywords")
                fields = self.records[record_type]
                if len(tree.args) != len(fields):
                    raise ParseError(
                        line,
                        f"record '{record_type}' expects {len(fields)} value(s), got {len(tree.args)}",
                    )
                return (
                    record_type,
                    [
                        ast.unparse(ast.fix_missing_locations(argument))
                        for argument in tree.args
                    ],
                    None,
                )
        if isinstance(tree, ast.BinOp) and isinstance(
            tree.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)
        ):
            left_name, left_type = self._record_operand(
                tree.left, line, record_locals, temps
            )
            right_name, right_type = self._record_operand(
                tree.right, line, record_locals, temps
            )
            if left_name is None or right_name is None:
                raise ParseError(
                    line,
                    "record operators require record locals or record constructors on both sides",
                )
            if left_type != right_type:
                raise ParseError(
                    line,
                    f"record operator mixes '{left_type}' and '{right_type}'",
                )
            symbol = {
                ast.Add: "+",
                ast.Sub: "-",
                ast.Mult: "*",
                ast.Div: "/",
            }[type(tree.op)]
            declared = self.operators.get((symbol, left_type))
            if declared is None:
                raise ParseError(
                    line,
                    f"record '{left_type}' has no operator '{symbol}'",
                )
            fields: list[str] = []
            for expression in declared:
                rendered = expression
                for side, name in (("left", left_name), ("right", right_name)):
                    for field in self.records[left_type]:
                        rendered = re.sub(
                            rf"\b{side}\s*\.\s*{re.escape(field)}\b",
                            f"{name}__{field}",
                            rendered,
                        )
                fields.append(rendered)
            return left_type, fields, None
        if isinstance(tree, ast.Name) and tree.id in record_locals:
            return None, None, tree.id
        return None, None, None

    def _expect_open_brace(self, line: int) -> None:
        if self.index >= len(self.lines) or self.lines[self.index][1] != "{":
            raise ParseError(line, "expected '{' after control-flow condition")
        self.index += 1

    def _validate_braced_expression(self, expression: str, line: int) -> None:
        try:
            transform_braced_expression(expression)
        except ValueError as error:
            raise ParseError(line, f"invalid braced expression: {error}") from error

    def _parse_goto(self, target: str, line: int) -> dict[str, object]:
        if re.fullmatch(r"to\s*=\s*stop", target, re.I):
            return {"kind": "stop"}
        push_match = re.fullmatch(r"push\s*\((.*)\)", target, re.I)
        if push_match:
            assignment = _PUSH_RE.fullmatch(f"push ({push_match.group(1)})")
            if assignment:
                value, encoding = self._parse_value(assignment.group(2).strip(), line)
                return {
                    "kind": "push",
                    "name": assignment.group(1),
                    "value": value,
                    "encoding": encoding,
                }
        raise ParseError(line, f"unsupported goto target: {target}")

    def _parse_value(self, text: str, line: int) -> tuple[dict[str, object], str | None]:
        encoding: str | None = None
        encoding_match = re.search(r"\s+-([A-Za-z0-9_-]+)\s*$", text)
        if encoding_match:
            encoding = encoding_match.group(1)
            text = text[: encoding_match.start()].strip()
        if text.startswith("[") or re.match(
            r"(?:array_set|array_push|matrix_mul|matrix_transpose|"
            r"matrix2_inverse|matrix3_inverse|solve_linear)\s*\(",
            text,
            re.I,
        ):
            try:
                transform_braced_expression(text)
            except ValueError as error:
                raise ParseError(line, f"invalid array value expression: {error}") from error
            return {"kind": "array_expression", "expression": text}, encoding
        if _IN_RE.fullmatch(text):
            return {"kind": "up"}, encoding
        parts: list[dict[str, str]] = []
        cursor = 0
        while cursor < len(text):
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            if cursor >= len(text) or text[cursor] != "{":
                parts = []
                break
            start = cursor + 1
            end = start
            quoted = end < len(text) and text[end] == '"'
            escaped = False
            if quoted:
                end += 1
            while end < len(text):
                character = text[end]
                if quoted:
                    if escaped:
                        escaped = False
                    elif character == "\\":
                        escaped = True
                    elif character == '"':
                        quoted = False
                elif character == "}":
                    break
                elif character in {'{', '"'}:
                    raise ParseError(line, "invalid braced string or variable reference")
                end += 1
            if end >= len(text):
                raise ParseError(line, "missing closing brace in value")
            content = text[start:end]
            content = content.strip()
            if content.startswith('"'):
                try:
                    literal = json.loads(content)
                except json.JSONDecodeError as error:
                    raise ParseError(line, "invalid quoted string literal") from error
                if not isinstance(literal, str):
                    raise ParseError(line, "quoted string literal must contain text")
                parts.append({"kind": "literal", "value": literal})
            elif re.fullmatch(r"\(\s*in\s*=\s*up\s*\)", content, re.I):
                parts.append({"kind": "up"})
            elif re.fullmatch(r"[A-Za-z_]\w*", content):
                parts.append({"kind": "variable", "name": content})
            else:
                raise ParseError(
                    line,
                    'braces must contain a variable name, {"quoted text"}, or (in = up)',
                )
            cursor = end + 1
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            and_match = re.match(r"\(\s*and\s*\)", text[cursor:], re.I)
            if and_match:
                cursor += and_match.end()
                if cursor >= len(text) or not text[cursor:].strip():
                    raise ParseError(line, "expected a value after (and)")

        if len(parts) == 1 and not text[cursor:].strip():
            return parts[0], encoding
        if len(parts) >= 2 and not text[cursor:].strip():
            return {"kind": "concat", "parts": parts}, encoding
        raise ParseError(line, f"unsupported value expression: {text}")

    def _parse_command_assignment(self, line: str, number: int) -> Statement | None:
        match = _ASSIGN_COMMAND_RE.fullmatch(line)
        if not match:
            return None
        name, expression = match.groups()
        bash_match = re.search(r"\bbash\s*=\s*(.+?)(?:\s*\)\s*)*$", expression, re.I)
        if not bash_match:
            return None
        command = bash_match.group(1).strip()
        command = re.sub(r"\)+\s*$", "", command).strip()
        if not command:
            raise ParseError(number, "bash command cannot be empty")
        return Statement("command", number, {"name": name, "command": command})


def parse(source: str, *, line_map: list[int] | None = None) -> Program:
    """Parse a Cosmic ASM source string into a small, line-aware program.

    ``line_map`` 由 HOP 重写层提供：把生成行号映射回用户原始行号，
    使展开后的语句报错仍指向用户写的那一行。
    """
    if line_map is None:
        try:
            source, line_map = hop.rewrite(source)
        except hop.HopError as error:
            raise ParseError(error.line, error.message) from error
    return _Parser(source, line_map).parse()
