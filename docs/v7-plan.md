# v7 计划：可组合性

> 状态：**实施中；下方原始盘点已过时**
> 当前实现已包含若干原计划未实现的能力。以下第零节和第一节保留为历史规划背景，不可作为当前代码状态或测试基线；请以代码、测试及本文件末尾的当前状态为准。

---

## 零、先说清楚：v6 只落地了两项

> **历史记录（编写时）**：本节中的状态、路径、测试数和结论是旧盘点，未随代码更新。当前 `#pe` 已移除；V6/V7 的实际完成状态正在下方逐项校正。

盘完实际代码得出的结论，必须先摆出来，否则 v7 的计划全是空中楼阁。

> 已更新：v6 的已落地部分已提交并合入 `main`（`e7035b6`），全量测试 **223 项通过（1 skip）**。

| v6 承诺 | 文档 | 代码 | 实测 |
|---|---|---|---|
| 移除 `#pe` | ✅ 已写 | 历史记录 | 当前 parser 对 `#pe` 给出明确不支持错误 |
| `#gc = none/semi/mark` | ✅ 已写 | ❌ 未做 | 解析器不认 `#gc` |
| `gc_pin` / `gc_unpin` | ✅ 已写 | ❌ 未做 | 无此内置 |
| `The_world` | ✅ 已写 | ❌ 未做 | 无此内置 |
| arena 连续分配 | ✅ 已写 | ❌ 未做 | 解释器无 arena 概念 |
| `#asm` 标签式 | ✅ 已写 | ❌ 未做 | 解析器无 asm 语句 |
| `pi_digits` / `pi_str` / `e_*` / `sqrt2_*` | — | ✅ **已实现** | `runtime.py:598-599` 一带 |
| `kill {name} -force` | ✅ 已写 | ✅ **已实现** | 编译版生成 `secure_wipe()`；解释器不擦（README 已写明） |
| 位运算 / uint32 / 矩阵 / 积分 | — | ✅ 已实现（v5） | — |
| `cosp` 包管理器 | ✅ 已写 | ✅ **已实现** | 9 个命令，`cosp.lock` 哈希校验有效 |

**以上进度总结为历史记录，不能代表当前实现状态。**

## 当前实现快照（2026-11-10）

- 最近完整测试：**311 passed, 1 skipped, 58 subtests**；`compileall` 与 `git diff --check` 通过；Windows → WSL 交叉编译 + QEMU 运行通过，`examples/v7_features.cos` 在 RISC‑V 下输出与解释器一致。
- 已实现：`#pe` 移除、`ask`/`--no-input`、字符串与字节/数组辅助函数、相对 include、cosp TOML 插件元数据与 HTTPS ZIP 依赖、RISC-V Linux WSL 交叉编译路径。
- Unicode 字符串大小写：`upper` / `lower` 对每个码点映射，支持一对多映射；C 后端按需嵌入编译时 Python Unicode 表，不依赖 locale。映射采用逐码点语义，不做上下文相关转换。
- 内存报告/主 Context：编译器可选生成 JSON `.memreport`，报告目标 ABI 的变量/数组记录数及单个 Context 的记录存储字节数；生成 C 将主程序 Context 放入静态存储区（`.bss`）。`--max-context-bytes` 可限制该静态 Context。报告不估算堆、嵌套/并发 Context 或运行时栈峰值。
- 函数：生成 C 时仅输出从主程序表达式可达的用户函数，并递归保留其依赖；保留单表达式函数。多语句函数体现支持 `let`、`set`、`if`/`else`、`while`、`break`、`continue` 与 `return`（未执行到 `return` 时返回 `0`），解释器用独立帧执行、C 后端生成真实函数体并复用 `_c_expression` 的局部名映射；函数内循环同样受 `--max-loop-iterations` 约束，递归上限为 256。函数体仍不支持数组/矩阵运算、`push` 等副作用语句与外部状态。此裁剪仅针对用户函数，不是 C 运行时裁剪或库按需导入。
- 闭包：函数体内可声明单行嵌套函数，按值捕获外层参数与 `let` 局部名；捕获在调用点取当前值并以隐藏实参传入（解释器为局部函数表 + 捕获绑定，C 后端生成 `user_<outer>__<inner>` 并把捕获作为尾随参数）。无函数值、无逃逸、无写回外层，声明在函数体内提升。执行块内的嵌套函数仍未支持。
- 记录与运算符：`record Name { field, ... }` 声明值语义数值记录，函数体内用 `let p = Point(1, 2)`、`p.field` 读写、`let copy = p` 逐字段复制，`operator + Name = { ... }`（`+ - * /`）在解析期静态展开为字段运算。记录不能存入变量上下文、不能作为返回值；运算符两侧必须是同类型记录局部名。
- 异常：顶层 `try { } catch { } finally { }`，`catch` 捕获语言状态机层面的 `(error)`，`finally` 总是执行且在无 `catch` 时错误继续传播；硬失败（除零、越界、递归超限）两端均直接终止。`try` 只允许出现在顶层语句。
- 命名空间：`#%namespace = 名称` 作用于其后的函数声明（内部名 `名称__函数`），`#%namespace = global` 复位；调用须写 `名称::函数(...)`，或用 `#%use = 名称.函数 as 别名` 在解析期生成转发函数。未限定名不会搜索命名空间；记录与运算符仍为全局。
- V6 内存与汇编：`#gc = none|semi` 已落地（`none` 让编译版释放函数变空操作）；`gc_pin`/`gc_unpin` 固定数组缓冲区；`The_world (stop|force|start)` 实现可嵌套的延迟释放（解释器同步维护 pin 与停表深度）。`#gc = mark` 实现为「延迟到 force/退出统一清扫」的交叉验证模式。`#%asm = x86_64|arm64 (clobber: ...)` + `#asm { ... }` 原样透传 `__asm__ volatile`，架构/目标不符在解析期报错，解释器拒绝执行。
- 裁剪与按需导入：`#%features = no-bigint / no-array / no-matrix`（`no-array` 隐含 `no-matrix`）在解析期禁止对应语言能力并给出明确诊断；声明后编译追加 `-ffunction-sections -fdata-sections -Wl,--gc-sections`。运行时代码按功能域切块：`COSMIC_FEATURE_BIGINT` 排除 BigInt 与定点常量运行时，`COSMIC_FEATURE_ARRAY` 排除数组统计（mean/std/median/variance/correlation/percentile/trapz/simpson/min/max），`COSMIC_FEATURE_MATRIX` 排除矩阵与 `matrix2_*` 辅助。实测：GNU ld 的 `--gc-sections` 本就能丢弃这些不可达函数，因此 host 上可执行文件体积几乎不变（hello 级 41472 → 40960 B）；收益是生成 C 更短、编译更快，以及没有链接期裁剪的工具链。生成 C 只输出从主程序表达式可达的用户函数（含 include 进来的库函数）。
- 内存模式：`#%memory = heap|arena|static`、`#%maxlen`、`#%on_oom = abort|error` 已实现。三种模式只改变临时值（表达式中间数组、矩阵工作区、`array_push`/`array_copy` 副本）的存储：`heap` 逐个 malloc/free，`arena` 从 64 KiB 分块 bump，`static` 从编译期定长池（1 MiB）分配；`Context` 变量与具名数组始终是持久 malloc 副本，因此三种模式输出逐字节一致（有双后端测试）。临时池在整批归还点复位，实现块级 O(1) 回收，不做对象移动或指针提升。容量超限在两端一致：`abort` 报 `array capacity exceeded` 并退出 1，`error` 置语句状态为 `(error)` 且保留原数组。限制（解析期报错）：`static` 必须声明 `#%maxlen`；`gc_pin` 要求 `memory = heap`；非 `heap` 模式禁止 atom 块（临时池为进程级、非线程安全）。
- `#gc = mark` 已实现：无指针/无别名使标记阶段退化为“所有临时分配延迟到 `The_world (force)` 或进程退出统一清扫”，用作 refcount 路径的交叉验证。
- 内存报告：`--memory-report` JSON 增加 `memory_mode` / `maxlen` / `on_oom` / `gc_mode` / `static_pool_bytes`；`--heap-report` 编译出退出时向 stderr 打印 `memory: peak temporary bytes N` 的二进制（峰值口径为自上次整批归还以来的临时分配峰值）。
- 仍未验证：发行打包（`packaging/build.ps1` 的 PyInstaller + WiX 流程）在本环境未跑通；仓库仍无 CI 配置。
- 本快照不是 V7 完成声明；剩余项是发行打包验证与 CI。

这件事对 v7 的影响很直接：**v7 不能假设 GC 存在**。如果 v7 的新特性依赖「对象会被自动回收」，那它得等 GC 真正落地，不能并行。

---

## 一、按实测盘点：语言现在哪里最疼

不是凭印象，是扫代码和文档得出的。

### 1.1 已经够用的

| 能力 | 现状 |
|---|---|
| 数值 | int32 / int64 / uint32 / float64 / complex / bigint 六档，`math_run` + 40+ 科学函数 |
| 数组 | `array_at` / `array_set` / `array_len` / `array_push` / `array_sum` / `dot` / `mean` / `std` / `min` / `max` |
| 字节串 | `bytes_len` / `bytes_get` / `bytes_set` |
| 矩阵 | NxM、`matrix_mul` / `matrix_transpose` / `matrix2_inverse` / `matrix3_inverse` / `solve_linear` |
| 统计 | `median` / `variance` / `correlation` / `percentile` / `trapz` / `simpson` |
| 高精度常数 | `pi_digits` / `pi_str` / `e_digits` / `e_str` |
| 并发 | `#atom` / `lock` / `proton` / `nuclear_reactor` / `dog` |
| 外部调用 | `#dll` / `#extern` / `#link` / `cdll` / `calldll` |
| 控制流 | `if` / `while` / `jump(行号)` / `goto (to = stop)` / 块后 `else` |
| 文本函数 | `strlen` / `char_at` / `chr` / `ord` —— **只有这四个** |

### 1.2 三个真正卡脖子的地方

**① 函数只有一行**

```cos
function f(x) = x * x + 1
```

这是**唯一**的函数形态——右边必须是一个表达式。没有函数体、没有局部变量、没有 `return`、没有循环。

想写多步逻辑只能靠 `start` 块 + `father` + `jump`：

```cos
father = up
start(normal) = {calc}
{
    math_run {n} * 2 = ? -int64
    if up = (ok) goto (push ({tmp} = {(in = up)}))
    math_run {tmp} + 1 = ? -int64
    if up = (ok) goto (push ({out} = {(in = up)}))
}
```

一个「乘二加一」要六行，还得自己 `father`、自己 `goto`。**这是复杂度失控的主因**，比任何缺的特性都更消耗写代码的人。

**② 一个程序只能一个文件**

全项目搜不到 `include` / `#import` / 模块机制。所有工具函数只能复制粘贴进每个 `.cos`。`examples/` 里那几个排序、加密示例，各自都有一份自己的辅助代码。

**③ 字符串库只有四个函数**

`strlen` / `char_at` / `chr` / `ord`。没有 `substr`、没有 `find`、没有 `split`、没有 `join`、没有 `trim`、没有大小写转换、没有 `replace`。

写一个读取并解析文本文件的程序，只能一个字符一个字符 `char_at` 拼。

---

## 二、v7 主题建议：可组合性

**一句话**：让程序能长大，而不是让语言会变魔术。

三条主线，全部指向同一个问题——**现在拼不出中等规模的程序**：

| 主线 | 解决什么 | 判据 |
|---|---|---|
| **A. 多语句函数体** | 逻辑无法封装，只能靠块和 goto 拼 | 能写出带局部变量和循环的函数 |
| **B. 跨文件 include** | 代码无法复用，只能复制 | 两个 `.cos` 共享一个库文件 |
| **C. 字符串库** | 文本处理不可用 | 能读入一行文本并按分隔符切分 |

三条都不新增范式，只补「写程序本来就需要的东西」。

---

## 三、主线 A：多语句函数体

### 3.1 目标形态

```cos
function gcd(a, b)
{
    while (b != 0)
    {
        math_run {a} % {b} = ? -int64
        if up = (ok) goto (push ({tmp} = {(in = up)}))
        math_run {b} = ? -int64
        if up = (ok) goto (push ({a} = {(in = up)}))
        math_run {tmp} = ? -int64
        if up = (ok) goto (push ({b} = {(in = up)}))
    }
    return {a}
}
```

### 3.2 必须解决的语义问题

| 问题 | 决定 |
|---|---|
| 局部变量是否隔离 | **隔离**。函数体有自己的变量作用域，参数的 `father` 是函数作用域，不是调用者 |
| 能不能改调用者的变量 | **不能**。与块一致，按值复制；要回传就 `return` |
| 递归 | **允许**，但要有深度上限（默认 1000，可配），否则栈会炸 |
| 单表达式形态保留吗 | **保留**，`function f(x) = expr` 依然合法，等价于 `{ return expr }` |
| `return` 只在函数内合法 | 是。在函数外写 `return` → 编译期报错 |
| 无 `return` 时的返回值 | 返回最后一行结果（与块的状态规则对齐），状态 `ok` |

### 3.3 这是本版风险最高的一项

理由：函数体要新增一层作用域，而现在的作用域模型只有「块」和「`father` 继承」两种。加函数作用域会**碰 `father` 的全部语义**，而 `father` 是这语言最核心也最脆的东西。

**必须先做一件事**：把现有 `father` 语义写成测试用例固化下来，再加函数作用域。否则改一处，块和函数两边的行为会一起漂。

判据：加完函数作用域后，**211 项现有测试一项都不许变**。

---

## 四、主线 B：跨文件 include

### 4.1 目标形态

```cos
#%include = "lib/sort.cos"
```

沿用现有的「声明藏在注释里 + 单独标签」两行式，和 `#math`、`#asm` 一致：

```cos
#%include = "lib/sort.cos"
#include
```

（也可以只写一行 `#%include = "..."`，作为简写形式；具体取哪种见第七节待拍板项。）

### 4.2 规则

- **路径相对当前文件**，不接受绝对路径（防误引用系统文件）。
- **循环包含直接报错**，不做「只包含一次」的静默处理——静默会让依赖关系不可见。
- **深度上限 16 层**。
- 被包含文件的 `#%math` / `#%precision` 等全局设置：**取第一个声明者**，后包含的冲突声明报错。
- 被包含文件里的 `function` / `array` / `bytes` 声明，进入包含者的顶层作用域。
- **编译版和解释器行为必须逐字符一致**：包含顺序、冲突报错文案全统一。

### 4.3 顺带解决「库往哪放」

包含机制一有，就需要一个约定目录。建议：

```
D:\ASM\CosmicInterpreter\lib\      标准库（随发行版提供）
  sort.cos     排序
  str.cos      字符串扩展（见主线 C）
  text.cos     文本读写
```

`examples/` 里现有那些手工复制的辅助代码，v7 期间逐步收进 `lib/`。

---

## 五、主线 C：字符串库

> 以下是最初编写时的提案；字符串函数已在后续实现。当前 `upper`/`lower` 采用逐 Unicode 码点映射，细节见语法文档和本文件的当前实现快照。

现有四个函数不够，补一组最小的：

| 函数 | 签名 | 说明 |
|---|---|---|
| `substr` | `(s, start, len)` | 越界截断，不报错 |
| `find` | `(s, sub)` | 返回下标，找不到返回 `-1` |
| `split` | `(s, sep)` | 返回数组；`sep` 为空串按字符切
| `join` | `(arr, sep)` | 数组转字符串；非字符串元素转十进制 |
| `trim` | `(s)` | 去首尾空白（空格/`\t`/`\r`/`\n`） |
| `upper` / `lower` | `(s)` | 历史提案仅处理 ASCII；当前实现逐 Unicode 码点映射 |
| `replace` | `(s, from, to)` | 全部替换；`from` 为空串报错 |

### 5.1 一条明确的不承诺（历史提案）

历史提案：`upper` / `lower` **只做 ASCII**，不做 Unicode 大小写折叠。此提案已被当前逐码点 Unicode 映射实现取代。

理由：Unicode 大小写映射需要查表（`İ` 一个字符能展开成两个），那会把运行时体积翻倍，而且和现在的字节存储模型冲突。要做就等有 Unicode 表的那一版。

文档里必须写这一条，否则用中文的人会以为 `upper("中文")` 应该做什么。

### 5.2 编码边界

现有字符串按字节存，声明编码（`#name = GBK`）时按指定编码解释。新加的字符串函数**一律按字节位置操作**，不按「字符」。

这条要和现有的 `strlen`（返回 Unicode 字符数）区分清楚——`strlen` 是字符，`substr` 是字节。这个不一致必须在文档里明写，否则会是 v7 最大的陷阱。

> 或者反过来：让 `substr` 也按字符。但那要求每个字符串函数都知道编码，代价是每次调用多一层解码。
>
> **建议按字节，并在函数名上体现**：`substr_bytes` / `strlen_chars`。宁可名字长，也不要让两套索引混用。

---

## 六、主线 D：等待用户输入

### 6.1 现状：完全没有输入能力

全项目搜索 `input(` / `stdin` / `getline` / `scanf` / `read_line` / `prompt`，**零命中**（唯一命中的是 `c_backend.py:109` 的 `#include <stdint.h>` 里的 `stdint`）。

也就是说：这个语言现在**只能单向输出**。写不了「输入一个数字，算它的平方」这种第一个教程就会有的例子。对新手入门来说，这个缺口比缺 `return` 还严重。

### 6.2 目标形态

沿用现有语句的字面风格（`print (<...>)`、`push ({k} = {v})`、`kill {x}`、`sleep (1)`）：

```cos
ask ({name})
```

带提示语的形式：

```cos
ask ({name}) (<{"请输入半径: "}>)
```

读进来的是**字符串**。要数字就自己转：

```cos
ask ({raw})
math_run ord({raw}) - 48 = ? -int32
```

> 不做「自动类型推断输入」。用户敲 `12` 到底是数字还是字符串，由代码决定，不由库猜。这一条和 `substr` 按字节是同一个原则：宁可让用户多写一行，也不要让两套语义混着来。

### 6.3 必须解决的语义问题

| 问题 | 决定 |
|---|---|
| EOF（管道读完 / Ctrl+Z） | 变量设为**空串**，状态置 `(error)`，不中止程序 |
| 空行 | 合法的空串，状态 `(ok)` |
| 编码 | 按 `#name` 声明的编码解码；未声明按 UTF-8 |
| 行长上限 | 4096 字节，超出截断并置 `(error)` |
| 末尾换行 | 剥掉 `\r\n` / `\n` / `\r`，不保留 |
| 提示语末尾 | 不自动补空格，要空格自己在字符串里写 |

### 6.4 一个会咬人的坑：非交互环境

**`ask` 在管道/重定向/CI 环境下会立刻读到 EOF。** 如果实现成「阻塞等输入」，现有的 223 个测试里但凡跑到 `ask` 就会挂住，整条测试链死掉。

所以必须：

- EOF 立刻返回空串 + `(error)`，**绝不阻塞**
- 编译版用 `fgets`，返回 `NULL` 就是 EOF
- 解释器用 `sys.stdin.readline()`，返回 `''` 就是 EOF
- 提供一个 `--no-input` 开关，把所有 `ask` 强制当 EOF 处理（给测试和自动化用）

### 6.5 双后端一致性

这是第一个**依赖外部状态**的语句。两个后端必须做到：

- 同样的输入 → 同样的输出（包括 EOF 时的状态）
- 提示语在**两边都要刷 stdout 并 flush**（否则重定向到文件时提示语不出现，用户看着光标发呆）

验收方式：用管道喂固定输入，比对两版 stdout 的 SHA256。现有测试里已经有这个模式（v6 的定点常数就是这么验的）。

---

## 七、主线 E：RISC-V 优化

### 7.1 现状：工具链根本没有

实测：

```
riscv64-unknown-elf-gcc  -> 无
riscv32-unknown-elf-gcc  -> 无
riscv64-linux-gnu-gcc    -> 无
qemu-riscv32 / qemu-riscv64 -> 无
gcc -dumpmachine         -> x86_64-w64-mingw32
```

所以第 0 步不是优化，是**先把工具链装上**（`xpack-riscv-none-elf-gcc` 或者 `apt install gcc-riscv64-linux-gnu` 走 WSL），外加 qemu 做验证。

### 7.2 先定目标，再谈优化

RISC-V 不是一个目标，是两类完全不同的场合：

| 目标 | 三元组 | ABI | 有 OS？ | 用途 |
|---|---|---|---|---|
| 裸机单片机 | `riscv32-unknown-elf` | `ilp32` | 无 | ESP32-C3、GD32VF103、CH32V |
| Linux 用户态 | `riscv64-linux-gnu` | `lp64d` | 有 | 香橙派、VisionFive |

**这两条路的优化点几乎不重叠**，必须先选。选了裸机，`#dll`、`popen`、`windows.h` 全部不可用；选了 Linux，那和现在的 x86 后端差别不大，优化空间有限。

### 7.3 裸机 rv32 的真实障碍（按严重程度）

**① 没有硬件浮点。** `rv32imac` 不含 F/D 扩展，`float64` 全部退化成 libgcc 的软浮点（`__adddf3` / `__muldf3` / `__divdf3`）。一次浮点加法从 1 条指令变成几十条，而且每个软浮点例程本身就占几百字节。

**这一条直接决定了「可修剪运行时」对 rv32 是刚需而不是优化**——`float64` 和 `complex` 必须能整个裁掉。

**② `complex.h`、`stdatomic.h`、`windows.h`、`popen` 都没有。** 现在生成的 C 里这四个都在（见 `main.c` 头部）。rv32 裸机后端必须换一套运行时，不是加宏能解决的。

**③ `#atom` 需要 A 扩展。** `rv32imac` 有 A（原子），`rv32imc` 没有。并发组件在无 A 的核上得用关中断模拟。

**④ 数据类型宽度。** x86_64 下 `long` 是 64 位，rv32 下是 32 位。生成代码里凡是依赖 `long` 宽度的都要显式换成 `int32_t` / `int64_t`，否则同一份源码在两个 target 上行为不同。

**⑤ 没有 `double` 字面量的库支持。** 软浮点下 `printf("%f")` 要靠 newlib 的 `_printf_float`，默认不链接，得手动开，而且体积大。

### 7.4 值得做的优化

| 优化 | 收益 | 代价 |
|---|---|---|
| `-march=rv32imac_zca` 压缩指令 | **代码密度 −25~30%** | 无（RV32C 是标准扩展） |
| `-Os` + `-ffunction-sections` + `--gc-sections` | 见第九节实测：**只有 5.4%** | 无 |
| 整数除法常量优化 | `rv32i` 的 `div` 只在 M 扩展里有；无 M 时软件除法极慢 | 需知道 target 有没有 M |
| 位运算映射 | `&`/`|`/`^`/`<<`/`>>` 一对一映射，无损耗 | 无 |
| bigint 用 32 位 limb | RV32 上 64 位乘法要 4 条 `mul` 组合 | 与 x86 的 64 位 limb 实现分叉 |

**注意：`-march` 是唯一「白拿」的优化。** 其余的都要改生成器，风险不成比例。

### 7.5 不用做的事

RISC-V **没有分支延迟槽**（和 MIPS 不同），也没有 x86 那种复杂的对齐要求。所以不需要为「填延迟槽」或「对齐跳转目标」写优化。这一点比移植到 MIPS 省事得多。

---

## 八、主线 F：TOML 插件元数据

### 8.1 先看清现状：项目里已经有三种格式

| 文件 | 格式 | 谁写的 |
|---|---|---|
| `cosp.toml` | TOML | 用户手写，`cosp.py` **自己解析**（有个 `_strip_toml_comment`） |
| `cosp.lock` | JSON | 工具自动生成（sha256 / source / version） |
| `cosp.toml` 的 `[dependencies]` | TOML | 手写，值是路径 |

再加一个「插件元数据」的第四种格式，就是**四种**了。

### 8.2 建议：复用一个，不要新开

**插件元数据直接写进 `cosp.toml`，开一个 `[plugin]` 段：**

```toml
[package]
name = "sort"
version = "1.2.0"
entry = "sort.cos"

[plugin]
type = "library"          # library | app | board
features = ["no-bigint"]   # 见第九节，只减不加
requires = []              # 依赖的其他插件名

[plugin.exposes]
bubble = "sort_bubble"
quick  = "sort_quick"
```

理由：**解析器已经写好了**。`cosp.py` 里那套手写 TOML 解析虽然只支持扁平段，但加一个 `[plugin]` 段不用改结构。

### 8.3 顺带可以修的一件事

Python 3.11+ 有标准库 `tomllib`（本机 3.13.2 已确认可用）。现在 `cosp.py` 里手写解析的原因大概是「不想装依赖」，但 `tomllib` **就是标准库，零依赖**。

换成 `tomllib` 能白拿：嵌套表、数组、多行字符串、正确的转义处理、以及**注释的正确剥离**（手写的 `_strip_toml_comment` 遇到字符串里的 `#` 就会切错）。

> 代价：`tomllib` **只读不写**。需要写回的话还是得自己拼字符串（`cosp add` / `remove` 就是写回）。所以是「读用 tomllib，写仍手拼」。

### 8.4 如果坚持 INI

那就 **`cosmic.pkg` 用 INI，`cosp.toml` 保持 TOML**，两个文件各管一件事。但必须接受三条限制：

- `configparser` 默认把键名转小写 → 必须设 `optionxform = str`
- 同段重复键默认抛 `DuplicateOptionError`，`strict=False` 会静默取最后一个（更难查）
- **INI 表达不了数组**，`features = ["a","b"]` 得自己按逗号切

约束：**`cosmic.pkg` 只允许平铺字段，一旦需要嵌套就另开文件，不要硬塞。**

> 注意：这和我「永远只爱 INI」的偏好有冲突——但 `cosp.toml` 已经是 TOML 了，再引入 INI 就是双格式。二选一，别两个都要。

---

## 九、主线 G：可修剪的运行时

### 9.1 实测：体积到底花在哪

拿 `cosp` 编出来的 hello world 量：

| 项 | 数值 |
|---|---|
| 生成的 `main.c` | **93396 B / 2363 行** |
| `static` 函数个数 | **152 个** |
| 这些函数体合计 | **72595 B** |
| `int main(` 之后的程序体 | **948 B** |

**hello world 带了 76 倍于自身的运行时。** 但 `main` 之后的 948 B 才是用户代码。

### 9.2 按功能域的裁剪收益（实测函数体字节数）

| 标签 | 函数数 | 字节 | 例子 |
|---|---|---|---|
| **`no-bigint`** | 37 | **21809** | `bigint_add` / `bigint_multiply` / `cosmic_fixed_constant` |
| **`no-array`** | 49 | **20375** | `cosmic_array` / `set_array_variable` / `vector_dot` |
| **`no-matrix`** | 14 | **10885** | `cosmic_matrix` / `solve_linear` |
| core（不可裁） | 41 | 14301 | `fail_at` / `set_result` / `set_variable` |
| `no-string` | 4 | 1920 | `unicode_ord` / `unicode_strlen` |
| `no-complex` | 1 | 772 | `complex_variable` |
| `no-bytes` | 2 | 693 | `cosmic_bytes_get/set` |
| `no-math-ext` | 1 | 577 | `cosmic_riemann_zeta` |
| `no-file` | 1 | 561 | `write_text_file_owned` |
| `no-print` | 1 | 494 | `print_value` |
| `no-wipe` | 1 | 208 | `secure_wipe` |

**结论：只有三个标签值得做——`no-bigint`、`no-array`、`no-matrix`，合计 53069 B（73%）。** 其余七个加起来才 5.2 KB，做了只是让配置项变多。

### 9.3 关键发现：链接期裁剪几乎没用

实测同一份 `main.c`：

| 编译选项 | exe 体积 |
|---|---|
| `-O2`（基线） | 88169 B |
| `-Os -ffunction-sections -fdata-sections -Wl,--gc-sections` | 83452 B |
| 再加 `-fno-asynchronous-unwind-tables` | 82276 B |

**`--gc-sections` 只省 4717 B（5.4%）。** 原因：这些 `static` 函数**全都从 `main` 可达**（`set_variable` 调 `copy_result` 调 `fail_at`……），链接器按「可达性」剪，剪不动。

**所以裁剪必须在生成代码阶段做**——`c_backend.py`（241.8 KB / 5732 行，占核心源码 56%）在写 C 之前就知道标签，直接不生成那段模板。链接期已经晚了。

### 9.4 硬地板：Windows 上不可能到 10 KB

实测一个只有 `int main(void){return 0;}` 的 C 文件：

```
tiny.exe = 54486 B
```

这是 **PE 格式 + CRT 启动代码**的地板。所以：

- **Windows 目标永远看不到 10 KB 的 exe**，无论怎么裁
- 10 KB 这个量级**只对裸机和 Linux 有意义**

### 9.5 硬约束：ATtiny85 装不下 10 KB

- ATtiny85：**8 KB flash**，扣掉 Digispark 引导（约 2 KB）剩 **约 6 KB**
- 10 KB 的 IL **放不进去**

目标是这颗芯片的话，得压到 ≤6 KB，而且要连解释器一起算。按现在 72 KB 运行时的规模，**光靠裁剪到不了那个量级**——得另写一套最小运行时（不能引 `complex.h` / `stdatomic.h` / `windows.h` / `popen`）。

### 9.6 标签设计的三条约束

**① 只减不加，默认全开。**

```cos
#%features = no-bigint, no-array
#%features
```

不写 = 全功能。这样**现有 223 个测试一条都不用改**，新标签只影响选择裁剪的人。反过来（默认精简、要手动开）会让测试矩阵立刻炸。

**② 标签之间不能有依赖。** `no-array` 不能导致 `matrix` 编不出来（matrix 内部用数组）。要么 matrix 自带数组实现，要么 `no-matrix` 强制隐含 `no-array`——但**只能单向隐含，不能成环**。

**③ 不支持的函数要在编译期就报错，不是链接期。**

```
第 42 行：pi_digits 在当前配置下不可用（已启用 no-bigint）
```

而不是让链接器吐 `undefined reference`。这条比裁剪本身更重要——新手看不懂链接错误。

### 9.7 和 IL 的关系：先想清楚要不要解释层

如果目标是单片机，**AOT 直接生成 AVR/RV32 原生代码，比 IL + 解释器既快又小**。

IL 的价值是「一份产物跑多种平台」，不是省体积。字节码分发开销通常是原生的 **5 到 20 倍**；16 MHz 的 AVR 上这个差距是听得出来的。

所以「不会像 C 的 VM 一样慢」的关键**不在裁剪多狠，在要不要引入解释层**。

---

## 十、主线 H：按需导入

### 10.1 这是 G 的另一面

- **G（可修剪运行时）**：裁掉**语言内置**的模块
- **H（按需导入）**：裁掉**库**里没用到的符号

两者共用同一套可达性分析，但作用对象不同。

### 10.2 三种粒度的选择

| 粒度 | 写法 | 优点 | 缺点 |
|---|---|---|---|
| 整文件 | `#%include = "lib/sort.cos"` | 实现最简 | 库一大就全带进来 |
| 按符号 | `use sort.bubble` | 精确 | 需要符号表 + 命名空间 |
| 文件 + 死代码消除 | include 全量，生成时只输出被引用的函数 | 对用户最透明 | 生成器要建调用图 |

**建议走第三条。** 用户仍然写 `#%include`，但 `c_backend.py` 在生成 C 之前做一次**从 `main` 出发的调用图可达性分析**，未被引用的库函数不写进 C。

### 10.3 为什么第三条最合这套语言

因为**这个语言的静态结构特别干净**：

- 没有间接调用（无函数指针、无闭包）
- 没有循环引用（引用图是树）
- 函数只有一种形态（表达式或块，见主线 A）
- 没有 `goto` 跳到别的函数（`jump` 只在本块内）

**这四条加起来，调用图是完备且可判定的。** 换成 C++ 或 Python 都要处理虚函数/`getattr`，这里不用。

### 10.4 必须处理的冲突：同名

现在没有命名空间。两个库都有 `sort()`，按需导入会**同时把两个都拉进来**，然后撞名。

三条路：

1. **约定前缀**（`sort_bubble`）—— 零成本，但靠自觉
2. **导入时强制重命名**（`use sort.bubble as bubble`）—— 需要解析器支持
3. **加命名空间** —— 动作用域模型，成本最高

**建议先走第 1 条**，把命名空间留到 v8。理由和第六节 `substr` 一样：宁可名字长，不要混用语义。

### 10.5 一个反直觉的结论

按需导入**不一定让编译变快**。现在的 223 个测试平均 174 秒，其中大部分时间在 GCC 编译。省掉没用到的库函数能减少生成的 C 体积，但**解析和调用图分析本身也要时间**。

对小项目（hello world 级），按需导入可能反而更慢。真正受益的是「引了三个库但只用一个函数」的场景。

所以这一项**应该排在最后**，而且要有数据支撑才做。

---

## 十一、主线 I：指针碰撞与所有权

### 11.1 语言层没有指针，所以碰撞全在运行时

这个语言**没有用户可见的指针**：没有 `&`、没有 `*` 解引用、没有指针运算、没有函数指针。所以 C 意义上的「指针碰撞」（两个指针指向同一块内存、写一个影响另一个）**在语言层不可能发生**。

这是资产不是负担——它让后端可以放心给生成的内联代码加 `restrict`，因为语言确实保证了不别名。

**但运行时自己的 C 代码里，别名到处都是。** 这才是要管的东西。

### 11.2 现在有三套并行的所有权，它们会互相踩

实测代码里的分配路径：

| 所有权 | 分配方式 | 释放方式 | 代码位置 |
|---|---|---|---|
| **栈上 Context** | `Context storage = {0};` | 函数返回时自动 | `c_backend.py:4571` |
| **堆上 Context** | `calloc(1, sizeof(Context))` | 从未 free（atom 块常驻） | `4827`, `5440` |
| **全局数组链表** | `malloc` + 挂到 `cosmic_array_allocations` | `cosmic_release_arrays()` 全清 | `158`, `578`, `587` |
| **上下文内的 buffer** | `malloc`（`copy_bytes` / `set_array_variable` / `inherit_context`） | `free_context()` 逐项 free | `173`, `188`, `240`, `256`, `337` |

**危险在于第三行和第四行是两套独立的账。** `cosmic_release_arrays()` 会把全局链表里的每一块都 free 掉，而 `free_context()` 又会 free 掉 `vars[i].array_data` 和 `arrays[i].data`。

如果同一块 buffer 既被 `cosmic_allocate_array()` 登记进全局链表、又被存进某个 `Variable` 或 `ArrayVariable`——**它就会被 free 两次**。

矩阵构造走的是 `cosmic_allocate_array()`（`633`: `matrix.data = cosmic_allocate_array(count, line);`），而 `set_array_variable()`（`337`）用的是裸 `malloc`。两条路径目前**看起来**是分开的，但**没有任何机制阻止将来有人把它们接起来**。

> 这一条我没有逐路径跑完验证，所以不写成「存在 double free」，只写成「结构性风险，需要审计」。审计的方法：给 `cosmic_allocate_array` 返回的每块打标记（在 `ArrayAllocation` 里加一个 `owner` 字段），运行时断言「同一块只能有一个 owner」。

### 11.3 深拷贝是每块一次的 O(VAR_COUNT + ARRAY_COUNT)

`inherit_context()`（`232`）的实现是：

```c
for (int i = 0; i < VAR_COUNT; ++i) {
    destination->vars[i] = source->vars[i];
    destination->vars[i].bytes = copy_bytes(source->vars[i].bytes);   // malloc
    if (source->vars[i].array_data && source->vars[i].array_length) {
        destination->vars[i].array_data = malloc(len * item_size);    // malloc
        memcpy(...);
    }
}
for (int i = 0; i < ARRAY_COUNT; ++i) { /* 同样再 malloc 一遍 */ }
```

**每个 `start` 块、每次 `father` 继承，都把全部变量和数组重新 malloc 一遍。** 即使只改了一个变量，其它 99 个也照拷。

这个设计在小程序上没问题，在「100 个变量 × 1000 个块」时就是 10 万次 malloc。而且它和 v6 文档里承诺的「arena + 逃逸提升」完全是两套东西——v6 的 arena 一行都没实现。

### 11.4 v7 的决定

- **不加用户可见指针。** 加了就毁掉「引用图是树」这个 GC 前提，也毁掉 `restrict` 优化。
- **给分配打 owner 标记**，把 11.2 的隐性风险变成运行时断言（debug 构建里启用，release 关掉）。
- **`inherit_context` 改成写时复制（COW）**：`bytes` 和 `array_data` 先共享指针 + 引用计数，只在真的写的时候才 malloc。这一步能把「块数 × 变量数」的 malloc 次数从平方级降到线性。
- **两套所有权合并成一套**：要么全局链表管一切，要么上下文管一切。不要两套并存。

---

## 十二、主线 J：声明编译平台

### 12.1 现状：完全没有这个概念

搜索 `#%platform` / `#%target` / `#%arch` / `--target` / `--platform`，**零命中**。

`compiler.py:70` 起的 gcc 参数是**硬编码**的：

```python
command = [gcc, "-std=c11", "-D_DEFAULT_SOURCE"]
if simd: command.extend(["-O3", "-ftree-vectorize"])
else:    command.append("-O2")
if fast_math: command.append("-ffast-math")
if not keep_symbols: command.append("-s")
command.extend(["-lm", "-o", str(output_path)])
```

没有 `--target=`、没有 `-m32`/`-m64`、没有 sysroot。**编译器假定自己跑在什么机器上，就编给什么机器。**

### 12.2 要声明什么

建议 `#%target` 用四段式（比三元组多一段「能力」）：

```cos
#%target = rv32imac-unknown-elf
#%target
```

必须能表达的六件事：

| 项 | 为什么必须知道 | 影响 |
|---|---|---|
| **字长** | `long` 在 x86_64 是 8 字节，rv32 是 4 字节 | 所有整数类型的宽度、溢出行为 |
| **浮点** | rv32imac **没有 F/D 扩展** | `float64` 退化成 libgcc 软浮点 |
| **原子** | `rv32imc` 没有 A 扩展 | `#atom` / `lock` 只能用关中断模拟 |
| **线程** | 裸机没有 `pthread` | `#atom` 整个组件不可用 |
| **动态库** | 裸机没有 `dlopen` / `LoadLibrary` | `#dll` 整个组件不可用 |
| **libc** | 裸机是 newlib，没有 `popen` / `system` | `#extern` 部分函数不可用 |

### 12.3 三条硬约束

**① 声明了就必须真照做，不静默降级。**

声明 `rv32` 就得真的按 32 位生成（`long` = 4 字节、指针 = 4 字节）。如果后端偷偷用 64 位算，用户会在「本地跑得好、烧进去就错」上浪费几天。这一点和 `#asm` 不继承、不静默降级是同一个原则。

**② 不可用的组件必须在**编译期**报错，不是链接期。**

```
第 12 行：#dll 在目标 rv32imac-unknown-elf 上不可用（没有动态链接器）
第 30 行：#atom 在目标 rv32imac-unknown-elf 上不可用（没有线程库）
```

而不是让链接器吐 `undefined reference to dlopen`。

**③ 不写 = 本机。** 这样现有 223 个测试一条都不用改。

### 12.4 顺带能解决的一个隐患

现在生成代码里凡是依赖 `long` 宽度的地方都没有显式类型。声明平台之后，后端可以**强制把所有跨平台敏感的位置换成 `int32_t` / `int64_t` / `size_t`**，而不是留给编译器决定。这一条比新增功能更值钱——它是「同一份源码在两个 target 上行为一致」的前提。

---

## 十三、主线 K：栈分配

### 13.1 现在栈上放了什么

实测 `c_backend.py:4571`：

```c
Context storage = {0};
Context *context = &storage;
```

一个 `Context` 就是 `VAR_COUNT × sizeof(Variable) + ARRAY_COUNT × sizeof(ArrayVariable)`。

两个结构体的实际字段（`c_backend.py:136-155`）：

```c
typedef struct {
    unsigned char *bytes;      //  8
    const char *encoding;      //  8
    void *array_data;          //  8
    size_t array_length;       //  8
    int array_value;           //  4
    int array_complex;         //  4
    size_t matrix_rows;        //  8
    size_t matrix_columns;     //  8
    int initialized;           //  4
    int killed;                //  4
} Variable;                     // x64: 64 B   rv32: 40 B

typedef struct {
    double *data;              //  8 / 4
    double complex *complex_data;  //  8 / 4
    size_t length;             //  8 / 4
    int initialized;           //  4 / 4
    int complex_mode;          //  4 / 4
    size_t matrix_rows;        //  8 / 4
    size_t matrix_columns;     //  8 / 4
} ArrayVariable;                // x64: 48 B   rv32: 28 B
```

### 13.2 算一下：这堆东西在单片机上是灾难

假设一个中等程序 `VAR_COUNT = 100`、`ARRAY_COUNT = 20`：

| 目标 | Context 占用 |
|---|---|
| x86_64 | 100×64 + 20×48 = **7360 B** |
| rv32 | 100×40 + 20×28 = **4560 B** |
| **ATtiny85（SRAM 只有 512 B）** | **4560 B → 溢出 9 倍** |

**而且这还只是 main 里那一层。** 每个 `start` 块如果 `inherit`，deep copy 出来的新 Context 至少又一份；`calloc` 出来的 atom 块 Context 再加一份。

ATtiny85 想跑，`VAR_COUNT + ARRAY_COUNT` 加起来得压到 **个位数**（512 B 里还要留给栈、堆、ISR）。这不是「优化」，是「换一套存储模型」。

### 13.3 好消息：这些数字全是编译期已知的

`VAR_COUNT` / `ARRAY_COUNT` 是后端在生成时算出来的（`c_backend.py:3985`: `variable_count = max(1, len(self.variable_indices))`），然后写成 `#define` 交给 C 编译器。

所以**栈占用是可以在编译期精确预算的**，不用运行时探测。这意味着：

- 可以在生成时就报「这个程序的 Context 需要 4560 B，目标只有 512 B」
- 可以据此**拒绝编译**，而不是烧进去才发现跑不起来
- 可以把 Context 从栈搬到静态区（`.bss`），省掉栈溢出的风险

### 13.4 v7 的决定

- **生成时输出一份内存预算表**（Context / 栈峰值 / 堆峰值），写进 `--verbose` 或单独的 `.memreport`。
- **超目标容量时编译期报错**，给具体的数：「需要 4560 B，rv32imac 目标可用 512 B」。
- **单层程序的 Context 从栈移到 `.bss`**（现在就在栈上，递归时会叠加风险）。
- 递归深度上限（主线 A 提过 1000）必须和这个预算联动——每层递归的栈帧里有什么，得能算出来。

---

## 十四、主线 L：堆内存

### 14.1 现在所有的堆分配（实测清单）

| 位置 | 分配 | 大小 | 释放 |
|---|---|---|---|
| `copy_bytes` | `malloc(size)` | 字符串长度 | `free_context` |
| `copy`（数组字节） | `malloc(size)` | 数组长度 | `free_context` |
| `set_array_variable` | `malloc(len * 8)` | 数组 | `free_context` |
| `set_complex_array_variable` | `malloc(len * 16)` | 复数数组 | `free_context` |
| `inherit_context` | `malloc` × (变量数 + 数组数) | 深拷贝 | `free_context` |
| `cosmic_allocate_array` | `malloc(data)` + `malloc(node)` | 矩阵工作区 | `cosmic_release_arrays` |
| atom 块 Context | `calloc(1, sizeof(Context))` | **常驻，从不 free** | — |
| `parent_copy` Context | `calloc(1, sizeof(Context))` | **常驻，从不 free** | — |

### 14.2 失败处理是「立刻死」

所有分配失败都是同一个模式：

```c
if (!data) fail_at(line, "out of memory");
```

`fail_at` 打印 `line N: out of memory` 然后退出。**没有优雅降级、没有重试、没有部分成功。**

对小工具这是合理的。但对裸机、对需要长时间运行的场合，这是「一个 malloc 失败就整机重启」。

### 14.3 裸机上根本没有 malloc

newlib 的 `malloc` 是可选的（`_sbrk` 要自己实现）。ATtiny85 上要么自己写一个 bump allocator，要么全部静态分配。

所以「堆内存」这一块在裸机目标上**不是优化问题，是存在性问题**。

### 14.4 一个数量级上的荒谬对比

`set_array_variable` 里有个上限检查：

```c
if (source.length > 1000000) fail_at(line, "arrays may contain at most one million items");
```

**一百万个 double = 8 MB。** 而 ATtiny85 的全部 SRAM 是 **512 B**。这个上限是 16000 倍。

不是说要改这个数——在 PC 上它很合理。但它说明**同一套常量不能服务所有目标**，必须跟着 `#%target` 走。

### 14.5 v7 的决定

- **加 `#%memory = heap | arena | static` 三档**，默认 `heap`（现状）。
  - `arena`：所有分配从一块编译期定长的池里 bump，块退出整块回收
  - `static`：所有变量和数组容量编译期确定，零 malloc
- **`static` 档下 `array_push` 必须编译期报错**（容量不可预知），除非配合 `#%maxlen` 声明。
- **分配失败的行为可配**：`abort`（现状）/ `return error`（把 OOM 变成 `up = (error)`，与现有状态机对齐）。
- **补一条「堆峰值」统计**，和主线 K 的栈峰值并列。

> 注意：这三档和 v6 文档里承诺的 arena 是同一件事。v6 写的是「每个 start 块一个 arena，块退出整块归还」，但代码一行没有。v7 做这件事，等于把 v6 的债接过来还——**如果 v6 后续要补 arena，这里必须合并，不能各做一套**。

---

## 十五、次要项（时间够才做）

按性价比排：

| 项 | 价值 | 风险 |
|---|---|---|
| `array_fill(n, v)` / `array_copy(a)` | 高（现在构造数组很笨） | 低 |
| `bytes_from_str(s)` / `str_from_bytes(b)` | 高（字节和字符串现在不能互通） | 低 |
| `#%include` 的简写形式 | 中 | 低 |
| 结构体 / 记录类型 | 高 | **高**，会动类型系统 |
| 错误恢复（`try` / `catch` 或类似） | 高 | **高**，会动整个状态机 |

**结构体和错误处理都建议推到 v8**，理由：现在的 `up = (ok)` / `up = (error)` 状态机是这个语言的地基，动它会牵连 GC、`father`、块状态三处。v7 已经要动作用域了，不宜同时动状态机。

---

## 十六、明确不做（历史提案）

- 不做 Unicode 大小写折叠（见 5.1）
- 不做模块命名空间（`lib::sort` 那种）——包含即平铺，简单优先
- 不做包管理器 / 远程依赖
- 不做运算符重载
- 不做闭包（会破坏「引用图是树」这一 GC 前提）
- 不做 `try` / `catch`（见第六节）

---

## 十七、实施顺序

```
1  把现有 father / 块作用域语义固化成测试（防回归，先做）
2  #pe 移除（v6 遗留，一小时的事，先清干净）
3  字符串库 C 主线：substr/find/split/join/trim/upper/lower/replace
     —— 风险最低，见效最快，先拿这个验证「同时改解释器和 C 后端」的流程
4  等待用户输入（主线 D）：ask 语句 + EOF 语义 + --no-input
     —— 独立于作用域，且是新手入门的刚需
5  #%include：解析器 → 解释器 → C 后端，三级一致
6  多语句函数体：作用域隔离 + return + 递归深度上限
7  TOML 插件元数据（主线 F）：复用 cosp.toml，加 [plugin] 段
8  可修剪运行时（主线 G）：先做 no-bigint / no-array / no-matrix 三个标签
9  按需导入（主线 H）：调用图可达性分析
10 栈/堆预算与平台声明（主线 J/K/L）：先做「编译期内存预算表」和「超容量报错」
     —— 这两项不改运行时行为，但能提前暴露问题，风险最低
11 指针碰撞审计（主线 I）：给分配打 owner 标记 + COW 改造 inherit_context
12 RISC-V 优化（主线 E）：先装工具链，再谈 -march=rv32imac_zca
13 收编 examples/ 的重复代码进 lib/
14 次要项按剩余时间
```

**第 1 步不能省。** 后面三步全都要动解析器，没有回归测试就等于蒙眼改。

**第 3 步排在 4、5 之前**是有意的：字符串库不动作用域、不动状态机，是练习「双后端同步改造」最安全的靶子。流程跑顺了再啃 include 和函数体。

---

## 十八、需要拍板

1. **主题**：就按「可组合性」这三条走，还是心里另有所指？
2. **函数体 vs include 的先后**：我建议字符串库 → include → 函数体（风险递增）。如果急着写带局部变量的函数，可以调换顺序。
3. **`substr` 按字节还是按字符**：我建议按字节并改名叫 `substr_bytes`，宁可名字长也不混用索引。
4. **v6 是先落地再开 v7，还是并行**：v6 代码已落地并合入 main（`e7035b6`），这条自动结案。
5. **`lib/` 目录**：现在建，还是等 include 做出来再建？
6. **`ask` 的语法**：`ask ({name})` 还是 `readline ({name})`？提示语用第二个括号还是内联？
7. **RISC-V 目标**：裸机 `rv32imac`（单片机，`#dll` 全废）还是 Linux `riscv64`（和 x86 差不多，优化空间小）？这决定要不要另写运行时。
8. **插件元数据用哪种**：`cosp.toml` 加 `[plugin]` 段（复用现有解析器），还是另开 `cosmic.pkg` 用 INI（守偏好）？**不能两个都要。**
9. **`cosp.py` 手写 TOML 是否换成标准库 `tomllib`**：读能换，写还得手拼。
10. **IL 到底要不要**：只做单片机 → 不该用 IL（AOT 原生又快又小）；要一份产物通吃 → IL 对，但要接受 5–20 倍解释开销。
11. **裁剪标签默认全开还是默认精简**：我建议默认全开（现有 223 个测试不用改）。
12. **内存模型三档的优先级**：`heap`（现状）/ `arena` / `static`，先做哪个？我建议先做 `static`——它是裸机唯一的可行路，而 `arena` 和 v6 承诺重叠，得先和 v6 的债对齐。
13. **`Context` 是否从栈移到 `.bss`**：移了省栈溢出风险，但多进程/多线程会互相踩（现在 atom 块是 `calloc` 的，不受影响）。
14. **`inherit_context` 改 COW 还是维持深拷贝**：COW 需要引用计数，会引入「谁减到零谁 free」的新所有权问题——和主线 I 的审计是一件事。
15. **OOM 行为**：`abort`（现状）还是转成 `up = (error)`？后者能对齐现有状态机，但会把「内存不足」和「算错了」混成一个状态。
16. **`#%target` 是否必须显式声明**：我建议不写 = 本机，写错就是错。但如果要做交叉编译，可能得要求必填。

---

## 附：这次盘点的实测依据

| 结论 | 依据 |
|---|---|
| 函数只有单表达式 | `parser.py:108` `_FUNCTION_RE` 要求 `= 表达式$` |
| 没有 return | 全项目 `_RETURN_RE` / `"return"` 命中 0 |
| 没有 include | `parser.py` + `runtime.py` 搜 `include\|#import` 命中 0 |
| 字符串只有四函数 | `runtime.py` 内置表只有 `strlen`/`char_at`/`chr`/`ord` |
| `#pe` 仍在 warn | `runtime.py:1759` |
| 测试 211 项 | `tests/test_interpreter.py` + `tests/test_compiler.py` 的 `def test_` 计数 |
| 圆周率已实现 | `runtime.py:598-599` |
| 完全没有输入能力 | 搜 `input\|stdin\|getline\|scanf\|read_line` 命中 0 |
| 无 RISC-V 工具链 | `riscv*-gcc` / `qemu-riscv*` 全无，`gcc -dumpmachine` = `x86_64-w64-mingw32` |
| hello world 运行时 | 152 个 static 函数 / 72595 B；`main` 之后仅 948 B |
| 裁剪收益集中 | `no-bigint` 21809 B + `no-array` 20375 B + `no-matrix` 10885 B = 73% |
| 链接期裁剪无效 | `--gc-sections` 只用 5.4%（88169 → 83452 B） |
| Windows 体积地板 | 空 `main` = **54486 B**（PE + CRT） |
| 核心源码体量 | `cosmic_interpreter` 430.9 KB / 10215 行，`c_backend.py` 占 241.8 KB / 5732 行 |
| 两套并行所有权 | 全局链表 `cosmic_array_allocations`（`158`/`578`）与 `free_context`（`215`）各 free 一遍 |
| 无平台声明 | 搜 `#%platform\|#%target\|--target` 命中 0；`compiler.py:70` 硬编码 gcc 参数 |
| Context 在栈上 | `c_backend.py:4571` `Context storage = {0};` |
| 堆 Context 从不 free | `4827` / `5440` 的 `calloc` 只分配不回收（atom 块常驻） |
| 深拷贝是 O(VAR+ARRAY) | `inherit_context`（`232`）每块重新 malloc 全部变量与数组 |
| `Variable` 大小 | x64 **64 B** / rv32 **40 B**（`136-146`） |
| `ArrayVariable` 大小 | x64 **48 B** / rv32 **28 B**（`147-155`） |
| 数组上限与 ATtiny85 差 16000 倍 | `set_array_variable` 上限 1000000 项 = 8 MB；ATtiny85 SRAM = 512 B |
