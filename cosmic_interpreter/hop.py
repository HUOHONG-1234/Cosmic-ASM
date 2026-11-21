"""HOP（动词优先）语法糖层。

把中文动词句子重写成现有 Cosmic 语句，发生在 ``parser.parse()`` 之前。
设计约束：

* **不引入第二套内核**：只做行级重写，旧语法原样透传。
* **行号可回溯**：一句 HOP 可能展开成 2–3 行，``rewrite`` 同时返回
  ``line_map``，把生成行号映射回原始行号，报错仍指向用户写的行。
* **失败即停**：连续的 HOP 句子之间插入 ``require_ok <步骤号>``，
  前一步出错时立刻终止，不再继续执行后面的步骤。

语法：``动词[名词限定] 参数… 存 目标``，参数可用 ``"文本"``、``{变量}``、
``(表达式)`` 或裸数字。为了读起来顺，``里的`` / ``为`` / ``到`` / ``用``
这几个助词会被忽略。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# 助词：出现在句中但不参与语义。
_PARTICLES = frozenset({"里的", "为", "到", "用", "把"})

# 结果类别决定展开形式。
_STRING = "string"   # 结果为文本：math_run ... = ? 后写回变量
_NUMERIC = "numeric"  # 结果为数值：同上，跟随程序 math mode
_ARRAY = "array"      # 结果为数组：array 声明
_STATEMENT = "statement"  # 只执行，不写回

# 目标关键字：中文 `存` 与英文 `to` / `into` 等价。
_TARGET_KEYWORDS = ("存", "to", "into")


@dataclass(frozen=True)
class _Verb:
    name: str
    arity: int
    builtin: str
    kind: str = _NUMERIC
    # 第一个参数是数组名：内置要裸名（`array_sum(data)`），不能包装成 {data}。
    array_argument: bool = False

    @property
    def needs_target(self) -> bool:
        return self.kind != _STATEMENT


_VERBS: dict[str, _Verb] = {}


def _register(*verbs: _Verb) -> None:
    for verb in verbs:
        _VERBS[verb.name] = verb


# text 组：字符串结果一律走 math_run ... = ?（该形式会返回文本）。
_register(
    _Verb("打开", 1, "read_text", _STRING),
    _Verb("写入", 2, "write_text", _STATEMENT),
    _Verb("文本长度", 1, "strlen", _NUMERIC),
    _Verb("查找位置", 2, "find", _NUMERIC),
    _Verb("替换文本", 3, "replace", _STRING),
    _Verb("去空白", 1, "trim", _STRING),
    _Verb("转大写", 1, "upper", _STRING),
    _Verb("转小写", 1, "lower", _STRING),
    _Verb("截取字节", 3, "substr_bytes", _STRING),
    _Verb("行数", 1, "line_count", _NUMERIC),
)

# array 组
_register(
    _Verb("数组长度", 1, "array_len", _NUMERIC, array_argument=True),
    _Verb("数组求和", 1, "array_sum", _NUMERIC, array_argument=True),
    _Verb("数组均值", 1, "mean", _NUMERIC, array_argument=True),
    _Verb("取元素", 2, "array_at", _NUMERIC, array_argument=True),
    _Verb("追加元素", 2, "array_push", _ARRAY, array_argument=True),
    _Verb("填充", 2, "array_fill", _ARRAY),
)

# math 组
_register(
    _Verb("平方根", 1, "sqrt", _NUMERIC),
    _Verb("绝对值", 1, "abs", _NUMERIC),
    _Verb("幂", 2, "pow", _NUMERIC),
    _Verb("向下取整", 1, "floor", _NUMERIC),
    _Verb("向上取整", 1, "ceil", _NUMERIC),
    _Verb("正弦", 1, "sin", _NUMERIC),
    _Verb("余弦", 1, "cos", _NUMERIC),
    _Verb("对数", 2, "log", _NUMERIC),
)

# 英文动词（verb-object 风格），与中文动词完全等价，供非中文母语者使用。
_register(
    _Verb("open_file", 1, "read_text", _STRING),
    _Verb("write_file", 2, "write_text", _STATEMENT),
    _Verb("text_length", 1, "strlen", _NUMERIC),
    _Verb("find_index", 2, "find", _NUMERIC),
    _Verb("replace_text", 3, "replace", _STRING),
    _Verb("trim_text", 1, "trim", _STRING),
    _Verb("upper_text", 1, "upper", _STRING),
    _Verb("lower_text", 1, "lower", _STRING),
    _Verb("slice_bytes", 3, "substr_bytes", _STRING),
    _Verb("count_lines", 1, "line_count", _NUMERIC),
    _Verb("array_length", 1, "array_len", _NUMERIC, array_argument=True),
    _Verb("array_total", 1, "array_sum", _NUMERIC, array_argument=True),
    _Verb("array_mean", 1, "mean", _NUMERIC, array_argument=True),
    _Verb("at_index", 2, "array_at", _NUMERIC, array_argument=True),
    _Verb("push_item", 2, "array_push", _ARRAY, array_argument=True),
    _Verb("fill_array", 2, "array_fill", _ARRAY),
    _Verb("square_root", 1, "sqrt", _NUMERIC),
    _Verb("absolute_value", 1, "abs", _NUMERIC),
    _Verb("power_of", 2, "pow", _NUMERIC),
    _Verb("floor_of", 1, "floor", _NUMERIC),
    _Verb("ceil_of", 1, "ceil", _NUMERIC),
    _Verb("sine_of", 1, "sin", _NUMERIC),
    _Verb("cosine_of", 1, "cos", _NUMERIC),
    _Verb("log_of", 2, "log", _NUMERIC),
)


class HopError(Exception):
    """HOP 句子写法不合法；``line`` 是原始行号。"""

    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"line {line}: {message}")
        self.line = line
        self.message = message


def _tokenize(text: str) -> list[str]:
    """按空白切分，但保护 "文本"、{变量}、(...) 内部。"""
    tokens: list[str] = []
    buffer: list[str] = []
    depth = 0
    in_string = False
    for character in text:
        if in_string:
            buffer.append(character)
            if character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
            buffer.append(character)
            continue
        if character in "{(":
            depth += 1
        elif character in "})":
            depth = max(0, depth - 1)
        if character.isspace() and depth == 0:
            if buffer:
                tokens.append("".join(buffer))
                buffer = []
            continue
        buffer.append(character)
    if buffer:
        tokens.append("".join(buffer))
    return tokens


def _strip_particles(tokens: list[str]) -> list[str]:
    return [token for token in tokens if token not in _PARTICLES]


def _store_lines(builtin: str, arguments: list[str], target: str) -> list[str]:
    """生成「算出来写进变量」的两条语句，保持由 caller 追加步骤守卫。"""
    call = f"{builtin}({', '.join(arguments)})"
    return [
        f"math_run {call} = ?",
        f"if up = (ok) goto (push ({{{target}}} = {{(in = up)}}))",
    ]

_BARE_IDENTIFIER = re.compile(r"\w+\Z", re.UNICODE)


def _brace(argument: str) -> str:
    """裸标识符按变量引用处理：数组内置用裸名，其余内置要 ``{变量}``。"""
    stripped = argument.strip()
    if _BARE_IDENTIFIER.fullmatch(stripped) and not stripped.isdigit():
        return f"{{{stripped}}}"
    return argument


def _expand(verb: _Verb, arguments: list[str], target: str | None) -> list[str]:
    if verb.array_argument:
        # 数组内置要裸名；其余参数（下标/值）按变量包装。
        prepared = arguments[:1] + [_brace(argument) for argument in arguments[1:]]
    else:
        prepared = [_brace(argument) for argument in arguments]
    call = f"{verb.builtin}({', '.join(prepared)})"
    if verb.kind == _STATEMENT:
        return [f"math_run {call} = ?"]
    if verb.kind == _ARRAY:
        return [f"array {target} = {call}"]
    return _store_lines(verb.builtin, prepared, str(target))


def _try_expand(line: int, tokens: list[str]) -> list[str] | None:
    """能识别就返回展开后的语句；不是 HOP 句子返回 None（原样透传）。"""
    if not tokens:
        return None
    verb = _VERBS.get(tokens[0])
    if verb is None:
        return None
    rest = _strip_particles(tokens[1:])
    target: str | None = None
    if verb.needs_target:
        keyword_index = next(
            (index for index, token in enumerate(rest) if token in _TARGET_KEYWORDS),
            None,
        )
        if keyword_index is None:
            raise HopError(
                line,
                f"'{verb.name}' needs a target: write "
                f"'to <variable>' or '存 <variable>' "
                f"(example: {verb.name} ... to result)",
            )
        arguments = rest[:keyword_index]
        tail = rest[keyword_index + 1 :]
        if len(tail) != 1:
            raise HopError(
                line, f"'{verb.name}' must be followed by exactly one variable name"
            )
        target = tail[0].strip("{}")
        if not target or not target.replace("_", "").isalnum():
            raise HopError(line, f"'{verb.name}' target variable is not a valid name: {tail[0]}")
    else:
        arguments = rest
    if len(arguments) != verb.arity:
        raise HopError(
            line,
            f"'{verb.name}' expects {verb.arity} argument(s), got {len(arguments)}",
        )
    return _expand(verb, arguments, target)


_IDENTIFIER_RUN = re.compile(r"[^\W\d]\w*", re.UNICODE)


def _is_chinese_identifier(text: str) -> bool:
    """以非 ASCII 字母开头的标识符（中文变量名/函数名）。"""
    return bool(text) and any(ord(character) > 127 for character in text)


def _mangle_name(name: str) -> str:
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    return f"zh_{digest}"


# 内部名 → 用户原名，用于把错误信息还原成人能读的名字。
_REVERSE_NAMES: dict[str, str] = {}


def humanize(message: str) -> str:
    """把消息里的 HOP 内部标识符换回用户写的中文名。"""
    if not _REVERSE_NAMES:
        return message
    for mangled, original in _REVERSE_NAMES.items():
        if mangled in message:
            message = message.replace(mangled, original)
    return message


def _collect_chinese_names(line: str, found: set[str]) -> None:
    """收集一行里出现在字符串字面量之外的标识符。"""
    in_string = False
    index = 0
    length = len(line)
    while index < length:
        character = line[index]
        if character == '"':
            in_string = not in_string
            index += 1
            continue
        if in_string:
            index += 1
            continue
        match = _IDENTIFIER_RUN.match(line, index)
        if match and _is_chinese_identifier(match.group(0)):
            found.add(match.group(0))
            index = match.end()
            continue
        index += 1


def _apply_mangling(line: str, mapping: dict[str, str]) -> str:
    """把字符串字面量之外的已知中文标识符替换成 ASCII 名。"""
    if not mapping:
        return line
    result: list[str] = []
    in_string = False
    index = 0
    length = len(line)
    while index < length:
        character = line[index]
        if character == '"':
            in_string = not in_string
            result.append(character)
            index += 1
            continue
        if in_string:
            result.append(character)
            index += 1
            continue
        match = _IDENTIFIER_RUN.match(line, index)
        if match:
            word = match.group(0)
            result.append(mapping.get(word, word))
            index = match.end()
            continue
        result.append(character)
        index += 1
    return "".join(result)


def rewrite(source: str) -> tuple[str, list[int] | None]:
    """重写源码。返回 ``(新源码, line_map 或 None)``。

    ``line_map[i]`` 是新源码第 ``i+1`` 行对应的原始行号；没有任何 HOP 句子时
    返回 ``(source, None)``，调用方按原逻辑处理。

    中文标识符（变量名、目标名）会在这里被翻译成稳定的 ASCII 名：核心解析器
    仍然只认 ASCII 标识符，C 后端也就不需要改。字符串字面量与注释不受影响。
    """
    raw_lines: list[str] = []
    line_map: list[int] = []
    chinese_names: set[str] = set()
    changed = False
    previous_was_hop = False
    step = 0
    for number, original in enumerate(source.splitlines(), 1):
        stripped = original.strip()
        tokens = _tokenize(stripped) if stripped else []
        expanded = _try_expand(number, tokens) if tokens else None
        if expanded is None:
            raw_lines.append(original)
            line_map.append(number)
            previous_was_hop = False
            step = 0
            if stripped and not stripped.startswith("#"):
                _collect_chinese_names(stripped, chinese_names)
            continue
        changed = True
        step += 1
        # 连续的 HOP 步骤之间插入守卫：前一步失败立刻停。
        if previous_was_hop and step > 1:
            raw_lines.append(f"require_ok {step}")
            line_map.append(number)
        raw_lines.extend(expanded)
        line_map.extend([number] * len(expanded))
        previous_was_hop = True
        for verb_line in expanded:
            _collect_chinese_names(verb_line, chinese_names)

    if not changed:
        return source, None
    mapping = {name: _mangle_name(name) for name in sorted(chinese_names)}
    _REVERSE_NAMES.update({mangled: original for original, mangled in mapping.items()})
    output: list[str] = []
    for index, raw in enumerate(raw_lines):
        if raw.strip().startswith("#"):
            output.append(raw)
            continue
        output.append(_apply_mangling(raw, mapping))
    return "\n".join(output) + ("\n" if source.endswith("\n") else ""), line_map


def expand_only(source: str) -> str:
    """便利函数：只返回重写后的源码（忽略行号映射）。"""
    return rewrite(source)[0]
