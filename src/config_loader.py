from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path | None = None) -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    config_path = path or ROOT / "config.yaml"
    with config_path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)

    username = os.getenv("X_USERNAME", "").strip()
    password = os.getenv("X_PASSWORD", "").strip()
    auth_token = os.getenv("X_AUTH_TOKEN", "").strip()
    ct0 = os.getenv("X_CT0", "").strip()
    login_mode = config.get("scrape", {}).get("login_mode", "manual")
    if login_mode == "auto" and (not username or not password):
        raise ValueError("X_USERNAME and X_PASSWORD must be set in .env for auto login")
    if login_mode == "cookies" and (not auth_token or not ct0):
        raise ValueError("X_AUTH_TOKEN and X_CT0 must be set in .env for cookie login")

    config["credentials"] = {
        "username": username,
        "password": password,
        "auth_token": auth_token,
        "ct0": ct0,
    }
    config["root"] = str(ROOT)
    return config


def setup_logging(config: dict[str, Any]) -> None:
    log_cfg = config["logging"]
    log_dir = Path(config["root"]) / config["paths"]["log_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "pipeline.log"

    logging.basicConfig(
        level=getattr(logging, log_cfg["level"]),
        format=log_cfg["format"],
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
        force=True,
    )
