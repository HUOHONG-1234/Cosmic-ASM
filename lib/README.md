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

## 硬约束（决定 lib 能提供什么）

1. **用户函数只能返回数值。** `function 名称(参数) = 表达式` 与语句体函数都只产生数值；
   字符串结果无法从用户函数返回。需要字符串结果时，直接调用内置函数
   （`substr_bytes` / `trim` / `upper` / `lower` / `replace` / `read_text` /
   `str_from_bytes` / `char_at`）。
2. **编译后端的字符串内置只接受字面量或 `var` 文本变量**，不接受函数参数。
   实测报错：`invalid expression: strlen expects a string literal, variable, or char_at result`。
   所以 `text_len(文本)` 这类包装函数在编译版不可用。
3. **编译后端的 `ord` 只接受字面量、`var` 变量或 `chr(...)`**，不接受 `char_at(...)`。
   取码点请先放入单字符 `var`：`math_run ord({CH}) = ?`。
4. **数组不能作为用户函数参数**（编译版报 `unknown array 'a'`）。数组统计请直接写
   `array_sum(data)` 之类的内置调用。
5. **函数名/变量名首字符必须是 ASCII 字母或下划线**（`[A-Za-z_]\w*`）。中文只能出现在
   第一个字符之后，因此库函数名用英文；注释仍可用中文。
6. **函数体内的局部名必须在最外层、任何 `if`/`while` 之前用 `let` 声明**；块内不能写
   `let`，块内赋初值用 `push`。

## 文件

### lib/math.cos

纯数值函数：`sq(x)`、`cube(x)`、`factorial(n)`、`max2(a, b)`、`min2(a, b)`、`clamp(v, low, high)`。
两个后端均可编译，输出一致。

### lib/text.cos

文本查询本身只能用内置函数（见上面第 2 条），本文件提供的是「码点 → 数值」的纯数值函数：

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

`read_text`/`write_text` 不能嵌在 `strlen`/`find`/`line_count` 里，也不能在函数体内使用；
本文件**只保留说明**，不定义函数。正确顺序是「先读进变量，再查询」：

```cos
#%include = "lib/io.cos"
math_run read_text("index.json") = ?
push ({TXT} = {(in = up)})
math_run strlen({TXT}) = ? -int64              # 字节数
math_run line_count({TXT}) = ? -int64          # 行数
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
