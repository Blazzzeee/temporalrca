from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from .config import load_config
from .procfs import ProcFS
from .runtime import run_agent


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="temporalrca-agent")
    result.add_argument("--config", default="/etc/temporalrca-agent/config.toml")
    result.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    subcommands = result.add_subparsers(dest="command")
    subcommands.add_parser("run", help="run the reporting daemon")
    subcommands.add_parser("check-config", help="validate configuration and exit")
    subcommands.add_parser("snapshot", help="print one raw /proc snapshot")
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, arguments.log_level),
                        format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}')
    try:
        config = load_config(arguments.config)
        command = arguments.command or "run"
        if command == "check-config":
            print("configuration is valid")
            return 0
        if command == "snapshot":
            print(json.dumps(ProcFS(config.proc_root).system(), indent=2))
            return 0
        asyncio.run(run_agent(config))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        logging.getLogger("temporalrca_agent").error("%s", error)
        return 2


if __name__ == "__main__":
    sys.exit(main())

