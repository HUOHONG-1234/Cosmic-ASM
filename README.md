# Cosmic ASM

> 我要说什么来着?

<img src="Cosmic_logo_for_Markdown-1.svg" alt="Cosmic ASM logo" width="320">

[![Tests](https://img.shields.io/badge/tests-223%20passed-brightgreen)]()
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)]()
[![GCC](https://img.shields.io/badge/backend-GCC%20%7C%20C11-orange)]()
[![Dependencies](https://img.shields.io/badge/dependencies-none-success)]()

Cosmic ASM（简称 COS）是一门自制的编程语言，带一个 Python 编写的**解释器**和一个把 `.cos` 翻译成 C 再用 GCC 编成本地程序的**编译器**。

它的设计目标不是先进，是为初学者提供 C 的环境用于学习
我不会使用Git,所以我会让Agent代为上传.

---

## 上手

```powershell
git clone <repo>
cd CosmicInterpreter

# 解释执行
python -m cosmic_interpreter.cli examples\demo.cos

# 编译成原生程序
python -m cosmic_interpreter.compiler examples\demo.cos -o demo.exe

# 创建项目脚手架
python -m cosmic_interpreter.cosp new my_project
```

不需要装第三方库。

### Windows MSI 安装

安装 [CosmicAsm.msi](./dist/CosmicAsm.msi) 后，`CosmicInterpreter`、`CosmicCompiler` 和 `cosp` 会加入系统 `PATH`，并设置系统变量 `COSMIC_HOME` 指向安装目录。新开终端后即可直接使用这些命令；卸载 MSI 会移除其注册的环境变量值。

MSI 中的解释器和工具不依赖系统 Python。原生编译仍需单独安装 GCC，并确保 `gcc` 可从 `PATH` 找到；当前 MSI 不捆绑 GCC。

---

## 编译原生程序

```powershell
python -m cosmic_interpreter.compiler 程序.cos -o out.exe
```

生成的 C 源码会保留（`out.c`），可以查看。

```powershell
# 指定 C 文件位置和 GCC 路径
python -m cosmic_interpreter.compiler 程序.cos -o build\out.exe --c-output build\out.c --gcc gcc
```

默认会加 `-s` 剥掉符号表，产物能小 70%（实测 57,331 → 16,896 字节）。需要调试时加上 `--keep-symbols` 保留符号：

```powershell
python -m cosmic_interpreter.compiler 程序.cos -o build\out.exe --keep-symbols
```

> `-s` 只是去掉函数名、变量名这类调试信息，让反编译结果更难读，**不是加密**，算法逻辑和字符串常量仍然保留。

**跨平台**：生成标准 C11，除了 `-lm` 没有别的依赖。

- **Linux**：WSL2 Ubuntu 22.04 实测 211 项测试全过，产物为原生 ELF。
- **ARM / Termux**：生成代码无指针宽度、字节序、对齐假设；Windows 专属分支都在 `#ifdef _WIN32` 内。

生成的 C 开头自带 `_GNU_SOURCE` / `_DEFAULT_SOURCE`，Android bionic 和 glibc 都能正确声明 POSIX 接口，编译时不需要额外传 `-D`：

```bash
clang minimal.c -lm -o minimal
```

> `#text = GBK` 这类代码页转换依赖 Windows API，非 Windows 平台会回退为 UTF-8。

---

## 测试

```powershell
python -m unittest discover -s tests -v
```

```
Ran 223 tests
OK (skipped=1)
```

解释器和编译器各有一套，**同一个程序两边输出必须一致**——这是项目里最硬的一条规矩。

---

## 已知限制

写在这里，免得浪费你的时间：

| 限制 | 说明 |
|---|---|
| 无符号 32 位运算 | `uint32` 才有回绕，其他模式时会溢出报错 |
| 密码学不安全 | SHA/AES 是正确性演示，非恒定时间；汇编块同样不承诺 |
| 字节串有限 | `bytes` 类型有长度上限，不适合超大二进制 |
| 无符号计算 | 没有表达式树，做不了符号求导/积分 |
| 无任意精度浮点 | bigint 只有整数 |
| 内存卫生不等于安全加固 | 只有 GCC 编译版的 `kill {name} -force` 会覆盖该变量当前持有的存储；解释器不保证清零，副本、临时值、寄存器、转储和侧信道均不在保证范围内 |
| 并发是模拟 | 串行执行，不提供并行加速 |
| `print` 不认数组 | 数组要用 `array_at` 逐个取 |
| 内联汇编是实验性 | 可能随时改变或移除；不匹配平台编译期直接报错 |
| 无 `#pe` 组件 | v1 规划过，从未实现，v6 移除 |

### 支持等级

| 等级 | 含义 | 例子 |
|---|---|---|
| **稳定** | 承诺跨版本兼容 | 基本语法、`math_run`、数组、矩阵、位运算、并发组件、DLL 组件、`#gc = semi` |
| **实验性** | 可能在任意版本改变或移除 | `#asm` 内联汇编、`#gc = mark`、`gc_pin` 嵌套语义 |
| **不承诺** | 明确不提供保证 | 密码学安全与恒定时间、汇编块的优化器行为、并发并行性 |

详见 [v6 设计](./docs/v6-gc-asm.md)。

### 高精度定点常数

bigint 模式提供最多 1000 位定点常数；`*_digits(n)` 返回小数点后 `n` 位的整数，`*_str(n)` 返回带小数点的文本：

```cos
math_run pi_digits(40) = ? -bigint
math_run pi_str(40) = ? -bigint
math_run e_str(20) = ? -bigint
math_run sqrt2_str(20) = ? -bigint
```

支持 `pi_digits` / `pi_str`、`e_digits` / `e_str` 和 `sqrt2_digits` / `sqrt2_str`。精度参数必须是 0..1000 的整数。

### 模板与包管理器

`cosp`（Cosmic Package Manager）支持内置/本地模板脚手架、本地目录或 ZIP 依赖、`cosp.lock` 校验，以及项目 build/run/test。语言也支持按数值实参求值的 `generic function` 表达式函数。当前不支持联网注册表、自动执行依赖脚本或 C++ 式用户类型模板。

```powershell
cosp new my_project
cd my_project
cosp run
```

详见 [cosp 使用说明](./docs/cosp.md)。

---

## 它适合做什么

**适合**
- 教人理解编译器和解释器到底在干什么
- 验证数值算法
- 学并发语义（`lock` / `n-bomb` / `proton` 模型很干净）
- 做行为艺术

**不适合**
- 任何要交付给别人的生产系统
- 任何需要字符串处理的实际任务
- 任何真正的加密场景

---

## 文档

- [完整语法参考](./README-syntax.md)
- [错误排查手册](./docs/TROUBLESHOOTING.md)
- [v4 设计：并发与 DLL](./docs/v4-concurrency.md)
- [v5 设计：位运算、字节、矩阵](./docs/v5-numerics.md)
- [v6 设计：内存回收、内联汇编与支持等级](./docs/v6-gc-asm.md)
- [cosp：脚手架、包管理和泛型函数](./docs/cosp.md)

---

## License

MIT
