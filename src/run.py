from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analyze import analyze
from src.collect import collect_tweets
from src.config_loader import load_config, setup_logging
from src.process import process_all_raw


logger = logging.getLogger(__name__)


def run_collect(config: dict) -> Path:
    return collect_tweets(config)


def run_process(config: dict, raw_path: Path | None = None) -> Path:
    # raw_path kept for CLI compatibility; processing merges all raw JSONL files.
    _ = raw_path
    return process_all_raw(config)


def run_analyze(config: dict, parquet_path: Path | None = None) -> dict:
    if parquet_path is None:
        parquet_path = Path(config["root"]) / config["paths"]["processed_dir"] / "tweets.parquet"
    return analyze(parquet_path, config)


def run_all(config: dict) -> None:
    run_collect(config)
    parquet_path = run_process(config)
    run_analyze(config, parquet_path)
    logger.info("Pipeline finished successfully")


def main() -> None:
    parser = argparse.ArgumentParser(description="Indian market tweet intelligence pipeline")
    parser.add_argument(
        "command",
        choices=["collect", "process", "analyze", "run-all"],
        help="Pipeline stage to execute",
    )
    parser.add_argument("--raw", type=Path, default=None, help="Raw JSONL path for process")
    parser.add_argument("--parquet", type=Path, default=None, help="Parquet path for analyze")
    args = parser.parse_args()

    config = load_config()
    setup_logging(config)

    if args.command == "collect":
        run_collect(config)
    elif args.command == "process":
        run_process(config, args.raw)
    elif args.command == "analyze":
        run_analyze(config, args.parquet)
    else:
        run_all(config)


if __name__ == "__main__":
    main()
