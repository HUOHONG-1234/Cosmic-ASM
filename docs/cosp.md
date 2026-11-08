# cosp：Cosmic Package Manager

`cosp` 是 Cosmic Package Manager。源码环境可通过 `python -m cosmic_interpreter.cosp` 使用（要求当前 Python 环境能导入项目）；Windows 安装包提供 `cosp.exe`。

## 创建项目

```powershell
cosp new hello
cd hello
cosp run
cosp build
```

内置脚手架有 `app`（默认）和 `library`。也可以在已有空项目目录中运行 `cosp init`，或指定名称 `cosp init --name hello`。脚手架只写入文件，不执行模板脚本。

自定义模板目录使用如下结构；`template.json` 只列出要复制的文件，文件内容中的 `${project_name}` 会替换为新项目名：

```text
my-template/
  template.json
  files/
    cosp.toml
    main.cos
```

```json
{"files": ["cosp.toml", "main.cos"]}
```

通过 `cosp new hello --template C:\templates\my-template` 使用本地模板。模板文件路径必须相对且不能越出 `files/`。

## 清单和本地依赖

`cosp.toml` 当前支持 `[package]`（`name`、`version`、`entry`）和 `[dependencies]`。依赖值是项目清单相对的本地目录或 ZIP 文件路径：

```toml
[package]
name = "hello"
version = "0.1.0"
entry = "main.cos"

[dependencies]
math_helpers = "../math_helpers"
```

```powershell
cosp add math_helpers ..\math_helpers
cosp install
cosp remove math_helpers
```

包被复制到 `.cosp/packages/`，`cosp.lock` 记录来源、版本和 SHA-256 内容摘要。`cosp install` 按锁文件验证包内容；本地依赖内容变化时会明确失败，可用 `cosp add` 更新该依赖并刷新锁定。提交 `cosp.toml` 与 `cosp.lock`；`.cosp/` 是可重建的安装目录。ZIP 安装拒绝绝对路径和 `..` 路径成员。安装不执行包脚本。

本版只支持本地目录和 ZIP；不会联网获取依赖或自动运行依赖构建脚本。语言尚无模块导入/链接机制，因此安装的依赖可供项目工具读取，但不会自动加入 `.cos` 的编译单元。

## 其他命令

```powershell
cosp test
```

`build` 将清单中的 `entry` 交给现有 GCC 编译器，`run` 交给解释器；`test` 执行当前项目的 Python `unittest` 发现。所有命令都从当前工作目录读取 `cosp.toml`。

## 泛型表达式函数

Cosmic ASM 当前没有用户类型系统。`generic function` 用于声明按调用实参数值类型工作的表达式函数；它不提供 C++ 类模板、用户类型模板或任意文本宏：

```cos
generic function square(value) = value * value
math_run square(5.5) = ?
```

解释器按实参实际数值求值；编译后端按程序声明的数学模式生成对应表示。现有 `function` 语法保持不变。
