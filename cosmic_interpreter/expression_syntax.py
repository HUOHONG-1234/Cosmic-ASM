from __future__ import annotations

import json


def transform_braced_expression(expression: str) -> tuple[str, set[str]]:
    parts: list[str] = []
    variables: set[str] = set()
    cursor = 0
    quote: str | None = None
    escaped = False

    while cursor < len(expression):
        character = expression[cursor]
        if quote is not None:
            parts.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            cursor += 1
            continue
        if character in {"'", '"'}:
            quote = character
            parts.append(character)
            cursor += 1
            continue
        if character == "}":
            raise ValueError("unmatched closing brace in expression")
        if character != "{":
            parts.append(character)
            cursor += 1
            continue

        start = cursor + 1
        cursor = start
        quoted = cursor < len(expression) and expression[cursor] == '"'
        inner_quote: str | None = '"' if quoted else None
        escaped = False
        if quoted:
            cursor += 1
        while cursor < len(expression):
            current = expression[cursor]
            if inner_quote is not None:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == inner_quote:
                    inner_quote = None
            elif current == "}":
                break
            elif current in {"{", '"'}:
                raise ValueError("invalid braced expression")
            cursor += 1
        if cursor >= len(expression):
            raise ValueError("missing closing brace in expression")

        content = expression[start:cursor]
        if quoted:
            try:
                value = json.loads(content)
            except json.JSONDecodeError as error:
                raise ValueError("invalid quoted string literal in expression") from error
            if not isinstance(value, str):
                raise ValueError("braced string literal must contain text")
            parts.append(repr(value))
        else:
            name = content.strip()
            if not _is_identifier(name):
                raise ValueError(
                    "braces must contain a variable name or a quoted string literal"
                )
            parts.append("var_" + name)
            variables.add(name)
        cursor += 1

    if quote is not None:
        raise ValueError("unterminated string literal in expression")
    return "".join(parts), variables


def _is_identifier(value: str) -> bool:
    if not value:
        return False
    first = value[0]
    if not first.isascii() or not (first.isalpha() or first == "_"):
        return False
    return all(
        character.isascii() and (character.isalnum() or character == "_")
        for character in value
    )
