from __future__ import annotations

import json
import re
from pathlib import Path, PureWindowsPath

from .parser import ParseError


_INCLUDE_RE = re.compile(r'#%\s*include\s*=\s*("(?:\\.|[^"\\])*")\s*$', re.I)


def load_source(path: Path, *, max_depth: int = 16) -> str:
    """Expand relative include declarations in source order."""
    root = path.resolve()
    global_directives: dict[str, tuple[str, Path, int]] = {}

    def expand(current: Path, stack: tuple[Path, ...], depth: int) -> list[str]:
        if depth > max_depth:
            raise ParseError(1, f"include depth exceeds {max_depth}")
        resolved = current.resolve()
        if resolved in stack:
            chain = " -> ".join(item.name for item in (*stack, resolved))
            raise ParseError(1, f"include cycle detected: {chain}")
        try:
            lines = resolved.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError) as error:
            raise ParseError(1, f"cannot read source file '{resolved}': {error}") from error
        expanded: list[str] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            declaration = re.fullmatch(
                r"#%\s*(math|precision|target)\s*=\s*(.*?)\s*", line.strip(), re.I
            )
            if declaration:
                key, value = declaration.group(1).lower(), declaration.group(2).lower()
                previous = global_directives.get(key)
                if previous is None:
                    global_directives[key] = (value, resolved, index + 1)
                    expanded.append(line)
                elif previous[0] != value:
                    raise ParseError(
                        index + 1,
                        f"conflicting #%{key} declaration in '{resolved}' "
                        f"(first declared in '{previous[1]}' at line {previous[2]})",
                    )
                index += 1
                continue
            match = _INCLUDE_RE.fullmatch(line.strip())
            if not match:
                expanded.append(line)
                index += 1
                continue
            try:
                include_name = json.loads(match.group(1))
            except json.JSONDecodeError as error:
                raise ParseError(index + 1, f"invalid include path: {error}") from error
            windows_path = PureWindowsPath(include_name)
            include_path = Path(include_name)
            if include_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
                raise ParseError(index + 1, "include path must be relative")
            index += 1
            if index < len(lines) and lines[index].strip().lower() == "#include":
                index += 1
            expanded.extend(
                expand(resolved.parent / include_path, (*stack, resolved), depth + 1)
            )
        return expanded

    return "\n".join(expand(root, (), 0)) + "\n"
