# Cosmic ASM 完整语法参考

> 本文是逐条语法说明。快速上手看 [README.md](./README.md)。


这是一个使用 Python 编写的 Cosmic ASM 解释器 MVP，针对 [示例程序](./examples/demo.cos) 和目前整理出的语法实现。它还不是完整的语言规范实现；尚未支持的语法会尽可能给出带源码行号的错误。

## 运行

在项目目录中运行：

```powershell
python -m cosmic_interpreter.cli 路径\到\程序.cos
```

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

`function name(params) = expression` 定义数值表达式函数；`choose(condition, a, b)` 惰性选择分支，因此可用于递归基线。数值数组索引从 0 开始；用 `array scores = [1, 2, 3]` 声明具名数组，它会在同一上下文的多个 `math_run` 之间保留。`array_set(scores, index, value)` 和 `array_push(scores, value)` 会原地更新具名数组；也可以将它们嵌套在 `array_sum`、`array_at`、统计函数或 `array_len` 等数组操作里。传入列表字面量时，更新结果是临时数组，也可直接保存：`push ({saved} = array_set(scores, 1, 99))`。变量可以保存字符串或数组，数组赋值（例如 `push ({copy} = {saved})`）按值复制；之后可将该变量重新赋值为另一种类型。数组可用 `array_len`、`array_at`、`array_sum`、`dot`、`mean`、`std`、`min`/`max`，v5 起还可用 `median`、`variance`、`correlation`、`percentile`、`trapz`、`simpson`。数组统计目前仅接受实数；在 complex mode 下，具名数组和变量数组可保存复数并用于 `array_len`、`array_at`、`array_sum`、`array_set`、`array_push`、`dot`。`std` 计算总体标准差（`variance` 同样是总体方差），统计函数不接受空数组。执行块通过 `father = up` 继承上下文时，具名数组和变量数组都会复制，子块中的更新不会影响父上下文。数组值不是标量，不能直接作为 `math_run` 结果打印；请用数组函数提取数值。矩阵方面，`matrix2_det` 和 `matrix2_mul_entry` 仍是 2×2 便捷函数，v5 另提供任意 NxM 的 `matrix_rows`/`matrix_cols`/`matrix_at`/`matrix_mul`/`matrix_transpose`、`matrix2_inverse`、`matrix3_inverse` 和 `solve_linear`。

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

## 打包 Windows EXE 和 MSI 安装程序

项目提供一个 PyInstaller 打包脚本，会把解释器和编译器所需的 Python 模块分别捆绑为两个控制台程序，并使用 WiX Toolset 生成 MSI：

- `dist\CosmicInterpreter.exe`：运行 `.cos` 源文件。
- `dist\CosmicCompiler.exe`：翻译为 C 并调用 GCC 编译。
- `dist\CosmicAsm.msi`：安装上述两个程序到 64 位 Windows 的 `Program Files`。

需要 Windows、Python、PyInstaller、.NET SDK 和 GCC。安装 PyInstaller 依赖并运行脚本：

```powershell
python -m pip install -r requirements-build.txt
.\packaging\build.ps1
```

脚本会通过 .NET SDK 自动还原项目固定的 WiX SDK 版本，因此首次构建需要网络连接和 NuGet 包源。MSI 安装和卸载通常需要管理员权限。安装后的解释器可直接从命令行运行，例如：

```powershell
"C:\Program Files\Cosmic ASM\CosmicInterpreter.exe" examples\demo.cos
```

`CosmicCompiler.exe` 需要另外安装 GCC，并确保 `gcc` 可从命令行找到；MSI 不包含 GCC。两个 EXE 都是控制台程序，使用方式与上面的 CLI 参数相同。

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

> 密码学示例仅用于验证语言能力，是正确性演示，不是常量时间实现。

## v6：内存回收、内联汇编与支持等级

设计细节见 [docs/v6-gc-asm.md](./docs/v6-gc-asm.md)。这里只列语法。

### 内存回收默认全自动

**不写任何声明就是全自动回收。** 变量赋值计数 +1，作用域退出 −1，归零即释放。没有扫描线程、没有堆大小、没有调优参数。

```cos
#gc = none    #% 不回收（测内存峰值、基准；不释放≠更快，是更危险）
#gc = semi    #% 引用计数（默认，等价于不写）
#gc = mark    #% 标记-清除，用于交叉验证 refcount
```

- 这三个值是**文件级调试开关，不传染**，不进函数签名、不沿调用链传播。
- `semi` 是零 STW 暂停的：归零那一刻同步释放。

### 连续分配

每个 `start` 块（含 `if` / `while` 作用域）拥有一个 arena，块内分配线性递增、地址相邻，块退出时整块归还（O(1)）。

- 出块的值通过 `push` 提升到父块 arena，代价是一次 memcpy。
- 钉住区（`gc_pin`）永不移动，供外部 DLL 持有裸地址。
- 移动只发生在安全点：块退出、`The_world (force)` 内部；不发生在 DLL 调用期间、`lock` 临界区内、`nuclear_reactor` 链执行期间。

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

把缓冲区交给异步外部库（如 `PlaySoundA` 的 `SND_MEMORY`）时必须成对调用，否则函数返回后计数归零、缓冲被释放，声音还在放。`gc_pin` 返回对象本身，可嵌套，`#gc = none` 下是空操作。

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
| **稳定** | 基本语法、`math_run`、数组/矩阵、位运算、atom、dll、`#gc = semi`、块内连续分配 |
| **实验性** | `#asm`、`#gc = mark`、`gc_pin` 嵌套语义、压缩触发时机 |
| **不承诺** | 密码学恒定时间、汇编块优化器行为、全局零碎片、固定内存布局、并发是模拟而非并行 |

---

## 当前支持的语法

- `#%...`：注释。
- `#execute`、`#math`：导入组件。普通执行块中的单独标识符（例如 `hello`）目前作为空操作处理。
- `#name = GBK`：指定变量的存储编码，不会初始化变量值。
- `var name = {"初始文本"}`：显式声明并初始化字符串变量。
- `push ({name} = {"文本"})`：将字符串写入变量。目标变量此前没有声明时会自动创建为字符串变量。花括号中的 `{name}` 始终表示变量引用；`{"text"}` 才是字符串字面量，不会再把未声明名称悄悄当作文本。用 `(and)` 连接多个片段，例如 `push ({c} = {a} (and) {b})` 拼接变量，`push ({greeting} = {"Hello, "} (and) {name})` 拼接文本与变量。`{(in = up)}` 特殊表示取上一行结果。字符串字面量使用 JSON 风格的双引号与转义。
- `call = (组件)`：调用已导入的组件。
- `father = up` 或 `father = (up)`：让紧随其后的执行块继承上方上下文中的变量和导入。
- `start(normal) = {名称}`、`start(loop) = {名称}`：定义普通执行块或循环执行块。
- 执行块后可选 `else { ... }`：仅当执行块以 `stop` 或 `error` 状态结束时执行一次，正常完成时跳过。
- `jump(行号)`：跳到当前执行块中指定的物理源代码行；目标必须是同一块内的直接、非控制结构语句。不能跨块跳转或跳入 `if`/`while`/其他执行块。
- `if up = (状态) goto (...)`：检查上一行状态。`if up {*} = (值)` 检查上一行结果；`if up {变量名} = (值)` 检查指定变量。
- `if (条件) { ... } else { ... }`：按数值条件执行分支。
- `while (条件) { ... }`：循环执行代码块。
- `function 名称(参数, ...) = 表达式`：定义可递归的数值函数；`choose(条件, 真值, 假值)` 会惰性计算被选中的分支。
- `generic function 名称(参数, ...) = 表达式`：声明按调用实参数值类型工作的表达式函数；不增加用户类型或任意文本宏。项目脚手架和本地依赖由 `cosp` 命令管理，详见 [cosp 使用说明](./docs/cosp.md)。
- 数值数组可通过 `array name = [1, 2, 3]` 保存，也可通过 `push ({name} = [1, 2, 3])` 将数组结果保存到变量；变量中的数组按值复制，并可再赋值为字符串。complex mode 支持复数具名数组和数组变量。
- `read_text(path)` 与 `write_text(path, text)`：以 UTF-8 读写文本，读取最大 16 MiB。
- `goto (to = stop)`：结束当前执行块；只允许在执行块中使用。
- `goto (push (...))`：满足条件时执行变量赋值。
- `dog(名称) = {ok}`、`dog(名称) = {stop}`：检查指定执行块的最终状态。条件不匹配时会报告死锁错误。
- `dog(名称) = (stop : error) (pass : ok)`：状态白名单映射。左侧列出允许的执行块状态，右侧是 `dog` 产生的状态；`pass` 等同于 `ok`。状态不在白名单中会报告死锁错误，映射左侧状态不能重复。
- `tape_create(name)`、`tape_write(name) = {0..255}`、`tape_read(name) = ?`、`tape_move(name) = (left|right)`：创建和操作按需扩展的双向磁带。
- [冒泡排序示例](./examples/bubble_sort.cos) 使用变量保存四个元素并通过相邻比较交换排序。当前语法尚不支持数组下标和动态磁带写入，因此示例固定排序四个整数。
- `print`、`kill`、`sleep`、`math_run`，以及示例中的嵌套 `bash = 命令` 形式。

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
