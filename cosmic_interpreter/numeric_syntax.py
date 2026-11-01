from __future__ import annotations

import re


_RAG_START_RE = re.compile(r"\brag\s*(?=\()", re.I)


def _read_rag_argument(expression: str, open_paren: int, expected_name: str) -> tuple[str, int]:
    cursor = open_paren + 1
    while cursor < len(expression) and expression[cursor].isspace():
        cursor += 1

    name_match = re.match(r"[A-Za-z_]\w*", expression[cursor:])
    if name_match is None or name_match.group(0).lower() != expected_name:
        raise ValueError(f"rag syntax must specify '{expected_name} : expression'")
    cursor += len(name_match.group(0))
    while cursor < len(expression) and expression[cursor].isspace():
        cursor += 1
    if cursor >= len(expression) or expression[cursor] != ":":
        raise ValueError(f"rag syntax must specify '{expected_name} : expression'")
    cursor += 1
    value_start = cursor
    parentheses = 0
    braces = 0

    while cursor < len(expression):
        character = expression[cursor]
        if character == "{":
            braces += 1
        elif character == "}" and braces:
            braces -= 1
        elif not braces and character == "(":
            parentheses += 1
        elif not braces and character == ")":
            if parentheses == 0:
                value = expression[value_start:cursor].strip()
                if not value:
                    raise ValueError(f"rag {expected_name} cannot be empty")
                return value, cursor + 1
            parentheses -= 1
        cursor += 1

    raise ValueError(f"rag {expected_name} argument is missing ')'")


def normalize_rag_calls(expression: str) -> str:
    parts: list[str] = []
    cursor = 0
    while match := _RAG_START_RE.search(expression, cursor):
        parts.append(expression[cursor : match.start()])
        first_open = match.end()
        maximum, after_maximum = _read_rag_argument(expression, first_open, "max")

        second_open = after_maximum
        while second_open < len(expression) and expression[second_open].isspace():
            second_open += 1
        if second_open >= len(expression) or expression[second_open] != "(":
            raise ValueError("rag syntax must be rag (max : expression) (min : expression)")
        minimum, after_minimum = _read_rag_argument(expression, second_open, "min")
        parts.append(f"rag_range({maximum}, {minimum})")
        cursor = after_minimum

    parts.append(expression[cursor:])
    return "".join(parts)
