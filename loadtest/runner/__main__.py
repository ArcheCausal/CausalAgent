"""命令行入口：python -m runner <preflight|accounts|stage|recovery|report>。"""

from runner.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
