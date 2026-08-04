from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer

logger = logging.getLogger(__name__)


def _lexicon_score(text: str, bullish: list[str], bearish: list[str]) -> float:
    tokens = set(text.casefold().split())
    bull = sum(1 for word in bullish if word in tokens)
    bear = sum(1 for word in bearish if word in tokens)
    total = bull + bear
    if total == 0:
        return 0.0
    return (bull - bear) / total


def _engagement_weight(engagement: pd.Series) -> pd.Series:
    return np.log1p(engagement.astype(float))


def build_feature_matrix(frame: pd.DataFrame, config: dict[str, Any]) -> tuple[pd.DataFrame, Any]:
    analysis = config["analysis"]
    vectorizer = TfidfVectorizer(
        max_features=analysis["tfidf_max_features"],
        ngram_range=(analysis["tfidf_ngram_min"], analysis["tfidf_ngram_max"]),
        min_df=analysis["min_df"],
        max_df=analysis["max_df"],
        dtype=np.float32,
    )
    tfidf = vectorizer.fit_transform(frame["content_norm"].fillna(""))
    tfidf_strength = np.asarray(tfidf.sum(axis=1)).ravel()

    bullish = config["lexicon"]["bullish"]
    bearish = config["lexicon"]["bearish"]
    lex = frame["content"].fillna("").map(lambda text: _lexicon_score(text, bullish, bearish))
    weight = _engagement_weight(frame["engagement"])

    scored = frame.copy()
    scored["lexicon_score"] = lex.astype(float)
    scored["tfidf_strength"] = tfidf_strength.astype(float)
    scored["engagement_weight"] = weight.astype(float)
    scored["tweet_signal"] = scored["lexicon_score"] * scored["engagement_weight"]
    # Blend sparse text magnitude into [-1, 1] bounded signal
    denom = scored["tfidf_strength"].replace(0, np.nan)
    text_component = scored["lexicon_score"] * (scored["tfidf_strength"] / denom.max())
    scored["tweet_signal"] = (0.7 * scored["tweet_signal"].fillna(0) + 0.3 * text_component.fillna(0)).clip(-1, 1)
    return scored, vectorizer


def aggregate_signals(scored: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    analysis = config["analysis"]
    rng = np.random.default_rng(analysis["random_seed"])
    bucketed = scored.set_index("timestamp").sort_index()
    groups = bucketed.groupby(pd.Grouper(freq=analysis["time_bucket"]))

    rows: list[dict[str, Any]] = []
    alpha = 1 - analysis["confidence_level"]

    for ts, group in groups:
        if group.empty:
            continue
        values = group["tweet_signal"].to_numpy(dtype=float)
        mean = float(values.mean())
        if len(values) == 1:
            low = high = mean
        else:
            boots = rng.choice(values, size=(analysis["bootstrap_samples"], len(values)), replace=True)
            boot_means = boots.mean(axis=1)
            low = float(np.quantile(boot_means, alpha / 2))
            high = float(np.quantile(boot_means, 1 - alpha / 2))

        rows.append(
            {
                "bucket_start": ts,
                "tweet_count": int(len(group)),
                "signal_mean": mean,
                "ci_low": low,
                "ci_high": high,
                "mean_engagement": float(group["engagement"].mean()),
                "mean_likes": float(group["likes"].mean()),
            }
        )

    result = pd.DataFrame(rows)
    logger.info("Aggregated %s time buckets", len(result))
    return result


def _sampled(frame: pd.DataFrame, n: int, seed: int) -> pd.DataFrame:
    if len(frame) <= n:
        return frame
    return frame.sample(n=n, random_state=seed)


def plot_signals(agg: pd.DataFrame, scored: pd.DataFrame, config: dict[str, Any], output_dir: Path) -> None:
    analysis = config["analysis"]
    output_dir.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(11, 5))
    if not agg.empty:
        ax.plot(agg["bucket_start"], agg["signal_mean"], color="#1f4e79", linewidth=2, label="Composite signal")
        ax.fill_between(
            agg["bucket_start"],
            agg["ci_low"],
            agg["ci_high"],
            color="#1f4e79",
            alpha=0.2,
            label=f"{int(analysis['confidence_level'] * 100)}% CI",
        )
    ax.axhline(0, color="#666666", linewidth=1)
    ax.set_title("Indian market tweet signal (hourly)")
    ax.set_xlabel("Time (UTC)")
    ax.set_ylabel("Signal")
    ax.legend(loc="best")
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output_dir / "signal_timeseries.png", dpi=120)
    plt.close(fig)

    sample = _sampled(scored, analysis["plot_sample_size"], analysis["random_seed"])
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.hist(sample["engagement"], bins=40, color="#2f6b3a", alpha=0.85)
    ax.set_title("Engagement distribution (sampled)")
    ax.set_xlabel("Engagement (replies + retweets + likes)")
    ax.set_ylabel("Count")
    fig.tight_layout()
    fig.savefig(output_dir / "engagement_hist.png", dpi=120)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 5))
    tag_means = scored.groupby("query_tag")["tweet_signal"].mean().sort_values()
    ax.barh(tag_means.index.astype(str), tag_means.values, color="#8b4513")
    ax.set_title("Mean signal by hashtag")
    ax.set_xlabel("Mean tweet signal")
    fig.tight_layout()
    fig.savefig(output_dir / "signal_by_hashtag.png", dpi=120)
    plt.close(fig)
    logger.info("Wrote plots to %s", output_dir)


def analyze(parquet_path: Path, config: dict[str, Any]) -> dict[str, Path]:
    root = Path(config["root"])
    output_dir = root / config["paths"]["output_dir"]
    sample_dir = root / config["paths"]["sample_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    sample_dir.mkdir(parents=True, exist_ok=True)

    frame = pd.read_parquet(parquet_path)
    if frame.empty:
        raise ValueError("Processed dataset is empty; cannot analyze")

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    scored, vectorizer = build_feature_matrix(frame, config)
    agg = aggregate_signals(scored, config)

    scored_path = output_dir / "tweet_signals.parquet"
    agg_path = output_dir / "aggregated_signals.csv"
    scored.to_parquet(scored_path, index=False, engine="pyarrow", compression="zstd")
    agg.to_csv(agg_path, index=False)
    agg.to_csv(sample_dir / "aggregated_signals.csv", index=False)

    vocab_path = output_dir / "tfidf_vocabulary.txt"
    with vocab_path.open("w", encoding="utf-8") as handle:
        for term in vectorizer.get_feature_names_out():
            handle.write(term + "\n")

    plot_signals(agg, scored, config, output_dir)

    summary = {
        "tweets": int(len(scored)),
        "mean_signal": float(scored["tweet_signal"].mean()),
        "bullish_share": float((scored["tweet_signal"] > 0).mean()),
        "bearish_share": float((scored["tweet_signal"] < 0).mean()),
        "hashtags": sorted(scored["query_tag"].dropna().unique().tolist()),
    }
    summary_path = output_dir / "summary.json"
    pd.Series(summary).to_json(summary_path, force_ascii=False, indent=2)
    logger.info("Analysis summary: %s", summary)

    return {
        "scored": scored_path,
        "aggregated": agg_path,
        "summary": summary_path,
    }
