from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .expression_syntax import transform_braced_expression

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
_VAR_RE = re.compile(r"var\s+([A-Za-z_]\w*)\s*=\s*(.+)\s*$", re.I)
_ENCODING_RE = re.compile(r"([A-Za-z_]\w*)\s*=\s*([A-Za-z0-9_-]+)\s*$", re.I)
_SLEEP_RE = re.compile(r"sleep\s*\(\s*(\d+(?:\.\d+)?)\s*\)\s*(?:\{\s*s\s*\})?", re.I)
_EXIT_RE = re.compile(r"exit\s*\(\s*(\d+)\s*\)", re.I)
_KILL_RE = re.compile(r"kill\s*\{\s*([A-Za-z_]\w*)\s*\}(?:\s+-force)?", re.I)
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
    r"function\s+([A-Za-z_]\w*)\s*\((.*?)\)\s*=\s*(.+)\s*$",
    re.I,
)


class _Parser:
    def __init__(self, source: str) -> None:
        self.lines: list[tuple[int, str, bool]] = []
        self.math_mode = "float64"
        math_mode_declared = False
        self.output_precision = 15
        output_precision_declared = False
        self.extern_functions: dict[str, dict[str, object]] = {}
        self.link_libraries: list[str] = []
        for number, original in enumerate(source.splitlines(), 1):
            line = original.strip()
            if not line:
                continue
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
        return Program(
            tuple(statements),
            self.math_mode,
            self.output_precision,
            extern_functions,
            tuple(self.link_libraries),
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
                name, raw_parameters, expression = function_match.groups()
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
                statements.append(
                    Statement(
                        "function",
                        number,
                        {"name": name, "parameters": parameters, "expression": expression.strip()},
                    )
                )
                continue
            if line.lower().startswith("function"):
                raise ParseError(number, "invalid function definition")

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
                statements.append(Statement("kill", number, {"name": kill_match.group(1)}))
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


def parse(source: str) -> Program:
    """Parse a Cosmic ASM source string into a small, line-aware program."""
    return _Parser(source).parse()
