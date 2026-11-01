# Cosmic ASM

> 我要说什么来着?

[![Tests](https://img.shields.io/badge/tests-211%20passed-brightgreen)]()
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)]()
[![GCC](https://img.shields.io/badge/backend-GCC%20%7C%20C11-orange)]()
[![Dependencies](https://img.shields.io/badge/dependencies-none-success)]()

Cosmic ASM（简称 COS）是一门自制的编程语言，带一个 Python 编写的**解释器**和一个把 `.cos` 翻译成 C 再用 GCC 编成本地程序的**编译器**。

它的设计目标不是先进，是为初学者提供 C 的环境用于学习

---

## 上手

```powershell
git clone <repo>
cd CosmicInterpreter

# 解释执行
python -m cosmic_interpreter.cli examples\demo.cos

# 编译成原生程序
python -m cosmic_interpreter.compiler examples\demo.cos -o demo.exe
```

不需要装第三方库。

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
Ran 211 tests in 128.4s
OK (skipped=1)
```

解释器和编译器各有一套，**同一个程序两边输出必须一致**——这是项目里最硬的一条规矩。

---

## 已知限制

写在这里，免得浪费你的时间：

| 限制 | 说明 |
|---|---|
| 无符号 32 位运算 | `uint32` 才有回绕，其他模式时会溢出报错 |
| 密码学不安全 | SHA/AES 是正确性演示，非恒定时间 |
| 字节串有限 | `bytes` 类型有长度上限，不适合超大二进制 |
| 无符号计算 | 没有表达式树，做不了符号求导/积分 |
| 无任意精度浮点 | bigint 只有整数 |
| 并发是模拟 | 串行执行，不提供并行加速 |
| `print` 不认数组 | 数组要用 `array_at` 逐个取 |

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

---

## License

MIT
