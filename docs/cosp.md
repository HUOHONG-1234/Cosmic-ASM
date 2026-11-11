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

## 清单和依赖

`cosp.toml` 使用 Python 3.11+ 标准库 `tomllib` 读取，支持 `[package]`（`name`、`version`、`entry`）、`[dependencies]` 和可选的 `[plugin]` 表。依赖值可以是项目清单相对的本地目录/ZIP 路径，也可以是 HTTPS ZIP URL：

```toml
[package]
name = "hello"
version = "0.1.0"
entry = "main.cos"

[dependencies]
math_helpers = "../math_helpers"
remote_lib = "https://packages.example/remote_lib-1.0.0.zip"
# 索引解析出来的依赖写成表：从归档里只取 subdir 子目录，并校验版本
mathx = { url = "https://github.com/HUOHONG-1234/Cosmic-ASM/archive/refs/heads/main.zip", subdir = "packages/mathx", version = "1.0.0", registry = "https://raw.githubusercontent.com/HUOHONG-1234/Cosmic-ASM/main/index.json" }
```

依赖表支持的键只有 `url`（必填，HTTPS）、`subdir`（归档内的相对 POSIX 路径）、`version`（语义版本，必须与包清单一致）、`registry`（来源索引，记录用）。`subdir` 只对远端归档有效。

插件元数据供工具和构建流程描述包的种类、功能标签、依赖包名与公开符号；它不会执行脚本，也不会改变现有的依赖安装行为：

```toml
[plugin]
type = "library"           # library | app | board
features = ["no-bigint"]   # 功能标签
requires = ["core"]        # 依赖包名称

[plugin.exposes]
quick = "sort_quick"
```

未提供 `[plugin]` 的既有清单继续有效。`cosp add` / `remove` 写回清单时会保留并序列化这些字段。

```powershell
cosp add math_helpers ..\math_helpers
cosp install
cosp remove math_helpers
```

包被复制到 `.cosp/packages/`，`cosp.lock` 记录来源、版本和 SHA-256 内容摘要。`cosp install` 校验锁文件版本及每个包的版本号和摘要格式，要求锁文件依赖名称与清单一致，并按锁文件验证包内容；依赖内容变化时会明确失败，可用 `cosp add` 或 `cosp remove` 更新清单并刷新锁定。提交 `cosp.toml` 与 `cosp.lock`；`.cosp/` 是可重建的安装目录。下载仅允许 HTTPS 和 HTTPS 重定向，拒绝 URL 内嵌凭据，单包压缩档最大 256 MiB。ZIP 安装拒绝绝对路径和 `..` 路径成员。安装不执行包脚本。

`cosp add` 接受本地路径或 HTTPS ZIP URL，例如 `cosp add remote_lib https://packages.example/remote_lib-1.0.0.zip`。当前不处理版本范围；索引只解析上列出的**精确版本**。

## 远端索引与 GitHub 来源

省略路径时，`cosp add <名称>` 会从索引 JSON 解析包，默认索引指向本项目仓库：

```text
https://raw.githubusercontent.com/HUOHONG-1234/Cosmic-ASM/main/index.json
```

```powershell
cosp index                 # 列出索引里的包
cosp add mathx             # 解析索引 -> 写清单 -> 下载归档 -> 只安装 packages/mathx
cosp install               # 之后完全按 cosp.lock 复现，不再访问索引
```

索引格式（仓库根目录的 `index.json`）：

```json
{
  "index_version": 1,
  "archive": "https://github.com/HUOHONG-1234/Cosmic-ASM/archive/refs/heads/main.zip",
  "packages": {
    "mathx": {
      "version": "1.0.0",
      "subdir": "packages/mathx",
      "description": "Numeric helpers: twice, square_sum, clamp."
    }
  }
}
```

- `index_version` 必须是 `1`；`archive` 是所有条目的默认归档（条目可用自己的 `url` 覆盖）。
- 条目键：`url`、`subdir`、`version`、`description`；`name` 必须符合包名规则。
- 索引只用于 `cosp add` 的解析。解析结果会写成清单里的依赖表（含 `url`、`subdir`、`version`），`cosp install` 只读清单与锁文件，因此离线/上游改动后仍可复现。
- 默认索引可用 `--index URL`（`cosp index` / `cosp add`）或环境变量 `COSMIC_COSP_INDEX` 覆盖，适合自建镜像或本地联调。
- 索引与归档都只允许 HTTPS，拒绝内嵌凭据；索引上限 4 MiB，归档上限 256 MiB。
- 本仓库的包放在 `packages/<名称>/`，各自带 `cosp.toml`；发布新包只需新增目录并把它加进 `index.json`。

语言尚无模块导入/链接机制，因此安装的依赖可供项目工具读取，`.cos` 通过 `#%include` 显式引入安装后的文件（例如 `#%include = ".cosp/packages/mathx/lib.cos"`），不会自动加入编译单元。

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
