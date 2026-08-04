from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")
WHITESPACE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    if text is None:
        return ""
    value = unicodedata.normalize("NFKC", str(text))
    value = CONTROL_CHARS.sub("", value)
    value = WHITESPACE.sub(" ", value).strip()
    return value


def _content_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    content = normalize_text(row.get("content", ""))
    mentions = row.get("mentions") or []
    hashtags = row.get("hashtags") or []
    if isinstance(mentions, str):
        mentions = [mentions]
    if isinstance(hashtags, str):
        hashtags = [hashtags]

    cleaned = {
        "tweet_id": str(row.get("tweet_id", "")),
        "username": normalize_text(row.get("username", "")).lstrip("@"),
        "timestamp": row.get("timestamp"),
        "content": content,
        "content_norm": content.casefold(),
        "replies": int(row.get("replies") or 0),
        "retweets": int(row.get("retweets") or 0),
        "likes": int(row.get("likes") or 0),
        "views": int(row.get("views") or 0),
        "mentions": [normalize_text(m).lstrip("@") for m in mentions if m],
        "hashtags": [normalize_text(h).lstrip("#").casefold() for h in hashtags if h],
        "query_tag": normalize_text(row.get("query_tag", "")).casefold(),
        "permalink": row.get("permalink"),
        "scraped_at": row.get("scraped_at"),
        "content_hash": _content_hash(content.casefold()),
    }
    cleaned["engagement"] = cleaned["replies"] + cleaned["retweets"] + cleaned["likes"]
    return cleaned


def clean_and_dedupe(rows: list[dict[str, Any]], max_workers: int = 4) -> pd.DataFrame:
    cleaned: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_clean_row, row) for row in rows]
        for future in as_completed(futures):
            cleaned.append(future.result())

    frame = pd.DataFrame(cleaned)
    if frame.empty:
        return frame

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["tweet_id", "timestamp"])
    frame = frame[frame["tweet_id"].astype(bool)]
    frame = frame.sort_values("timestamp").drop_duplicates(subset=["tweet_id"], keep="last")
    frame = frame.drop_duplicates(subset=["content_hash"], keep="last")
    frame = frame.reset_index(drop=True)
    logger.info("Cleaned rows=%s after dedupe", len(frame))
    return frame


def write_parquet(frame: pd.DataFrame, processed_dir: Path) -> Path:
    processed_dir.mkdir(parents=True, exist_ok=True)
    if frame.empty:
        out = processed_dir / "tweets_empty.parquet"
        frame.to_parquet(out, index=False)
        return out

    frame = frame.copy()
    frame["date"] = frame["timestamp"].dt.strftime("%Y-%m-%d")
    out = processed_dir / "tweets.parquet"
    frame.drop(columns=["date"]).to_parquet(out, index=False, engine="pyarrow", compression="zstd")

    # Partitioned layout for scalable reads
    for (date_value, tag), group in frame.groupby(["date", "query_tag"], dropna=False):
        part_dir = processed_dir / f"date={date_value}" / f"hashtag={tag or 'unknown'}"
        part_dir.mkdir(parents=True, exist_ok=True)
        group.drop(columns=["date"]).to_parquet(
            part_dir / "part-0.parquet",
            index=False,
            engine="pyarrow",
            compression="zstd",
        )

    logger.info("Wrote parquet dataset to %s", out)
    return out


def process_all_raw(config: dict[str, Any]) -> Path:
    root = Path(config["root"])
    raw_dir = root / config["paths"]["raw_dir"]
    rows: list[dict[str, Any]] = []
    for path in sorted(raw_dir.glob("tweets_*.jsonl")):
        rows.extend(_load_jsonl(path))
    logger.info("Loaded %s raw rows from %s files", len(rows), len(list(raw_dir.glob("tweets_*.jsonl"))))

    frame = clean_and_dedupe(rows)
    target = int(config["target_tweet_count"])
    lookback = int(config["lookback_hours"])
    if not frame.empty:
        now = pd.Timestamp.now(tz="UTC")
        selected = None
        for hours in (lookback, 36, 48, 72):
            candidate = frame[frame["timestamp"] >= now - pd.Timedelta(hours=hours)].reset_index(drop=True)
            if len(candidate) >= target:
                selected = candidate
                logger.info("Selected %s tweets within last %sh (target=%s)", len(selected), hours, target)
                break
        if selected is None:
            selected = frame.sort_values("timestamp").tail(min(len(frame), max(target, len(frame)))).reset_index(drop=True)
            logger.warning(
                "Insufficient tweets in short lookbacks; using newest %s cleaned tweets",
                len(selected),
            )
        frame = selected

    processed_dir = root / config["paths"]["processed_dir"]
    sample_dir = root / config["paths"]["sample_dir"]
    sample_dir.mkdir(parents=True, exist_ok=True)
    out = write_parquet(frame, processed_dir)
    sample = frame.head(100).copy()
    sample.to_parquet(sample_dir / "tweets_sample.parquet", index=False, engine="pyarrow", compression="zstd")
    sample.to_csv(sample_dir / "tweets_sample.csv", index=False)
    return out
