# Cosmic ASM 完整语法参考

> 本文是逐条语法说明。快速上手看 [README.md](./README.md)。


这是一个使用 Python 编写的 Cosmic ASM 解释器 MVP，针对 [示例程序](./examples/demo.cos) 和目前整理出的语法实现。它还不是完整的语言规范实现；尚未支持的语法会尽可能给出带源码行号的错误。

## 运行

在项目目录中运行：

```powershell
python -m cosmic_interpreter.cli 路径\到\程序.cos
```

### 编译目标

默认目标为 `host`。可在源文件开头声明交叉编译目标：

```cos
#%target = riscv64-linux-gnu
#%target = aarch64-linux-gnu
```

RISC-V：编译器选择 `riscv64-linux-gnu-gcc`，传入 `-march=rv64gc -mabi=lp64d`。
ARM64（v8 新增）：选择 `aarch64-linux-gnu-gcc`，传入 `-march=armv8-a`。
两者都需要先安装对应交叉工具链；没有该工具链时会明确报错，不会回退到本机 GCC。Windows 上走 WSL 转换路径（`--gcc` 可覆盖工具链名）。当前支持集不包含裸机 MCU，也不包含 Windows on ARM。

源文件可用相对路径包含其他 `.cos` 文件，路径相对于声明所在文件。支持单行写法，也兼容其后带 `#include` 标签的两行写法：

```cos
#%include = "lib/text.cos"
#include
```

绝对路径、循环包含及超过 16 层的包含会报错。跨文件重复声明相同的 `#%math` / `#%precision` 时仅保留首次声明；值不同时会报告冲突。

运行仓库附带的示例：

```powershell
python -m cosmic_interpreter.cli examples\demo.cos
```

新的科学计算、循环和磁带示例：

```powershell
python -m cosmic_interpreter.cli examples\scientific_tape.cos
```

四个整数的冒泡排序示例：

```powershell
python -m cosmic_interpreter.cli examples\bubble_sort.cos
```

用反正切公式计算圆周率：

```powershell
python -m cosmic_interpreter.cli examples\pi_machin.cos
```

### 标准输入

`ask` 从标准输入读取一行并赋给变量；提示文本可选。输入最多保留 4096 个编码后字节，超出部分会被截断并令状态为 `error`。EOF 将变量设为空字符串并令状态为 `error`，空行则是成功输入。

```cos
ask ({name}) (<{"Name: "}>)
print (<{name}>)
```

使用 `--no-input` 可禁用标准输入读取，`ask` 会按 EOF 处理：

```powershell
python -m cosmic_interpreter.cli 程序.cos --no-input
python -m cosmic_interpreter.compiler 程序.cos --no-input
```

### 字符串函数

数学表达式支持以下字符串操作：

```cos
math_run substr_bytes("Cosmic", 1, 3) = ?  # 字节起点与长度，越界截断
math_run find("Cosmic ASM", "ASM") = ?     # 字节下标；未找到为 -1
math_run trim("  Cosmic  ") = ?            # 去除首尾空格、制表符、CR/LF
math_run upper("Cosmic") = ?               # Unicode 大小写映射
math_run lower("COSMIC") = ?
math_run replace("cosmic cosmic", "cosmic", "star") = ?
```

`substr_bytes` 和 `find` 使用 UTF-8 字节偏移，不是 Unicode 字符下标。截出的字节若不是完整 UTF-8 序列，解释器会以替代字符呈现；编译后的程序保留原始字节。`upper`/`lower` 对每个 Unicode 码点分别执行大小写映射，映射可能展开为多个码点；它不做 locale 敏感转换或上下文相关的希腊终 sigma 处理。编译后端根据编译时 Python 的 Unicode 数据生成映射表。`replace` 替换所有匹配项，空的源字符串会报错。

数值数组还支持 `array_fill(length, value)` 创建填充值数组，以及 `array_copy(array)` 创建独立副本：

```cos
array values = array_fill(4, 3)
array duplicate = array_copy(values)
```

`array_fill` 的长度必须是 0 到 1,000,000 之间的整数；`array_copy` 保留数组元素类型和值，不共享可变存储。

UTF-8 字符串与数值字节数组可显式转换：

```cos
array encoded = bytes_from_str("宇宙")
math_run str_from_bytes(encoded) = ?
```

`bytes_from_str` 返回 UTF-8 字节数组；`str_from_bytes` 要求每个元素是 0..255 的整数，且字节序列必须是有效 UTF-8。由于现有字符串以 NUL 结尾存储，两项转换都拒绝嵌入 NUL。

Unicode 字符与码点互转：

```powershell
python -m cosmic_interpreter.cli examples\unicode_conversion.cos
```

复数、函数、数组和矩阵示例：

```powershell
python -m cosmic_interpreter.cli examples\advanced_math.cos
python -m cosmic_interpreter.compiler examples\advanced_math.cos --simd -o build\advanced_math.exe
```

字符串长度与取字符示例（本仓库语法支持 `strlen` 和 `char_at`）：

```cos
var word = {"你好"}
math_run strlen({word}) = ? -int32
math_run char_at({word}, 0) = ?
```

`strlen` 按 Unicode 字符计数；`char_at` 使用从 `0` 开始的索引，因此上例长度为 `2`、第 `0` 个字符为“你”。

函数、递归、数组与矩阵计算：

```cos
function factorial(n) = choose(n <= 1, 1, n * factorial(n - 1))
math_run factorial(10) = ?
math_run dot([1, 2, 3], [4, 5, 6]) = ?
math_run matrix2_det([[1, 2], [3, 4]]) = ?
math_run matrix2_mul_entry([[1, 2], [3, 4]], [[5, 6], [7, 8]], 1, 0) = ?
```

`function name(params) = expression` 定义数值表达式函数；`choose(condition, a, b)` 惰性选择分支，因此可用于递归基线。也可以写线性的多语句函数，用 `let name = expression` 声明局部数值名，并以 `return expression` 结束：

```cos
function transform(value)
{
    let doubled = value * 2
    let adjusted = doubled + 3
    return adjusted
}
```

函数体支持按顺序的 `let`（声明局部名）、`set`（给已有局部名或参数赋值）、`return`、`if`/`else`，以及可嵌套的 `while` 循环；`while` 内可用 `break` 和 `continue`。局部名必须写在函数体最外层、任何 `if`/`while` 之前；函数体结束前没有执行到 `return` 时返回 `0`。下面的写法与上面的 `transform` 等价，并演示循环：

```cos
function gcd(a, b)
{
    let x = a
    let y = b
    let remainder = 0
    while (y > 0)
    {
        set remainder = x % y
        set x = y
        set y = remainder
    }
    return x
}
```

函数体内还可以声明**嵌套函数**，用单行形式 `function 名称(参数) = 表达式`：

```cos
function outer(seed)
{
    let factor = 2
    function apply(value) = value * factor + seed
    return apply(3) + apply(4)
}
```

嵌套函数按值捕获外层参数和 `let` 局部名：调用时取当前值的副本，函数内无法写回外层，也不存在函数值、指针或可变引用。因此「捕获」只是把外层当前值当作隐藏实参传入。嵌套函数在函数体内声明即对整个函数体可见（声明被提升），且内部不能再声明嵌套函数。

数值记录（record）与纯表达式运算符重载：

```cos
record Point { x, y }
operator + Point = { left.x + right.x, left.y + right.y }

function compute()
{
    let a = Point(1, 2)
    let b = Point(3, 4)
    let sum = a + b
    let copy = sum
    set copy.x = 10
    return sum.x * 100 + copy.x
}
```

可选功能裁剪（默认全开，只减不加）：

```cos
#%features = no-bigint, no-array, no-matrix
```

支持的标签只有 `no-bigint`、`no-array`、`no-matrix`（`no-array` 会隐含 `no-matrix`）。被禁用的语言能力在**解析期**就报错，例如 `'array_sum' is not available because feature 'no-array' is disabled`，而不是等到链接期；未知标签同样报错。声明了标签后编译命令会追加 `-ffunction-sections -fdata-sections -Wl,--gc-sections`，让链接器丢弃不可达的运行时函数；此外运行时代码本身按功能域切块：`no-bigint` 用 C 预处理排除 BigInt 与定点常量运行时，`no-array` / `no-matrix` 排除数组统计与矩阵运行时。注意：在 GNU ld 上 `--gc-sections` 已经能丢弃这些不可达函数，实测二进制体积几乎不变，收益主要体现在生成的 C 更短、编译更快，以及没有链接期裁剪的工具链上。这不是裸机级别的运行时最小化。

内存模式（v7）：

```cos
#%memory = heap             # 默认：每个临时数组单独 malloc/free
#%memory = arena            # 临时数组从 64 KiB 分块 bump 分配，块退出整块归还
#%memory = static           # 临时数组从编译期定长池分配，零 malloc
#%maxlen = 1024             # 数组容量上限（static 模式必填）
#%on_oom = abort            # 容量超限的默认行为：报错退出
#%on_oom = error            # 容量超限时置状态为 error，可由 catch / dog 处理
```

三种模式只改变**临时值**（表达式中间数组、矩阵工作区、`array_push`/`array_copy` 的副本）的存储方式；`Context` 里的变量与具名数组始终用持久 `malloc` 副本，所以模式之间语义完全一致，输出逐字节相同。`arena` / `static` 的临时池在每次整批归还时复位，因此块内分配不会跨块泄漏，但同一个块内的大量分配会一直占用到归还点（`arena` 会用新分块继续增长，`static` 到达池上限会报 `static memory pool is exhausted`）。`#%maxlen` 超限在两种后端一致：`abort` 打印 `array capacity exceeded: #%maxlen is smaller than the declared array` 并以退出码 1 结束；`error` 把语句状态置为 `(error)` 并保留原有数组内容。容量检查作用于 `array` 声明的具名数组；通过 `push` 存进变量的数组不受 `#%maxlen` 限制。

限制（明确报错，不静默降级）：`#%memory = static` 必须同时声明 `#%maxlen`；`gc_pin` 需要 `#%memory = heap`（pin 的缓冲区必须活过块 arena）；`#%memory` 非 `heap` 时不能使用 atom 块（临时池是进程级、非线程安全）。

按需导入：生成 C 时只输出从主程序表达式可达的用户函数（含 `#%include` 进来的库函数），未被调用的库函数不会出现在生成代码里；顶层副作用语句的顺序不受影响。

内存回收控制（v6）：

```cos
#gc = semi          # 默认：作用域结束时确定释放（none 表示不释放，进程退出统一回收）
array data = [1, 2, 3]
gc_pin ({data})     # 固定该数组的运行时缓冲区
The_world (stop)    # 暂停自动释放，期间只记延迟释放
The_world (force)   # 立即释放未 pin 的缓冲
The_world (start)   # 恢复自动释放并处理积压
```

`#gc = none` 在编译版里让释放函数变成空操作；解释器由宿主语言管理内存，因此 `#gc` 只影响编译版行为，pin/停表状态在解释器侧同样记录（`gc_pin` 要求目标是已声明的数组或 `bytes` 变量）。`#gc = mark` 已支持：这门语言没有指针和别名，标记阶段没有对象图可遍历，因此 `mark` 等价于“所有临时分配都延迟到 `The_world (force)` 或进程退出时统一清扫”，用作 refcount 模式的交叉验证。`The_world (stop)` 可嵌套，`start` 减到零才恢复；执行块结束时若停表深度与进入时不一致，会打印 warning 并恢复进入时的深度（停表不跨块泄漏）。

内存报告与峰值：

```bash
python -m cosmic_interpreter.compiler program.cos --memory-report report.json
python -m cosmic_interpreter.compiler program.cos --heap-report
```

`--memory-report` 写出编译期静态 Context 估算，除记录大小外还包含 `memory_mode`、`maxlen`、`on_oom`、`gc_mode` 与 `static_pool_bytes`。`--heap-report` 编译出的可执行文件在退出时向 stderr 打印 `memory: peak temporary bytes N`，表示自上次整批归还以来的临时分配峰值（`#gc = none` 下即整轮运行的峰值）。

内联汇编（实验性，仅主机目标）：

```cos
#%asm = x86_64 (clobber: rax, memory)
#asm
{
    nop
}
```

`#%asm` 声明行必须紧接 `#asm`，架构只支持 `x86_64` / `arm64`，clobber 列表必填且必须包含 `memory`。架构与当前目标不符时解析期报错；汇编体里出现但 clobber 列表没声明的寄存器会给 warning。解释器无法执行汇编块，会明确报错。汇编内容原样透传给 C 编译器的 `__asm__ volatile`，编译器不保证寄存器分配或指令顺序，也不提供恒定时间保证。

命名空间与别名（只影响函数；记录与运算符仍是全局）：

```cos
#%namespace = sort
function quick(values) = values * 2
#%namespace = global
#%use = sort.quick as doubled

math_run doubled(4) = ?
math_run sort::quick(3) = ?
```

`#%namespace = 名称` 只作用于其后的函数声明，`#%namespace = global`（或 `main`）回到全局；带命名空间的函数内部名字是 `名称__函数`，必须用 `名称::函数(...)` 或 `#%use = 名称.函数 as 别名` 调用，未限定名不会自动搜索命名空间。别名在解析期展开成同名参数的转发函数，因此目标必须存在；别名与已有函数重名、目标不存在都会在解析期报错。

错误处理用 `try` / `catch` / `finally`（仅限顶层语句，至少要有 `catch` 或 `finally` 之一）：

```cos
try
{
    ask ({line})
}
catch
{
    push ({note} = {"input unavailable"})
}
finally
{
    push ({cleanup} = {"done"})
}
```

`catch` 处理**语言状态机层面的错误**：try 体内某条语句把 `up` 置为 `(error)` 时，try 体余下语句不再执行，转入 `catch`；`catch` 正常结束则状态恢复 `(ok)`。没有 `catch` 时错误在 `finally` 之后继续向上传播（`finally` 自己成功也不会清除该错误）。`finally` 总是执行；`finally` 自身出错时以它的错误为准。硬失败（例如除零、数值越界、递归超限）在两个后端都会直接终止，不会被 `catch` 捕获。

`function` 声明只能写在顶层；块里出现的函数声明会报错，嵌套函数只允许写在函数体内。记录字段只能是数值，按值复制：`let copy = sum` 与 `set copy = sum` 都逐字段复制，改副本不影响原件。字段读写用 `name.field`。v9 起记录是一等值：可以作参数（`a : 记录名`）、作返回值（`-> 记录名`）、在函数之间传递；字段仍然是数值，`print` 也还到不了记录（`print` 是顶层语句）。构造写作 `Point(...)`，字段个数不匹配或未知字段会在解析期报错。`operator` 仅支持 `+ - * /`，两侧必须是同类型记录或记录构造表达式，右侧写出的字段个数必须与记录一致，且只能引用 `left` / `right` 的字段；重载在解析期静态展开成字段运算，没有运行时分派。声明须写在文件顶层、函数定义之前。 函数体内暂无数组/矩阵运算、`push` 等副作用语句或外部状态；局部名和参数不会写入调用者的变量上下文，函数体也不能隐式读取调用者变量，需通过参数传入。使用 `--max-loop-iterations` 时，函数体内的 `while` 与执行块循环共享同一上限和报错文案。**递归深度是解释器和编译版的已知差异，按特性处理。** 解释器的表达式求值与函数调用走显式栈，不占 Python 解释器栈，因此上限 **4096** 层；编译版每一帧都吃真实线程栈，按函数形态分别限制：多语句函数体 **1024** 层，单表达式函数、`bigint` 函数与嵌套函数 **256** 层。超过上限时两种后端都报 `function recursion depth exceeded`。也就是同一段深递归在解释器里能跑、编译版可能先撞墙，这是两端资源模型不同造成的，不是缺陷。数值数组索引从 0 开始；用 `array scores = [1, 2, 3]` 声明具名数组，它会在同一上下文的多个 `math_run` 之间保留。`array_set(scores, index, value)` 和 `array_push(scores, value)` 会原地更新具名数组；也可以将它们嵌套在 `array_sum`、`array_at`、统计函数或 `array_len` 等数组操作里。传入列表字面量时，更新结果是临时数组，也可直接保存：`push ({saved} = array_set(scores, 1, 99))`。变量可以保存字符串或数组，数组赋值（例如 `push ({copy} = {saved})`）按值复制；之后可将该变量重新赋值为另一种类型。数组可用 `array_len`、`array_at`、`array_sum`、`dot`、`mean`、`std`、`min`/`max`，v5 起还可用 `median`、`variance`、`correlation`、`percentile`、`trapz`、`simpson`。数组统计目前仅接受实数；在 complex mode 下，具名数组和变量数组可保存复数并用于 `array_len`、`array_at`、`array_sum`、`array_set`、`array_push`、`dot`。`std` 计算总体标准差（`variance` 同样是总体方差），统计函数不接受空数组。执行块通过 `father = up` 继承上下文时，具名数组和变量数组都会复制，子块中的更新不会影响父上下文。数组值不是标量，不能直接作为 `math_run` 结果打印；请用数组函数提取数值。矩阵方面，`matrix2_det` 和 `matrix2_mul_entry` 仍是 2×2 便捷函数，v5 另提供任意 NxM 的 `matrix_rows`/`matrix_cols`/`matrix_at`/`matrix_mul`/`matrix_transpose`、`matrix2_inverse`、`matrix3_inverse` 和 `solve_linear`。

复数使用 `#%math = complex`，可写 `complex(real, imag)`、复数四则运算及 `sqrt`、`sin`、`cos`、`exp`、`log`、`real`、`imag`、`conj`、`cabs`。输出形式为 `实部+虚部i`。`#%math = bigint` 在解释器和 GCC 原生后端中均支持无外部依赖的任意精度整数（上限为 1,000,000 位），包括 `+`、`-`、`*`、`//`、`%`、非负整数 `**`、整数比较和递归函数；BigInt 幂的指数最大为 10,000。

UTF-8 文本文件 I/O 可用 `read_text(path)` 和 `write_text(path, text)`。路径和内容可用字符串字面量或文本变量；读取上限为 16 MiB。只对可信程序开放文件写入。

GCC 编译默认使用 `-O2`；`--simd` 会使用 `-O3 -ftree-vectorize` 自动向量化，不强制生成依赖特定 CPU 的指令。可选 `--fast-math` 会额外启用 `-ffast-math`，但可能改变 NaN、无穷值及 IEEE-754 边界语义，因此默认关闭。`blas_dot([..], [..])` 绑定系统 BLAS 的 `ddot`，`lapack_determinant2([[..],[..]])` 绑定系统 LAPACK 的 LU 分解；编译器按用到的函数自动链接 `-lblas` / `-llapack`，也可用 `--blas-library`、`--lapack-library` 更改库名。运行原生程序的机器需自行提供这些库。解释器用内置计算提供相同数值结果。

## 编译为原生程序

除了直接解释运行，也可以先把 Cosmic ASM 翻译成 C，再调用 GCC 编译：

```powershell
python -m cosmic_interpreter.compiler examples\demo.cos -o build\demo.exe
```

成功后会保留生成的 C 文件（本例为 `build\demo.c`），并生成可执行文件。没有指定 `-o` 时，会在源文件旁生成同名的 `.exe`（Windows）或原生可执行文件（其他系统），C 文件也会放在可执行文件旁。可以通过 `--c-output` 指定 C 文件路径，或用 `--gcc` 指定 GCC 程序路径：

```powershell
python -m cosmic_interpreter.compiler 程序.cos -o build\程序.exe --c-output build\程序.c --gcc gcc
```

需要预先安装 GCC，并确保 `gcc` 可以在终端中运行。此编译器将程序翻译成独立 C 源码，再由 GCC 生成原生程序；生成后的程序不需要 Python 解释器。

可以用 `--max-loop-iterations N` 给编译产物加循环保护。省略该选项时，编译产物不会人为限制循环次数。

编译后端目前只支持 UTF-8、GBK、ASCII 和 CP1252 编码。编译版本中的普通文本按 UTF-8 处理；Windows 上声明的编码用于变量字节存储和输出转换，其他系统目前回退为 UTF-8。科学函数和 `float64` 使用 C `double`，结果显示精度可能与解释器略有差异；纯整数表达式在 `int64` 模式下使用有溢出检查的 64 位整数，含科学函数的 `int64` 表达式需落在 double 可精确表示的整数范围内。`int32`/`int64` 结果都会检查整数范围。

编译版的 `bash = 命令` 会通过系统命令处理器执行，以便支持 Shell 命令语法。**这会执行源文件指定的系统命令，只编译和运行你信任的代码。** 如需查看生成的 C 代码，可直接打开编译器留下的 `.c` 文件。

默认允许解释器执行外部命令，以便支持示例中的 `bash = dir`。解释器普通命令会拆分为参数后直接启动，不通过 shell 拼接执行；在 Windows 上，`dir` 和 `Get-ChildItem` 会交给 PowerShell 执行。**只运行你信任的源文件。** 如果不希望解释器执行外部命令，可以关闭该功能：

```powershell
python -m cosmic_interpreter.cli 路径\到\程序.cos --no-external-commands
```

## 自己打包成单个 EXE

仓库只提供源码，不附带预编译产物。要一个独立可执行文件就自己打：

```powershell
python -m pip install pyinstaller
pyinstaller --onefile --name cosmic packaging\cosmic_entry.py
```

产物 `dist\cosmic.exe` 自带 Python 运行时，可直接用 `cosmic run 程序.cos` / `cosmic build 程序.cos -o app.exe` / `cosmic new 项目名`。把它所在目录加进 `PATH` 就能全局调用。

原生编译那一步仍然要系统里装 GCC，并确保 `gcc` 可从命令行找到。

## 基础（v1–v3）：变量、状态机、执行块

### 变量与文本

`{name}` 始终是变量引用，`{"text"}` 才是字符串字面量——靠花括号里有没有引号区分。

```cos
var greeting = {"Hello, "}
push ({name} = {"World"})
push ({line} = {greeting} (and) {name})
print (<{line}>)
```

- `var name = {"初始文本"}`：声明并初始化字符串变量。
- `push ({name} = 值)`：写入变量，此前不存在会自动创建为字符串变量。
- `(and)` 拼接片段；`{(in = up)}` 取上一行 `math_run` 的结果。
- `#name = GBK` 指定该变量的存储编码（不初始化值）。

### 状态机与求值

每条语句结束都有状态 `up`：`(ok)` 或 `(error)`；求值结果另存在 `up` 的值里。

```cos
math_run 6 * 7 = ? -int64
if up = (ok) goto (push ({answer} = {(in = up)}))
print (<{answer}>)
```

- `math_run <表达式> = ? -类型`：求值。类型后缀 `-int32` / `-int64` / `-float64` / `-complex` / `-bigint` / `-uint32`；**文本结果不加后缀**。
- `if up = (状态) goto (...)`：按状态跳转。
- `print (<{var}>)` 打变量，`print (<{"literal"}>)` 打文本。
- `kill {var}` 销毁变量；`sleep (30)` 暂停。

### 执行块

`start` 只定义块，`call` 才让它跑——漏写 `call` 时块静默不执行，也不报错。

```cos
start(normal) = {main}
{
    print (<{"run"}>)
}
call = (main)
```

- `start(normal) = {名}` 普通块，`start(loop) = {名}` 循环块。
- `father = up` 把当前上下文复制给紧随其后的块；块内改动不回传父块。
- `dog(名) = {ok}` 检查块结束状态，或用 `dog(名) = (pass : ok)` 做状态白名单映射。
- `jump(行号)` 跳到本块内某物理行（计入空行与注释），不能跳进 `if` / `while`。
- 块后可选 `else { ... }`，仅在块以 `stop` / `error` 结束时执行。
- `goto (to = stop)` 主动结束当前块。

### 输入与系统

```cos
ask ({name}) (<{"Name: "}>)
bash = dir
```

`ask` 读一行标准输入（上限 4096 字节），`--no-input` 可禁用它。`bash =` 执行系统命令，只跑可信代码。

### 磁带

`tape_create(名)`、`tape_write(名) = {0..255}`、`tape_read(名)`、`tape_move(名) = (left|right)`：按需扩展的双向字节磁带，是语言图灵完备性论证的存储基础。

## v4：并发（atom）与 C DLL

### 并发组件 `#atom`

```cos
#atom
call = (atom)

start(normal) = {worker}
{
    lock {worker}
    {
        math_run {counter} + 1 = ? -int64
        if up = (ok) goto (push ({counter} = {(in = up)}))
    }
}

proton {chain} = {worker} -> {worker} -> {worker}
nuclear_reactor {main} = {chain}
dog(worker) = (pass : ok) (error : error)
```

- `lock {块} [-timeout 秒] { ... }` 锁住该块的父上下文变量，退出时释放；同块可重入；`-timeout` 超时返回 `error`。

  > 编译版把 `-timeout 秒` 换算成约 `秒×1000` 次 1 毫秒轮询。由于 Win32 `CRITICAL_SECTION` 与递归 pthread 互斥量对同线程可重入，超时只在**另一个线程正持锁**时才会触发；同线程内重复加锁会立即成功。
- `n-bomb {all}` / `n-bomb {块}` 强杀：状态置为 `stopped`，已写入的变量保留，不回滚。
- `neutron {父} = {子}` 运行时挂父块；`father = up` 仍是编译期复制上下文，两者互补。
- `proton {链} = {块1} -> {块2}` 定义执行链；`nuclear_reactor {父} = {链}` 点火，立即返回。
- 需要结果时用 `dog(块)` 等待其结束，或 `lock` 同步。
- 只有被 `proton` 链调度的块才交给原子线程；其余 `start(normal)` 块仍同步执行，执行完把状态写进 `dog`。因此同一个文件里既可以先写普通的 `start` + `dog`，后面再写并发链。

`dog` 对并发块可见的状态：`idle`（排队/未启动）、`running`、`ok`、`stop`、`error`、`stopped`（被强杀）。

解释器用真实线程实现；GCC 后端 Windows 用 Win32 线程、其他系统用 pthread（编译时自动加 `-pthread`）。

### C DLL 组件 `#dll`

```cos
#dll
call = (DLL)
#extern gettickcount () -> DWORD as GetTickCount
#link kernel32

calldll = (kernel32)
cdll = status (kernel32)
cdll = come (kernel32) (gettickcount)
cdll = out (kernel32)
```

- `#extern 名称 (类型, ...) -> 类型 [as 真实导出名] [stdcall]` 声明签名；默认 `cdecl`，Win32 API 通常需要 `stdcall` 或 `as` 指定真实导出名。
- 类型：`VOID INT UINT LONG DWORD WORD BYTE CHAR PTR STR FLOAT DOUBLE`（另接受 `LPCSTR HWND`）。
- `calldll = (库)` 加载（Windows `LoadLibrary`，Linux `dlopen`）；`cdll = out` 卸载；`cdll = status` 返回 `loaded` / `unloaded` / `missing`。
- 参数用括号传递：`cdll = come (库) (函数) (参数1) (参数2)`，短横线只留给编码与模式标志。
- `VOID` 返回固定为 `ok`；库/函数/参数错误返回 `error` 且 `err_count()` 递增。

**只加载你信任的 DLL**，风险与 `bash =` 命令相同。

### 行为对齐修复

- `array a = array_set(a, 1, 99)` 允许重绑定具名数组，解释器与编译版一致。
- `print` 未定义变量：两版都静默、状态为 `error`，并计入 `err_count()`。
- 新增 `exit(状态码)`：解释器由 CLI 作为退出码返回；编译版直接退出进程。
- `err_count()` 返回累计错误数；编译版输出已加 `fflush`，重定向到文件不会丢内容。

## v5：位运算、uint32、字节数组、矩阵与数值计算

v5 的全部特性都在解释器和 GCC 编译版中同时实现，并由测试逐项对照 Python 独立计算的期望值。

### 位运算与 `-uint32`

- 新增 `&`、`|`、`^`、`<<`、`>>`、`~`。位运算只能在整数模式下使用（`int32`/`int64`/`uint32`/`bigint`），在 `float64`/`complex` 下会明确报错。
- 新增 `-uint32` 模式：结果按 2³² 回绕而不是报溢出，`>>` 恒为逻辑右移。用户函数在 `uint32` 模式下返回 `uint32_t`，函数体内也使用无符号语义。
- `bigint` 模式下位运算以补码表示实现，`>>` 与 Python 一样是算术右移。
- 新增 `hex(value[, width])`（返回字符串）和 `unhex(text)`（建议配 `bigint` 模式，因为结果可能超出 64 位）。

### 字节数组

- `bytes name = [...]` 声明字节数组，元素必须是 `0..255` 的整数，越界会报错。
- `bytes_len(name)`、`bytes_get(name, i)`、`bytes_set(name, i, value)` 读写；与 `array` 一样按名原地修改，可在循环内使用。

### NxM 矩阵与线性方程组

- 矩阵写作嵌套列表 `[[a,b],[c,d]]`，**形状随值一起保存**：`matrix_transpose([[1,2,3],[4,5,6]])` 得到 3×2，而不是丢失维度变成 6 个元素的扁平表。
- `matrix_rows`、`matrix_cols`、`matrix_at(M, row, col)`、`matrix_mul(A, B)`、`matrix_transpose(M)`。
- `matrix2_inverse(M)`、`matrix3_inverse(M)`：仅支持实数方阵；奇异矩阵会报错。
- `solve_linear(A, b)`：高斯消元（列主元）解 `Ax = b`，返回普通数组，用 `array_at` 读取。`A` 必须是方阵且 `b` 长度等于行数。
- 矩阵值不能 `print`，也不能作为 `math_run` 标量结果，必须交给矩阵函数消费（与数组规则一致）。

### 数值积分、统计与特殊函数

- 积分：`trapz(array, h)` 梯形法、`simpson(array, h)` 辛普森法（要求奇数个样本且至少 3 个，对二次多项式精确）。
- 统计：`median`、`variance`（总体方差，与 `std` 一致）、`correlation`（皮尔逊相关系数）、`percentile(array, p)`（`p` 取 0–100，相邻样本线性插值）。
- 特殊函数：`lgamma`、`erfc`、`beta(a,b)`、`zeta(s)`（仅实数 `s > 1`，由直接求和加 Euler–Maclaurin 尾部修正实现，精度约 1e-15）。

示例：[bitops.cos](./examples/bitops.cos)、[linear_algebra.cos](./examples/linear_algebra.cos)、[numerical.cos](./examples/numerical.cos)、[sha256.cos](./examples/sha256.cos)、[aes128.cos](./examples/aes128.cos)。

> 想把当前全部语法（v1–v10）一次看全并直接运行，用 [full_syntax.cos](./examples/full_syntax.cos)：它按小节演示每一类语法，解释器与 GCC 编译版输出逐行一致。纯文档版的语法巡览见仓库外的 `宇宙语.txt`。

> 密码学示例仅用于验证语言能力，是正确性演示，不是常量时间实现。

## v6：内存回收、内联汇编与支持等级

设计取舍：用引用计数而非追踪式 GC（归零即释放，没有扫描线程、没有堆大小、没有调优参数）；不做成「必须声明」，因为默认全自动才是它该有的样子；临时值从 arena 线性分配、不移动对象、不做指针提升，因此 Context 里的变量天然持有持久副本，跨块引用不需要逃逸分析。

### 内存回收默认全自动

**不写任何声明就是全自动回收。** 变量赋值计数 +1，作用域退出 −1，归零即释放。没有扫描线程、没有堆大小、没有调优参数。

```cos
#gc = none    #% 不回收（测内存峰值、基准；不释放≠更快，是更危险）
#gc = semi    #% 引用计数（默认，等价于不写）
#gc = mark    #% 标记-清除，用于交叉验证 refcount
```

- 这三个值是**文件级调试开关，不传染**，不进函数签名、不沿调用链传播。
- `semi` 是零 STW 暂停的：归零那一刻同步释放。

### 连续分配（`#%memory = arena` / `static`）

临时值（表达式中间数组、矩阵工作区、`array_push` / `array_copy` 的副本）在 `arena` 下从 64 KiB 分块线性分配，在 `static` 下从编译期定长池分配；两模式都在整批归还点（数组语句之后、进程退出）复位，回收是 O(1)。

- **不移动对象、不做指针提升**：`Context` 中的变量和具名数组始终持有持久 `malloc` 副本，所以跨块引用天然安全，不需要逃逸分析。
- 因为不移动，`gc_pin` 只在 `#%memory = heap` 下允许；非堆模式下 pin 的缓冲区会在池复位时失效，因此解析期直接报错。
- 同一个块内的大量临时分配会累积到归还点：`arena` 追加新分块继续增长，`static` 到达池上限会报 `static memory pool is exhausted`。
- 临时池是进程级的，因此非 `heap` 模式不能使用 atom 块（解析期报错）。

### 手动干预

```cos
  = The_world (stop)    #% 暂停自动回收，归零对象进入待回收队列
  = The_world (force)   #% 立即回收队列中计数为零的对象（不动已 pin 的）
  = The_world (start)   #% 恢复自动回收，并处理积压队列
```

- 返回值空；`stop` 记深度可嵌套，`start` 减一层，减到零才恢复。
- 停表深度**不跨块继承**；块退出时未配对会清零并 warning。
- `#gc = none` 下三者皆空操作；`#gc = mark` 下 `force` 触发一次完整标记-清除。
- 它管「什么时候扫」，管不了「该不该扫」——判定由引用计数完成。

### `gc_pin` / `gc_unpin`

```cos
  = gc_pin (buf)
  cdll = come (winmm) (playsounda) (0) (buf) (0)
  = gc_unpin (buf)
```

把缓冲区交给异步外部库（如 `PlaySoundA` 的 `SND_MEMORY`）时必须成对调用，否则函数返回后计数归零、缓冲被释放，声音还在放。`gc_pin` 返回对象本身，可嵌套，`#gc = none` 下是空操作；它要求 `#%memory = heap`。

### `#pe` 已移除

写 `#pe` 报 `component 'pe' is not supported (removed in v6)`。它自 v1 起从未实现。要解析 PE 用普通字节操作：`bytes head = [0x4D, 0x5A]`。

### 高精度定点常数（bigint）

`#%math = bigint` 模式支持圆周率、自然常数和 √2 的定点表示，精度参数为小数点后的位数，范围是 `0..1000`：

```cos
#%math = bigint
math_run pi_digits(40) = ? -bigint
if up = (ok) goto (push ({pi40} = {(in = up)}))
print (<{pi40}>)

math_run pi_str(5) = ? -bigint
if up = (ok) goto (push ({pi5} = {(in = up)}))
print (<{pi5}>)

math_run e_str(10) = ? -bigint
if up = (ok) goto (push ({e10} = {(in = up)}))
print (<{e10}>)

math_run sqrt2_str(10) = ? -bigint
if up = (ok) goto (push ({sqrt2} = {(in = up)}))
print (<{sqrt2}>)
```

- `pi_digits(n)`、`e_digits(n)`、`sqrt2_digits(n)` 返回去掉小数点后的整数，例如 `pi_digits(5)` 为 `314159`。
- `pi_str(n)`、`e_str(n)`、`sqrt2_str(n)` 返回带小数点的文本，例如 `pi_str(5)` 为 `3.14159`。
- 两组函数都要在 bigint 数学模式下使用；精度超出范围或不是整数会报错。

### `kill -force`

```cos
var secret = {"sensitive text"}
kill {secret} -force
```

`-force` 是可选的；不带它的 `kill {name}` 只执行通常的变量销毁。GCC 编译版带 `-force` 时，会在释放变量当前持有的字符串或数组存储前覆盖该存储。解释器不保证清零；此选项也不保证清除副本、临时值、寄存器、转储或侧信道中的数据。

### 内联汇编（实验性）

```cos
#%asm = x86_64 (clobber: rax, rcx, rdi, memory)
#asm
{
    mov rax, 60
    xor rdi, rdi
    syscall
}
```

- 声明行写注释里，`#asm` 单独一行开标签，后面紧跟 `{ }`。
- 架构字段必填（v6 只实现 `x86_64`），不匹配即**编译期失败**并给出行号，不做静默降级。
- `clobber` 必填，`memory` 不能省；空体写 `(clobber: memory)`。
- 每个 `#asm` 块各写一行声明，不继承。
- 写成 `#asm = x86_64` 会被当编码声明报错——和 `#math = int64` 同一个坑。

### 支持等级

| 等级 | 涵盖 |
|---|---|
| **稳定** | 基本语法、`math_run`、数组/矩阵、位运算、atom、dll、`#gc = none\|semi\|mark`、`#%memory = heap\|arena\|static` |
| **实验性** | `#asm`、`#%maxlen` 容量策略、`gc_pin` 嵌套语义、`--heap-report` 峰值口径 |
| **不承诺** | 密码学恒定时间、汇编块优化器行为、全局零碎片、固定内存布局、并发是模拟而非并行 |

---

## v7：拆分文件、命名空间、错误处理、内存策略

### 跨文件 include

```cos
#%include = "lib/text.cos"
```

也可写成 `#%include` 加下一行 `#include` 的两行式。路径相对**声明所在文件**，绝对路径、循环包含、超过 16 层都报错。跨文件重复声明 `#%math` / `#%precision` 时保留首次，值不同报冲突。

### 命名空间与别名

```cos
#%namespace = sort
function quick(values) = values * 2
#%namespace = global
#%use = sort.quick as doubled

math_run doubled(4) = ?
math_run sort::quick(3) = ?
```

`#%namespace` 只作用于其后的函数声明，`global`（或 `main`）复位。未限定名不会自动搜索命名空间，必须写 `名称::函数(...)` 或用 `#%use` 生成转发别名。记录与运算符仍是全局的。

### 错误处理

```cos
try
{
    ask ({line})
}
catch
{
    push ({note} = {"input unavailable"})
}
finally
{
    push ({cleanup} = {"done"})
}
```

只允许写在顶层，`catch` / `finally` 至少有一个。`catch` 抓的是状态机层面的 `(error)`：try 体内某句把 `up` 置为 error 后余下语句不再执行。`finally` 总执行；没有 `catch` 时错误在 `finally` 后继续传播。除零、越界、递归超限这类硬失败直接终止，抓不住。

### 内存策略

```cos
#%memory = heap             # 默认：每个临时数组单独 malloc/free
#%memory = arena            # 临时数组从 64 KiB 分块 bump 分配
#%memory = static           # 从编译期定长池分配，零 malloc
#%maxlen = 1024             # 数组容量上限（static 模式必填）
#%on_oom = abort            # 容量超限：退出码 1
#%on_oom = error            # 容量超限：状态置 error，可被 catch 处理
```

三种模式只改变**临时值**（表达式中间数组、矩阵工作区、`array_push` / `array_copy` 的副本）的存储方式，`Context` 里的变量与具名数组始终用持久 `malloc`，所以三种模式输出逐字节相同。限制：`static` 必须同时写 `#%maxlen`；`gc_pin` 需要 `heap`；非 `heap` 模式不能用 atom 块（临时池是进程级、非线程安全）。

### 特性裁剪

```cos
#%features = no-bigint, no-array, no-matrix
```

只有这三个标签（`no-array` 隐含 `no-matrix`），被禁用的能力在**解析期**报错。声明后编译会加 `-ffunction-sections -fdata-sections -Wl,--gc-sections`，并让运行时按功能域切块。注意：GNU ld 上 `--gc-sections` 本来就能丢这些函数，实测体积几乎不变，收益在生成的 C 更短、编译更快。

### 泛型表达式函数

```cos
#%math = int64
generic function twice(x) = x * 2
```

按调用实参的数值类型工作，不引入用户类型或文本宏。

### 内存报告

```bash
python -m cosmic_interpreter.compiler program.cos --memory-report report.json
python -m cosmic_interpreter.compiler program.cos --heap-report
```

`--memory-report` 写静态 Context 估算（含 `memory_mode`、`maxlen`、`on_oom`、`gc_mode`、`static_pool_bytes`）；`--heap-report` 让产物退出时向 stderr 打 `memory: peak temporary bytes N`。`--max-context-bytes` 可做容量门禁，只约束主 Context 的静态记录占用。

## v8：动词优先语法（HOP）

v8 增加一层「动词在前、宾语在后、用 `存` 接目标」的语法糖。**旧写法完全保留**，两种写法可以混用。

```cos
打开 "log.txt" 存 文本
行数 文本 存 总行
查找位置 文本 "错误" 存 位置
替换文本 文本 里的 "ERROR" 为 "错误" 存 结果
print (<{总行}>)
```

规则：

- 目标变量用 `存` 引出：`动词 参数… 存 结果`。结果写进该变量，随后可直接 `print (<{结果}>)`。
- `打开` / `写入` 的路径与内置 `read_text` / `write_text` 一致，相对**当前工作目录**（不是脚本所在目录）；`#%include` 才是相对脚本目录的。
- 中文变量名由 HOP 层自动映射成内部 ASCII 名，核心解析器与 C 后端不需要支持中文标识符；报错信息会把内部名还原成你写的中文名。
- 助词 `里的` / `为` / `到` / `用` / `把` 可写可不写，只为读起来顺。
- **连续两句 HOP 之间自动插入 `require_ok`**：前一步失败立刻终止，报错带原始行号，不会带着坏数据继续跑。

支持的动词（返回值写在动词名里，不需要猜）：

| 组 | 动词 | 说明 |
|---|---|---|
| text | `打开` | 读文本文件，结果为文本 |
| text | `写入` | 写文本文件（`写入 路径 文本`） |
| text | `文本长度` | 返回字节数 |
| text | `查找位置` | 返回字节下标，找不到为 -1 |
| text | `替换文本` | 返回替换后的新文本 |
| text | `去空白` / `转大写` / `转小写` | 返回新文本 |
| text | `截取字节` | `截取字节 文本 起点 长度 存 结果` |
| text | `行数` | 返回行数（换行数，非空且不以换行结尾时补一行） |
| array | `数组长度` / `数组求和` / `数组均值` / `取元素` | 数组必须用裸名：`数组求和 数据 存 总量` |
| array | `追加元素` / `填充` | 生成新数组：`追加元素 数据 5 存 更多` |
| math | `平方根` `绝对值` `幂` `向下取整` `向上取整` `正弦` `余弦` `对数` | 数值运算 |

HOP 展开后仍是普通语句，因此解释器与 C 后端输出逐字节一致（有专门测试）。

**英文动词（v8.1）**：同一批能力提供 verb-object 风格的英文动词，供非中文母语者使用；目标关键字可用 `to` / `into` 代替 `存`。两种写法完全等价。

```cos
open_file "log.txt" to text
count_lines text to lines
replace_text text "ERROR" "error" to cleaned
text_length cleaned to bytes
square_root 81 to root
array data = [5, 1, 9]
array_total data to total
```

| 组 | 英文动词 |
|---|---|
| text | `open_file` `write_file` `text_length` `find_index` `replace_text` `trim_text` `upper_text` `lower_text` `slice_bytes` `count_lines` |
| array | `array_length` `array_total` `array_mean` `at_index` `push_item` `fill_array` |
| math | `square_root` `absolute_value` `power_of` `floor_of` `ceil_of` `sine_of` `cosine_of` `log_of` |

英文动词**只接受位置参数**（不支持英文助词），参数顺序与中文动词一致：源 → 目标，输出最后。

### `require_ok`

```cos
ask ({输入})
require_ok 2
```

前一步状态为 `(error)` 时立即终止，报错形如 `line 3: HOP 第 2 步失败: 前一步状态为 error`。不带步骤号也合法（`require_ok`）。它主要给 HOP 用，也可以手写。

### `line_count`

新增内置：`math_run line_count({文本}) = ? -int64`。空文本为 0；`"a\nb\n"` 为 2。

### v8.1 新增内置

数组在本语言里只能是数值（v9 起可以作函数参数和返回值），所以文本拆分用「计数 + 取字段」表达，没有 `split`/`join`。

| 分类 | 内置 | 语义 |
|---|---|---|
| 文本 | `split_count(text, sep)` | 分隔段数（`"a,b,c"` → 3） |
| 文本 | `field(text, sep, index)` | 取第 index 段（1-based），越界报错 |
| 文本 | `count_text(text, needle)` | 子串出现次数 |
| 文本 | `contains_text` / `starts_with` / `ends_with` | 返回 0/1 |
| 文本 | `repeat_text(text, n)` | 重复 n 次，结果上限定 16 MiB |
| 文本 | `pad_left` / `pad_right(text, width, char)` | 按**码点宽度**补齐 |
| 文本 | `line_at(text, index)` | 取第 index 行（1-based） |
| 文本 | `to_number(text)` | 文本转数值，失败报错 |
| 随机 | `random_secure(low, high)` | **CSPRNG** 闭区间整数：解释器 `secrets`；Windows `BCryptGenRandom`、Linux `getrandom` |
| 随机 | `random_bytes(n)` | n 个 0..255 的 CSPRNG 字节数组 |
| 时间 | `now_ms()` | Unix 毫秒 |
| 数组 | `sort_array` / `reverse_array` | 返回新数组，不改入参 |
| 数组 | `array_index_of(array, value)` | 首个相等下标，找不到 -1 |

```cos
#%math = int64
push ({csv} = {"1,2,3"})
math_run split_count({csv}, ",") = ? -int64
if up = (ok) goto (push ({列数} = {(in = up)}))
math_run to_number(field({csv}, ",", 2)) = ? -int64
if up = (ok) goto (push ({第二列} = {(in = up)}))
array data = [5, 1, 9]
array ordered = sort_array(data)
math_run random_secure(1, 6) = ? -int64
if up = (ok) goto (push ({骰子} = {(in = up)}))
```

编译版在 Windows 上会额外链接 `-lbcrypt`（仅 host 目标；交叉编译不加）。

### 标准库 `lib/`

`lib/` 是随发行版带的数值库，和 `packages/`（cosp 安装的第三方包）分工不同：

```cos
#%include = "lib/math.cos"
math_run factorial(5) = ? -int64
```

v8.1 起 `lib/` 有这些文件：

| 文件 | 内容 |
|---|---|
| `lib/math.cos` | `sq` `cube` `factorial` `max2` `min2` `clamp` |
| `lib/text.cos` | 按码点判定空白/数字/字母、CJK 宽度、大小写码点转换 |
| `lib/numtheory.cos` | `gcd` `lcm` `is_prime` `factor_count` `comb` `perm` |
| `lib/algo.cos` | `sorted_total` `reverse_total` `sorted_median` `has_value` `smallest3` `largest3` |
| `lib/data.cos` | CSV 数值统计（宏式 include：先定义 `CSV_TEXT`/`CSV_SEP`） |
| `lib/time.cos` | `ms_to_seconds` `seconds_to_ms` `elapsed_ms` `elapsed_seconds` `ms_to_minutes` |
| `lib/random.cos` | `secure_percent` `secure_bool` `secure_dice` `secure_between`（CSPRNG） |
| `lib/term.cos` | ANSI 常量 |
| `lib/array.cos`、`lib/io.cos` | 说明文档式，讲清当前后端限制 |

v9 起用户函数可以接收/返回文本、数组与记录，库里也补齐了 `text_len` / `text_upper` 这类真包装，
详见 [lib/README.md](./lib/README.md) 与下面的「v9 Delta」一节。

### 解释器热路径

v8 给表达式求值加了「计划缓存」：花括号变换、正则与 AST 只按表达式文本做一次，取值仍每轮读当前变量。实测一个两语句 `while` 循环从 **240 µs/次降到 57.7 µs/次（4.16×）**，语义与错误文案不变。可用 `python scripts/bench_loop.py` 复现。

## v9：文本、数组、记录成一等值

以前函数只能收发数字。v9 起参数可标 `: text` / `: array` / `: 记录名`，返回可标 `-> text` / `-> array` / `-> 记录名`；**不标则按函数体内的用法自动推断**。

```cos
function greet(name : text) -> text = upper(name)
function total(values : array) = array_sum(values)
function build(size) = array_fill(size, 7)
record Money { amount, currency }
function add_money(a : Money, b : Money) -> Money
{
    return Money(a.amount + b.amount, a.currency)
}
```

三条要点：文本参数与文本返回两后端都支持；数组按值传递（调用点拷贝，返回值走受管内存）；记录可作参数、返回值、在函数间传递，但字段仍是数值，`print` 仍到不了记录。

## v10：函数内输出、循环局部变量、文本拼接

```cos
function report(name : text, score)
{
    print (<{name} (and) " = " (and) {score}>)
    return score
}

function build_list(n) -> text
{
    let acc = ""
    let i = 1
    while (i <= n)
    {
        let piece = text_concat("item", to_text(i))
        if (i > 1)
        {
            set acc = text_concat(acc, ",")
        }
        set acc = text_concat(acc, piece)
        set i = i + 1
    }
    return acc
}
```

- **函数内 `print`**：可直接输出，支持 `(and)` 拼接。注意文本形参要标 `: text`——`print` 不参与参数类型推断。
- **循环内 `var`**：每轮都是新绑定，可以重复声明；顶层与非循环块里的重复声明仍然报错。
- **文本拼接**：`text_concat(a, b)`、`text_join(array, sep)`、`to_text(v)`，可赋值、可返回，这是写代码生成器的前提。
- **记录文本字段**：`record Node { kind : text, value }`，C 侧是真 `struct`，按值传参与返回。
- **递归深度**：见上面的 v10 递归深度条目。

---

## 其余语法速查

上面按版本分节讲过的不再重复，这里只补零碎的几条：

- `#%...`：注释。`#execute`、`#math`：导入内置组件；块里单写一个标识符（如 `hello`）是空操作。
- `if (条件) { ... } else { ... }` / `while (条件) { ... }` / `break` / `continue`。
- `while` 体内用 `var` 声明局部变量（v10 起每轮重新绑定）。
- `function 名(参数) = 表达式` 可递归，`choose(条件, 真, 假)` 惰性求值。
- `function 名(参数) { ... }` 多语句函数体：`let` / `set` / `if` / `while` / `return`，没 `return` 时返回 `0`。
- 函数体内可声明嵌套函数，按值捕获外层参数与 `let` 局部名；无函数值、无写回外层。
- `record 名 { 字段, ... }` + `operator + 名 = { 表达式, ... }`：数值记录与静态运算符重载（`+ - * /`，只允许 `left` / `right` 的字段，解析期展开）。
- `array 名 = [1, 2, 3]` 声明具名数组；`array_set` / `array_push` 原地更新，`array_len` / `array_at` / `array_sum` / `dot` / `mean` / `std` / `min` / `max` 读取。数组只能装数字，`print` 打不了，要用函数取值。
- `read_text(path)` / `write_text(path, text)`：UTF-8 读写，读上限 16 MiB。
- `goto (to = stop)` 结束当前块；`goto (push (...))` 执行变量赋值。
- `dog(名) = {ok}` 检查块状态；`dog(名) = (stop : error) (pass : ok)` 是状态白名单映射，`pass` 等同 `ok`。
- `print` / `kill` / `sleep` / `math_run` / `bash = 命令`。
- `#%math` 每个程序只能声明一次；`#%precision = 17` 只改显示精度（范围 1–17，默认 15），不增加 `double` 的实际精度。
- 数学常量：`pi`、`e`、`tau`。三角函数用弧度。
- 科学函数：`abs` `sqrt` `exp` `log` `log10` `log2` `floor` `ceil` `pow` `fmod` `hypot` `erf` `erfc` `gamma` `lgamma` `beta` `zeta` `sin` `cos` `tan` `asin` `acos` `atan` `atan2` `sinh` `cosh` `tanh`。
- 编译选项：`--simd`（`-O3 -ftree-vectorize`）、`--fast-math`（可能改变 NaN / 无穷 / IEEE-754 边界语义，默认关）、`--keep-symbols`（默认 `-s` 剥符号）、`--max-loop-iterations N`。
- `blas_dot` / `lapack_determinant2` 绑定系统 BLAS / LAPACK，编译器按需链接 `-lblas` / `-llapack`；运行机器要自备这些库。

## 执行和数据规则

- `ok` 表示语句执行完成；执行块正常完成时返回 `ok`，通过 `to = stop` 结束时返回 `stop`。
- 解释器 API 和 GCC 编译产物默认不限制循环轮数，以保留语言的无界循环语义；命令行运行时建议配置循环上限，防止意外死循环。
- 变量以字节保存：声明了编码（例如 `#name = GBK`）时使用指定编码，否则使用 UTF-8。`print(...)-GBK` 可指定输出编码。
- 打印不存在或尚未赋值的变量会得到 `error` 状态；打印已被 `kill` 的变量会得到 `error` 状态和 `killer` 结果。
- `father` 会复制当前变量和导入到紧随其后的执行块中；块内变量修改不会反向写回父上下文。
- `dog(status)` 检查最近完成的执行块状态；`dog(名称)` 检查指定名称执行块的状态。
- `jump` 使用文件中的物理行号（包括空行和注释）；只可跳到当前执行块内直接的普通语句，避免跳入嵌套控制结构。
- `math` 和 `execute` 是内置组件。`pe` 在 v6 起已移除，写入会报编译错误。
- `#%math = int32`、`#%math = int64`、`#%math = float64`、`#%math = complex`、`#%math = bigint`、`#%math = uint32` 设置数学计算模式；单条表达式可用 `math_run ... = ? -complex` 或其他模式覆盖。`#%math` 每个程序只能声明一次。
- `#%precision = 17` 将 `float64` 数值结果设为 17 位有效数字（范围 1–17，默认 15）；该设置同时作用于解释器和 GCC 编译结果。它只控制显示精度，不会增加 `double` 的实际计算精度。
- 数学表达式支持已赋值变量 `{name}`、带引号的字符串字面量 `{"text"}`、括号、`+ - * / // % **`、比较/逻辑运算，以及 `pi`、`e`、`tau` 常数。未知变量引用会报错。
- 内置科学函数包括 `abs`、`sqrt`、三角/反三角和双曲函数、`exp`、`log`、`log10`、`log2`、`floor`、`ceil`、`pow`、`fmod`、`hypot`、`erf`、`erfc`、`gamma`、`lgamma`、`beta`、`zeta`，以及 Unicode 转换 `chr(codepoint)`、`ord(character)`。`ord` 参数须是一个 Unicode 字符（可使用字符串字面量或文本变量）；`chr` 的参数须是有效 Unicode 标量值。受当前 C 字符串表示限制，NUL 字符不支持。三角函数采用弧度。表达式由受限语法解析，不会执行任意 Python 代码。
- `strlen({s})` 返回字符串的 Unicode 字符数；`char_at({s}, i)` 返回从零开始索引 `i` 处的一个 Unicode 字符。越界或非整数索引会报错。
- `atan(x)` 返回反正切弧度，`atan2(y, x)` 根据两个坐标计算象限正确的角度。`rag (max : 上限) (min : 下限)` 均匀返回闭区间 `[下限, 上限]` 中的整数；上下限必须为 int32 范围内整数且下限不大于上限。它用于模拟/计算，不是密码学随机数。
- `chr`/`ord` 提供 Unicode 码点与单字符之间的转换。v5 起语言已具备位运算、字节数组、SHA-256 与 AES-128 示例；但这些都是**正确性演示，不是常量时间或侧信道安全的密码学库**，不要用于保护真实机密。
- `int32`/`int64` 要求计算结果为整数并处于类型范围；非整数科学计算使用 `float64`。`complex` 使用 double 精度复数；`bigint` 是最多 1,000,000 位的任意精度整数，不支持任意精度十进制，解释器与 GCC 后端的整数运算边界一致。
- `read_text(path)` 与 `write_text(path, text)` 以 UTF-8 读写文本，读取上限 16 MiB；文件 I/O 会访问源代码指定的路径，只运行可信程序。

## 图灵完备性与资源保护

在抽象语言模型中，`while` 和 `if/else` 提供循环及条件分支，双向动态磁带提供可按需扩展的存储空间，磁带单元使用有限符号集 `0..255`。这组构造可模拟标准图灵机，因此语言核心在**理想化的无界内存、无界执行时间语义下**是图灵完备的。

现实机器的内存和运行时间有限，不能实际提供无限磁带。解释器 API 默认不设循环上限；使用解释器 CLI 时可用 `--max-loop-iterations N` 保护自己。GCC 编译器默认不设循环上限；编译时可用同名选项加入保护。若程序可能不终止，请设置上限或只运行可信代码。

例如给解释器和编译器添加每个循环最多 100 万轮的保护：

```powershell
python -m cosmic_interpreter.cli examples\demo.cos --max-loop-iterations 1000000
python -m cosmic_interpreter.compiler examples\demo.cos -o build\demo.exe --max-loop-iterations 1000000
```

## 运行测试

```powershell
python -m unittest discover -s tests -v
```
