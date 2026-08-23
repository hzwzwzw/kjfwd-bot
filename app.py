from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
from pathlib import Path

from kjfwd_bot.service import run


HERE = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="柯基服务队微信群答疑机器人")
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--env", type=Path, default=HERE / ".env")
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="故障回退：脱离 bowxt 控制面运行（不推荐用于常规部署）",
    )
    args = parser.parse_args(argv)
    managed = os.environ.get("BOWXT_MANAGED") == "1"
    if not managed and not args.standalone:
        parser.error(
            "kjfwd-bot 默认由 bowxt Agent 控制面启动；仅故障回退时使用 --standalone"
        )
    if managed and args.standalone:
        parser.error("bowxt 受管实例不能同时使用 --standalone")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    if args.standalone:
        logging.getLogger(__name__).warning(
            "正在使用不推荐的独立进程 fallback；配置、启停和日志不受 bowxt 控制面完整管理"
        )
    stopping = threading.Event()

    def request_stop(_signum, _frame) -> None:
        stopping.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    run(args.config, args.env, stop_event=stopping)


if __name__ == "__main__":
    main()
