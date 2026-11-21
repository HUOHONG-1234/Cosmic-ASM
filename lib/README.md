# lib/ 标准库（随发行版附带）

本目录是 Cosmic ASM 的标准库，随仓库/安装包一起发布。它和 `packages/` 的分工是：

| 目录 | 谁维护 | 怎么用 | 内容 |
|---|---|---|---|
| `lib/` | 语言发行版自己 | `#%include = "lib/xxx.cos"` | 语言自带的底层工具函数/常量，不打包、不进 `index.json` |
| `packages/` | 用户/第三方，经 `cosp` 安装 | `cosp add 名称` 后由 `cosp` 生成 include | 可版本化、可发布的独立包（如 `mathx`、`seqkit`） |

`lib/` 不参与 `cosp` 的解析（`index.json` 只列 `packages/`），`examples/` 只放示例，
不再承担库的职责。

## 用法

```cos
#%include = "lib/math.cos"
math_run factorial(5) = ? -int64        # 120
```

`#%include` 的路径相对于**声明所在文件**；绝对路径、循环包含、超过 16 层都会报错。
同一个文件被多次 include 会重复定义函数，因此只在需要处引入一次。

## 语言能力（v9 之后）

v9 Delta 拆掉了三道墙，`lib/` 的写法也跟着变了：

1. **用户函数可以接收文本参数、返回文本。** 所以 `text_len(t)` / `text_upper(t)` 这类
   包装现在可以真正写出来，两个后端输出一致：

   ```cos
   #%include = "lib/text.cos"
   var TEXT = {"Cosmic ASM"}
   math_run text_len({TEXT}) = ? -int64        # 9
   math_run text_upper({TEXT}) = ?             # COSMIC ASM
   ```

   未加标注的参数会按函数体内的用法自动推断类型（`function f(t) = upper(t)` 的 `t` 是文本）。
   需要明确类型时写 `t : text` / `-> text`。
2. **用户函数可以接收数组参数、返回数组。**

   ```cos
   function total(values : array) = array_sum(values)
   function build(size) = array_fill(size, 7)
   math_run total(build(3)) = ? -int64         # 21
   ```
3. **记录可以跨函数传递**（参数与返回值都可以是记录）：

   ```cos
   record Money { amount, currency }
   function add_money(a : Money, b : Money) -> Money
   {
       return Money(a.amount + b.amount, a.currency)
   }
   ```
4. **`ord` 接受字面量、`var` 变量、`chr(...)`，也接受返回文本的内置调用**（如 `ord(char_at({T}, 0))`）。
5. **函数名/变量名首字符必须是 ASCII 字母或下划线**（`[A-Za-z_]\w*`）。中文只能出现在
   第一个字符之后，因此库函数名用英文；注释仍可用中文。
6. **函数体内的局部名必须在最外层、任何 `if`/`while` 之前用 `let` 声明**；块内不能写
   `let`，块内赋初值用 `push`。

## 文件

### lib/math.cos

纯数值函数：`sq(x)`、`cube(x)`、`factorial(n)`、`max2(a, b)`、`min2(a, b)`、`clamp(v, low, high)`。
两个后端均可编译，输出一致。

### lib/text.cos

v9 起分为两组。

文本包装（参数/返回值可以是文本）：

| 函数 | 说明 |
|---|---|
| `text_len(t)` | 文本长度（码点） |
| `text_lines(t)` | 行数 |
| `text_upper(t)` / `text_lower(t)` | 大小写转换 |
| `text_trim(t)` | 去掉首尾空白 |
| `text_count(t, word)` | 子串出现次数 |
| `text_has(t, word)` | 是否包含子串 |
| `text_starts(t, prefix)` / `text_ends(t, suffix)` | 前后缀判断 |
| `text_repeat(t, times)` | 重复文本 |
| `text_field(t, sep, index)` | 按分隔符取第 index 个字段 |
| `text_fields(t, sep)` | 按分隔符切分后的字段数 |

码点判定（纯数值，先用 `ord({CH})` 取码点）：

| 函数 | 中文名 | 说明 |
|---|---|---|
| `char_is_space(code)` | 是空白吗 | 9/10/13/32 |
| `char_is_digit(code)` | 是数字吗 | ASCII 0-9 |
| `char_is_alpha(code)` | 是字母吗 | ASCII A-Z/a-z |
| `char_width(code)` | 显示宽度 | CJK/全角/emoji 返回 2，其余 1 |

```cos
#%include = "lib/text.cos"
var CJK = {"中"}
math_run char_width(ord({CJK})) = ? -int64     # 2
```

文本查询请直接调用内置：

```cos
var TEXT = {"Cosmic ASM"}
math_run strlen({TEXT}) = ? -int64             # 10
math_run find({TEXT}, "ASM") = ? -int64        # 7（UTF-8 字节下标）
math_run line_count({TEXT}) = ? -int64         # 1
```

### lib/array.cos

数组不能作函数参数，本文件**只保留说明**，不定义函数。直接调用内置：

```cos
#%include = "lib/array.cos"
array data = [1, 2, 3, 4]
math_run array_sum(data) = ? -int64            # 具名数组写裸名
push ({values} = array_copy(data))
math_run array_sum({values}) = ? -int64        # 变量里的数组用 {名字}
```

### lib/io.cos

`read_text`/`write_text` 现在也能作为**用户函数参数**（v9 起文本参数/返回都支持），
同时保留 v8.1 的嵌套写法：`strlen(read_text(...))`、`find(read_text(...), 字串)`、
`line_count(trim(read_text(...)))` 两端一致。本文件不定义函数，只保留用法说明：

```cos
#%include = "lib/io.cos"
math_run strlen(read_text("index.json")) = ? -int64   # 直接嵌套
math_run read_text("index.json") = ?
push ({TXT} = {(in = up)})                            # 或先读进变量再多次查询
math_run line_count({TXT}) = ? -int64
```

### lib/term.cos

TUI 用的 ANSI 静态常量（`var`），`\u001b` 是唯一可用的 ESC 写法（`\x1b` 会被拒）：

`TERM_ESC`、`TERM_CLEAR`、`TERM_HOME`、`TERM_RESET`、`TERM_BOLD`、`TERM_REVERSE`、
`TERM_HIDE_CURSOR`、`TERM_SHOW_CURSOR`、`TERM_ALT_SCREEN`、`TERM_MAIN_SCREEN`。

```cos
#%include = "lib/term.cos"
print (<{TERM_CLEAR}>)
print (<{TERM_ESC} (and) {"38;5;33m"}>)        # 256 色前景
```

需要按行列拼数值的序列（如 `ESC[<行>;<列>H`）无法由用户函数生成，这部分由 Python 侧
`tui.py` 负责，cos 侧只暴露静态常量。

## 验证方式

每个 `lib/*.cos` 都用如下两步跑过，且输出逐字节一致：

```powershell
python -m cosmic_interpreter.cli 测试.cos
python -m cosmic_interpreter.compiler 测试.cos -o build\测试.exe
build\测试.exe
```

因后端限制被剔除的函数（`text_len` / `text_find` / `text_trimmed_len` /
`text_char_code` / `arr_len` / `arr_sum` / `arr_mean` / `file_bytes`）没有留在库里，
它们的作用已改为上文的内置调用写法。
