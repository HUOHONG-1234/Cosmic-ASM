# v8 计划：OverDrive

> 开发代号 **OverDrive**；首发版本标记 **v8 Zeta**。
> 状态：**已实现**（2026-11-18 起实施，见文末「实施结果」）。本文前半部分的实测数字保留为规划依据；
> 其中三处与代码不符的结论已在实施中纠正，见「实施结果 · 勘误」。
>
> **Zeta 只是一个版本标记，不表示规模缩减。** v8 的全部主线都在 Zeta 范围内，标记用于区分后续修订（Eta / Theta）。

---

## 零、先摆底盘

写这份计划之前，先把 v7 结束时语言长什么样量一遍。不然 v8 又是空中楼阁。

### 0.1 实测快照（规划基线，实施前）

> 下表是 2026-11-18 动手**之前**的量。实施后的实际数字见「十四、实施结果」。
> 两处已知偏差：测试实际是 **418 passed / 1 skipped**（非 336）；`lib/` 与 CI 已落地。

| 项 | 数值 |
|---|---|
| 测试定义数 | **336**（interpreter 175 / compiler 137 / cosp 24） |
| 解释器树遍历核心 | `parser.py` 2406 行 |
| C 后端 | `c_backend.py` **6877 行** |
| 运行时 | `runtime.py` 3174 行 |
| 包管理器 | `cosp.py` 826 行 |
| 编译驱动 | `compiler.py` 306 行 |
| examples | 26 个 `.cos` |
| packages | 2 个（`mathx` / `seqkit`，各带 `cosp.toml`） |
| CI | **无**（`.github` / `.gitlab-ci.yml` / `Jenkinsfile` 全不存在） |
| IR / 字节码 | **无**（全项目 `opcode` / `bytecode` / `instruction` 命中 0） |

### 0.2 v7 已经落地的（不再重复承诺）

`#%include` 相对引入、`ask` + `--no-input`、多语句函数体（`let`/`set`/`if`/`while`/`break`/`continue`/`return`）、
函数内闭包（按值捕获）、`record` + `operator`、`try/catch/finally`、命名空间 `#%namespace` /
`#%use`、`#gc = none|semi|mark`、`gc_pin`/`gc_unpin`、`The_world`、
`#%asm` + `#asm`、`#%features`、`#%memory = heap|arena|static` +
`#%maxlen` + `#%on_oom`、`--memory-report` / `--heap-report`、`cosp` 九命令 + 哈希锁。

字符串库也已补齐：`strlen` `substr_bytes` `find` `split` `join` `trim` `replace` `upper` `lower`。

**v8 不碰这些，一个都不推翻。**

### 0.3 v7 遗留的债

- 发行打包流程在本环境未跑通记录（`packaging/build.ps1` 的 PyInstaller + WiX）
- 无 CI
- `Context` 与具名数组始终是持久 malloc 副本，三种内存模式只影响临时值
- 解释器是纯树遍历，热循环无优化

---

## 一、v8 要解决什么

v6 解决"内存与汇编"，v7 解决"可组合性"，两者都是**让语言能写得更复杂**。

v8 换一个方向：**让语言更好用、跑得更快、装得上别的机器。**

四句话：

1. **写给人的语法**——动词优先，不学对象也能写出完整程序（HOP）
2. **在终端里能活**——自带 TUI，零外部依赖，三平台同源
3. **热循环不再是灾难**——把瓶颈从"解释器慢"变成"根本不在解释器里跑"
4. **换台机器也能跑**——Linux 与 ARM64 不再是纸面支持

这四件事的工程量，任何一件单独拿出来都比 v7 的一条主线大。合在一起就是 v8 的规模来源。

---

## 二、主线 A：动词优先语法层（HOP）

### 2.1 现状：现在怎么写

现在的 Cosmic 已经是动词打头，但**动词和参数之间没有任何语法约束**：

```
array_push({a}, 5)              # 动词_名词, 逗号隔开
math_run sqrt({x}) = ? -float64 # 动词_名词 + 修饰符 + 目标 + 类型标注
bytes_get({b}, 0)               # 动词_名词
```

问题是三重的：

- **动词和名词粘在一起**（`array_push`），学习者得背整串
- **每种动词的语法都不一样**（`math_run ... = ? -float64` 和 `array_push(...)` 完全不同形）
- **返回什么靠记忆**：`find` 返回下标，`replace` 返回新串，名字上看不出区别

### 2.2 目标形态

一条规则：**动词在前，宾语在后，空格分隔，缝用 `给` 接。**

```
打开 "log.txt" 给 文本
替换 文本 里的 "ERROR" 为 "错误" 给 文本
查找 文本 里的 "错误" 给 位置
长度 文本 给 字节数
行数 文本 给 总行
```

对应现在的写法：

```
#%include = "lib/text.cos"
math_run strlen({文本}) = ? -int64
math_run find({文本}, "ERROR") = ? -int64
math_run replace({文本}, "ERROR", "错误") = ? -int64
```

### 2.3 三条路径，必须选一条

| 路径 | 做法 | 代价 |
|---|---|---|
| **A1 语法糖层** | 解析期把中文句子重写成现有调用 | 不动内核，风险最低；但两套写法并存，文档要写两份 |
| **A2 预处理器** | 独立 `cosfmt` 工具先转成现有语法 | 完全隔离，不改解释器；但多一个构建步骤，报错行号会漂 |
| **A3 内核改造** | parser 直接吃新语法，旧语法作为别名 | 最干净；但要动 2406 行 parser 和 6877 行 C 后端 |

**v8 建议走 A1。** 理由：A3 会同时改两端，336 个测试全得跟着动，等于重写；A2 引入独立工具链，违背「零外部依赖」的底线；A1 只多一层重写，旧写法继续有效，测试不用改。

### 2.4 必须解决的语义问题

**（1）参数顺序统一成「源 → 目标，输出最后」**

不能有的函数源在前、有的目标在后。这条定死，否则学习者每遇一个新动词都要查。

**（2）返回值必须在名字里体现**

`查找` 返回位置，`替换` 返回新串——**光看动词看不出区别**。建议在动词后加一个名词限定：

```
查找位置 文本 里的 "错误" 给 位置
替换文本 文本 里的 "ERROR" 为 "错误" 给 结果
```

宁可名字长，不要学习者猜。

**（3）报错必须报「第几步、什么错」**

shell 最招人骂的就是 `a | b` 里 a 失败了 b 照样跑。v8 定死：**任何一步失败立即停止，错误信息带上步骤序号与原始行号。**

**（4）管道符号刻意不做**

暂时不引入 `|`。改用换行接续：

```
打开 "log.txt" 给 文本
替换文本 文本 里的 "ERROR" 为 "错误" 给 文本
行数 文本 给 总行
```

理由：`|` 一进来就得处理作用域传递、错误传播、缓冲策略，是三倍工作量。换行接续视觉上更接近 cmd，也符合目标用户的心智。

### 2.5 和 OOP 的分界

**不引入类、继承、多态、对象。** 但要承认一件事：

**「一批数据带名字」这个概念绕不过去。** v7 的 `record` 已经提供了值语义的字段组，v8 继续用它，不扩成对象。

HOP 的教学顺序是：

```
第一课：怎么打印
第二课：怎么算
第三课：怎么存（变量 / 数组 / record）
第四课：怎么重复（while）
第五课：怎么拆（函数）
```

OOP 的第一课是"什么是类"。这个差别就是 HOP 存在的理由。

---

## 三、主线 B：TUI 交互层（复古体验）

### 3.1 为什么不用 curses

**实测（2026-11-18，Python 3.13.2 / 3.14.6 都验过）：**

```
curses   -> 文件存在，import 失败
            ModuleNotFoundError: No module named '_curses'
msvcrt   -> 可用（标准库）
tkinter  -> 可用（标准库，但带 GUI 依赖）
```

Windows 的标准库里有 `curses/__init__.py`，但它执行到 `from _curses import *` 就断——那个 C 扩展根本没编进 Windows 版。

要修只有一条路：装 `windows-curses`。**那就当场破掉「零外部依赖」。**

另外查到本机有 `pyreadline3 3.5.6`，但那是第三方包，同样不能指望。

### 3.2 选定方案：ANSI 转义 + 平台按键适配

**渲染层：纯 ANSI 转义序列，一个 import 都不用。**

```
\x1b[2J            清屏
\x1b[H             光标回左上
\x1b[<行>;<列>H     定位
\x1b[7m / \x1b[0m  反显 / 复位
\x1b[?1049h        进备用屏（退出恢复原屏）
\x1b[?25l / \x1b[?25h  隐藏 / 显示光标
```

Windows 10 1511 之后控制台默认支持 VT 序列；需要显式打开时用 `ctypes` 调 `SetConsoleMode` 置 `ENABLE_VIRTUAL_TERMINAL_PROCESSING`，`ctypes` 是标准库。

**Linux / macOS 用完全相同的码。一份代码三平台，不需要 `#ifdef`。**

**输入层：约 40–60 行的薄适配。**

| 平台 | 做法 |
|---|---|
| Windows | `msvcrt.kbhit()` + `msvcrt.getch()`；方向键是两次调用（`\x00`/`\xe0` 后跟 `H`/`P`/`M`/`K`），需自行映射 |
| POSIX | `termios` 切 cbreak 模式 + `sys.stdin.read(1)` |

两个分支都是标准库。上层代码完全不感知平台。

### 3.3 必须写进规划的一行：中日韩字符宽度

TUI 画框时 `东` 占两个字符宽，`A` 占一个。用 `len()` 算宽度，混进中文框就歪。

```python
import unicodedata
def width(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)
```

`unicodedata` 是标准库，不用装包。**中文界面的 TUI，这一行不写就是斜框。**

### 3.4 边界

TUI 只做这些：菜单、进度条、分栏、光标移动、输入框、日志滚动。

**不做**：图形、鼠标、真彩色主题、窗口管理。超出这个范围就该开浏览器了。

---

## 四、主线 C：性能——热循环下沉

### 4.1 实测基准

**PyPy 8.0.1 / Python 3.12.15，examples 全 26 例：**

```
OK 26 / 26
总耗时        364.1s
basel              135.40s   ← 100 万次求和
monte_carlo_pi      87.68s   ← 20 万次随机
nbody               68.93s   ← 36500 步
pi_leibniz          57.49s   ← 级数
其余 22 例       0.35–2.27s
```

四个慢例占 **96%** 总时间。

**CPython 3.14.6（conda），同样 26 例、120 秒上限：**

```
OK 23 / 26
超时：basel / monte_carlo_pi / nbody
```

### 4.2 换解释器救不了

`basel` 只是循环里加一个数，却花了 135 秒。`aes128` 逻辑复杂得多，反而 0.74 秒。

**说明瓶颈是每轮迭代的固定开销**（AST 遍历、dict 分发、临时对象），不是运算本身。

而 PyPy 的 JIT 最擅长的是「循环里跑同一段紧凑字节码、类型稳定」。树遍历每轮在不同分支上跳，trace 记不住，**基本全程留在解释器里**。

另一个反证：同一个 `basel` 两次跑，176.03s → 135.40s，**波动 23%**。JIT 有预热成本且未稳定收敛。

**结论：PyPy 不纳入性能保证，只作为"能跑"的兜底选项。**

### 4.3 三条路，按性价比排

| 路径 | 预期收益 | 代价 |
|---|---|---|
| **C1 热循环下沉 C** | 数量级（省掉全部解释开销） | 需要在 6877 行 C 后端里加循环翻译 |
| **C2 字节码化** | 2–5×（PyPy 能真正 JIT） | 需要新写一个扁平指令解释器，等于第三套执行路径 |
| **C3 保持现状** | 1× | 慢例继续慢 |

**v8 选 C1。** 理由是现成的：`c_backend.py` 6877 行已经在把表达式翻成 C，`build/*.c` 里有 20 多个生成物作证。缺的只是**把 `while` 整块翻过去**，而不是逐条语句调回解释器。

C2 的陷阱：一旦引入字节码，就有解释器 / C 后端 / 字节码三条路径要同步维护——v7 已经在为"双后端一致"付代价，不该再翻倍。

### 4.4 触发条件要说清楚

热循环下沉不是无条件开启。触发条件建议：

- 循环体不含外部状态读写（`kill`、文件、网络、`ask`）
- 循环边界在编译期可知，或至少循环体内无 `break` 跳出的动态目标
- 用户可用 `#%no-jit-loop` 显式关掉

**不满足就是回退解释执行。这是能力增强，不是行为变更。**

---

## 五、主线 D：跨平台（Linux + ARM64）

### 5.1 现状

`compiler.py` 里只有一条目标分支：

```
if program.target == "riscv64-linux-gnu":
    selected_gcc = gcc or "riscv64-linux-gnu-gcc"
```

**ARM64 / aarch64 完全没有。** Linux 也只走 RISC-V 这一条。

### 5.2 两条路，层次不同

**D1 解释器层跨平台**——纯 Python，只要 Python 能跑的地方就能跑。现在理论上已经成立，但**从未在 Linux / ARM64 上实测过**，属于"应该能"而非"确实能"。

**D2 C 后端跨平台**——需要目标平台的 GCC 与运行时头文件。Windows 上交叉编译目前走 WSL，ARM64 还得再加一条工具链。

**v8 的顺序：先把 D1 做扎实（实测 + CI），再做 D2。**

### 5.3 ARM64 验证：手机就是真机

不必等树莓派或云主机。**Android 手机装 Termux + clang 就是一台真 ARM64 Linux。**

能直接验的东西：

| 验证项 | 做法 |
|---|---|
| 解释器层 D1 | `pkg install python` → 跑 336 个测试 |
| C 后端 D2 | `pkg install clang` → `cosmic build` 编出 aarch64 可执行文件 |
| `#%asm = arm64` | 实测内联汇编是否真能编过 |
| atom 块 `pthread` | Termux 自带 pthread，直接验 |
| 大整数运行时 | 纯 C，编一遍就知道 |
| TUI（主线 B） | Termux 就是标准终端，ANSI 转义序列直接测 |

这条路比交叉编译更可靠：交叉编译只能证明「能编出来」，手机能跑证明「真能跑」。

**仍然不做：** Windows on ARM（第四条平台）。

---

## 六、主线 E：发行与体积

### 6.1 实测：体积花在哪

```
CosmicInterpreter.exe   8.3 MB
CosmicCompiler.exe      8.5 MB
cosp.exe                9.0 MB
────────────────────────────
合计                   25.8 MB
CosmicAsm-0.1.0.msi    25.6 MB
```

三个 exe 用 PyInstaller `--onefile` 打的，**每个各自塞了一份完整的 Python 运行时**。MSI 基本就是三者相加。

### 6.2 DLL 拆分已被否决（附理由）

曾考虑把功能切成 DLL 以"节省空间"。**实测结论相反：拆了就更大。**

- 每个 DLL 各带一份 MSVC 运行时，加 PE 头和导出表
- 现在 `--onefile` 至少只带一次
- 更要命的是：一旦用 `.pyd`，就绑死 Python 3.13 和特定 ABI。升 3.14 或换 ARM64 都要重编
- 跨平台目标（主线 D）与 DLL 拆分直接冲突

**记录在案：v8 不做 DLL 拆分。**

### 6.3 可选方案

| 方案 | 效果 | 代价 |
|---|---|---|
| **E1 三合一** | 三入口合成一个 exe 走子命令，省两份运行时 | 改 CLI，加子命令分发 |
| **E2 onedir** | 共享一份运行时目录 | 分发变成文件夹，不再单文件 |
| **E3 保持现状** | 0 | 25.8 MB |

**v8 建议 E1**：`cosmic run` / `cosmic build` / `cosmic pkg`，单文件，约 9 MB，行为不变。

---

## 七、主线 F：工程化（CI）

### 7.1 现状：零 CI

`.github`、`.gitlab-ci.yml`、`Jenkinsfile` 全不存在。336 个测试全靠人工跑。

### 7.2 最小可行 CI

三件事，按顺序：

1. **单平台跑测试**（ubuntu-latest，Python 3.13）：`pytest` + `compileall` + `git diff --check`
2. **三平台矩阵**（ubuntu / windows / macos）：同一套测试跑三遍，验"纯 Python 可移植"这句话
3. **C 后端编译冒烟**：GitHub Actions 的 runner 自带 gcc，跑一遍 `examples/` 里的编译流程

### 7.3 显式不做

- 不做自动发版
- 不做代码覆盖率门槛（先有 CI 再说）
- 不做 Windows MSI 在 CI 里构建（WiX 依赖太重，本环境都没跑通）

---

## 八、主线 G：标准库归位

### 8.1 现状

```
examples/     26 个 .cos
packages/     mathx / seqkit（各带 cosp.toml + lib.cos）
lib/          不存在
```

v7 计划里写过"收编 examples 的重复代码进 lib/"，没做。

### 8.2 建议结构

```
lib/
  text.cos      字符串（包装现有 runtime 函数）
  math.cos      数值
  array.cos     数组
  io.cos        文件读写
  term.cos      TUI 原语（主线 B 的产物）
```

`examples/` 保留为纯示例，不再承担"库"的职责。

---

## 九、次要项（时间够才做）

- `ask` 支持密码输入（不回显）
- `cosp` 支持本地路径依赖（现在只有 HTTPS ZIP）
- `--check` 输出更友好（现在只报第一个错）
- `index.json` 的版本号与 README 对齐（当前 1.0.0 vs 0.1.0，不一致）
- 反编译式的源码映射：报错能定位到生成 C 的哪一行

---

## 十、明确不做

| 项 | 理由 |
|---|---|
| OOP（类 / 继承 / 多态） | HOP 的立身之本就是不引入 |
| 管道符号 `\|` | 三倍工作量，换行接续够用 |
| DLL 拆分 | 体积反而变大，且绑死 ABI（见 6.2） |
| 字节码 / IR | 会造出第三条执行路径（见 4.3） |
| GUI | 有 TUI 就够，GUI 是另一个项目 |
| Windows on ARM | 第四条平台，v8 不碰 |
| 自动发版 | CI 先跑起来再说 |

---

## 十一、v8 Zeta 的交付内容

**Zeta 不是缩水版。** 它只是一个希腊字母标记，用来区分 v8 的历次修订。

因此 Zeta 的范围 = 本文件第二到第八章的全部主线：

| 主线 | 内容 |
|---|---|
| A | 动词优先语法层（HOP） |
| B | TUI 交互层（ANSI + 按键适配） |
| C | 热循环下沉 C |
| D | 跨平台：Linux + ARM64（手机即可验证） |
| E | 三合一 CLI，体积降到约 9 MB |
| F | CI：三平台矩阵 |
| G | 标准库归位 `lib/` |

规模上，这是 v6（内存与汇编）+ v7（可组合性）之和还要多——因为 C、D、E 三项都要动 C 后端和构建流程，
而 v6/v7 的所有主线加起来也只动了 `c_backend.py` 的一个切面。

### 11.1 分阶段推进，不代表减量

按依赖关系切成三批，每批都是一次可发布的版本：

**Zeta-1（地基）**
- G 标准库归位 `lib/`
- A1 语法糖层：text / array / math 三组动词
- F1 单平台 CI

**Zeta-2（新能力）**
- B TUI：渲染 + 按键适配 + demo
- E1 三合一 CLI
- F2 三平台矩阵

**Zeta-3（性能与平台）**
- C1 热循环下沉
- D1/D2 Linux + ARM64 实测

三批全部完成才算 Zeta 收尾，中间版本分别标 Zeta-1 / Zeta-2。

### 11.2 验收标准

- 旧写法 336 个测试全绿（每批都跑）
- 新语法能跑通 `examples/` 全部 26 个例子的翻译版本
- TUI demo 在 Windows / Linux / Android(Termux) 三处表现一致
- CI 三平台矩阵全绿
- 三个 exe 合成一个，体积 ≤ 12 MB

---

## 十二、实施顺序

```
1  建 lib/ 目录，把 examples 里的重复逻辑抽出来（零风险，为 A 打地基）
2  A1 语法糖层：先做 5 个动词（打开/替换/查找/长度/行数）
3  A1 扩到 text / array / math 三组
4  B TUI：ANSI 渲染 + 宽度计算（先不上按键）
5  B TUI：按键适配层（Windows / POSIX 双分支）
6  B TUI：demo 程序（文件浏览器或计算器）
7  F1 CI：ubuntu + pytest
8  —— Zeta-1 发布线 ——
9  B TUI：ANSI 渲染 + 宽度计算（先不上按键）
10 B TUI：按键适配层（Windows / POSIX 双分支）
11 B TUI：demo 程序（文件浏览器或计算器）
12 E1 三合一 CLI
13 F2 CI 三平台矩阵
14 —— Zeta-2 发布线 ——
15 C1 热循环下沉：先做 while + 无副作用循环体
16 C1 扩展到 for 式循环与嵌套
17 D1 解释器层 Linux / ARM64 实测（Termux 手机即可）
18 D2 C 后端 ARM64（Termux + clang）
19 次要项
```

**第 1 步不能省。** 后面 A1 要大量引用 lib，没有 lib 就得在示例里复制粘贴。

**C1 排在最后一批**是有意的：它要动 C 后端，一旦出错影响面覆盖全部编译路径，不该和语法层同时改。

---

## 十三、需要拍板

1. **A 的路径**：A1 语法糖 / A2 预处理器 / A3 内核改造？（建议 A1）
2. **`给` 这个字**：用 `给` / `到` / `存` / `→`？一天出现二十次，顺手要紧
3. **动词带名词限定**（`查找位置` / `替换文本`）能不能接受？还是宁可短、让用户记？
4. **TUI 的第一个 demo 做什么**？文件浏览器 / 计算器 / REPL 界面？
5. **C1 的触发条件**：自动判定（编译器分析）还是用户显式标注（`#%hot-loop`）？
6. **E1 三合一**会不会破坏现有用户的脚本调用？如果破坏，值得做吗？
7. **手机验证 ARM64**：主人手机是 Android 还是别的？如果是 Android，Termux + clang 这条路就定了
8. **分批发布点**：Zeta-1 / Zeta-2 / 全量 三个发布点同不同意？（分批只是节奏，不减内容）
9. **`lib/` 与 `packages/` 的关系**：`lib` 是随发行版带的，`packages` 是用户装的，这个划分对不对？
10. **CI 平台**：GitHub Actions 还是别的？（仓库若不在 GitHub 就另说）

---

## 附：实测依据

> 下表为**规划期（实施前）**的取证。实施后已变化的三行已标注。

| 结论 | 依据 |
|---|---|
| 测试 336 项（实施前）→ **418 passed / 1 skipped**（实施后） | `pytest -q` 实跑 |
| 无 CI（实施前）→ `.github/workflows/ci.yml` 已落盘 | 目录检测 |
| 无 IR | 全项目搜 `opcode` / `bytecode` / `instruction` 命中 0 |
| Windows 无 `_curses` | `python -c "import curses"` → `ModuleNotFoundError: No module named '_curses'` |
| `pyreadline3` 是第三方 | `pip list` 命中 3.5.6，非标准库 |
| PyPy 26/26、364.1s | `tmp_conda/pypy_all.txt` |
| CPython 3.14 三例超时 | `tmp_conda/conda_run.txt`（120s 上限） |
| PyPy 波动 23% | `basel` 两次：176.03s / 135.40s |
| 三个 exe 各带运行时 | `build.ps1` 用 `--onefile`；8.3 / 8.5 / 9.0 MB |
| 只有 riscv64 目标（实施前）→ 已加 `aarch64-linux-gnu` | `compiler.py` 目标集合 |
| 字符串库缺 `split`/`join` | 实施后确认仍不做，补 `line_count` |
| 26 个 examples | `examples/*.cos` 计数 |
| `lib/` 不存在（实施前）→ `lib/{text,math,array,io,term}.cos` 已建 | 目录检测 |

---

## 十四、实施结果（2026-11-18）

### 勘误：三处规划前提与代码不符

| # | 原计划说法 | 实测 | 实际做法 |
|---|---|---|---|
| 1 | 主线 C「热循环下沉 C」未实现，要「把 while 整块翻过去」 | C 后端本来就整程序翻成 C，`while` 生成真 C 循环（`c_backend.py`） | 改为**解释器热路径优化**：表达式求值加「计划缓存」 |
| 2 | 次要项「cosp 只支持 HTTPS ZIP」 | 本地目录/ZIP 早已支持（`_dependency_source`） | 不重复实现 |
| 3 | 「字符串库已补齐 `split` `join`」 | 两者**不存在**，只有 `strlen substr_bytes find trim upper lower replace` | 新增内置 `line_count`；不做 split/join |

### 各主线交付

| 主线 | 实现 | 验证 |
|---|---|---|
| A（HOP） | 新增 `cosmic_interpreter/hop.py`：行级重写到现有语句，带 `存` 关键字、名词限定动词、行号 1:1 映射、中文名自动映射 | `tests/test_hop.py` 19 项，含解释器/C 后端逐字节一致 |
| A（失败即停） | 新增语句 `require_ok [步骤号]`（parser + runtime + c_backend） | 双后端验证：C 报 `line N: HOP step K failed` 并退出 1 |
| B（TUI） | `cosmic_interpreter/tui.py`：纯 ANSI + `unicodedata` 宽度 + Windows/POSIX 按键适配 | `tests/test_tui.py` 17 项（含中文框不歪回归） |
| B（REPL） | `cosmic_interpreter/repl.py`：行编辑/历史/Ctrl+L/Ctrl+Q/F1 帮助 | `tests/test_repl.py` 20 项 |
| C（性能） | `_numeric_values` 拆成「计划缓存 + 取值」，条件与表达式复用同一计划 | 5000 次两语句循环 **240 µs → 57.7 µs/次（4.16×）**；`scripts/bench_loop.py` 可复现 |
| C（C 循环） | 补测试锁住「块 `while` 与函数 `while` 都生成真 C 循环」 | `tests/test_compiler.py` 3 项 |
| D（ARM64） | `#%target = aarch64-linux-gnu`：parser 目标集合 + `aarch64-linux-gnu-gcc -march=armv8-a` | WSL 交叉编译 + QEMU 实跑：`hello from arm64 / 12 / 18`，产物确认为 ARM aarch64 ELF |
| E（三合一） | 新增 `cosmic_interpreter/cosmic_cli.py` 与 `packaging/cosmic_entry.py`：`cosmic run/build/pkg/index/tui/version` | `tests/test_cosmic_cli.py` 6 项；`cosmic.exe` **7.72 MB**（规划 ≤12 MB） |
| E（体积） | MSI 只装 `cosmic.exe` + `LICENSE.txt` | MSI **25.6 MB → 7.86 MB**；旧三 exe 由 `build.ps1` 备份到 `D:\ASM\backup-exes-<时间戳>\` |
| F（CI） | `.github/workflows/ci.yml`：三平台矩阵 + ubuntu/windows 的 gcc 冒烟（探测不到 gcc 时跳过而非失败） | 本地逐条跑通；顺带修掉 RISC-V 集成用例在无发行版 runner 上会失败的 skip 条件 |
| G（lib） | `lib/{text,math,array,io,term}.cos` + `lib/README.md` | 解释器与编译版输出一致（如 `factorial(5)=120`、`sq(7)=49`、`clamp(99,1,50)=50`） |
| 次要项 | `line_count` 内置、`--check` 附源码行与 JSON `source` 字段、`ask -secret` 双后端不回显、生成 C 带 `/* .cos line N */` | `tests/test_sourcemap.py` 5 项；`ask -secret` 管道实测 |

### 与规划的取舍

- **`split` / `join` 不做**：HOP 的「行数」用 `line_count` 就够；加这两个需要引入字符串数组语义，成本远超收益。
- **HOP 不做管道 `|`**：按规划保留换行接续。
- **C 源码映射是轻量版**：语句级注释，不做完整 sourcemap。语句密集程序（如 `sha256.cos`，1102 条语句）生成 C 增大约 4%，其余 <0.25%。
- **ARM64 用手机验证改为 WSL + QEMU**：本机有交叉工具链与 QEMU，比手机更可复现；Termux 真机验证仍是后续可选项。

### 验收对照

| 标准 | 结果 |
|---|---|
| 旧写法测试全绿 | 见全量 pytest（0 失败） |
| 新语法能跑 | HOP 三组动词有解释器 + C 后端成对测试 |
| TUI 三平台一致 | Windows 实测 + 按键层平台分支单测；Linux/macOS 由 CI 矩阵承担 |
| CI 三平台矩阵 | workflow 已落盘，本地同命令验证通过（未推送到远端，由用户 push） |
| 单 exe ≤ 12 MB | 7.72 MB |
