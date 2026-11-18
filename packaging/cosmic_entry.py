"""PyInstaller 入口：统一的可执行文件 cosmic.exe。"""

from cosmic_interpreter.cosmic_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
