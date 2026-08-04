# Technical Approach

## Problem

Build a market-intelligence pipeline for Indian equities discussion on X under a hard constraint: no official/paid Twitter APIs. Collection must still approach a practical volume (2000 tweets / 24h), survive rate limiting, and feed a quantitative signal stack suitable for research / algo inputs.

## Architecture

```
collect (Selenium) -> process (clean/dedupe/Parquet) -> analyze (TF-IDF + lexicon + CI + plots)
```

Stages are independently runnable via `python -m src.run <stage>`. Raw JSONL checkpoints make collection crash-safe. Processing merges every `data/raw/tweets_*.jsonl` file, so later scrape passes accumulate toward the target.

## Collection

- Browser automation via Selenium + Chrome (assignment requirement).
- Auth modes (config-driven):
  - `cookies`: inject `auth_token` + `ct0` from a normal browser session
  - `manual`: operator completes login in the Selenium window
  - `auto`: username/password from environment variables
- Search queries scoped with `since:YYYY-MM-DD` for the lookback window.
- Combined OR query plus per-hashtag Live/Latest tabs to improve coverage.
- In-memory ordered set keyed by `tweet_id` for O(1) dedup while scrolling.
- Randomized scroll delays and automation-marker reduction for anti-bot friction.
- Checkpoint writes every N tweets; session recovery restarts Chrome if the browser disconnects.
- Existing raw IDs are loaded on resume so top-up runs do not re-store duplicates.

## Processing and storage

- Unicode normalization with NFKC, control-character stripping, whitespace collapse.
- Indian-language content retained (no aggressive ASCII folding).
- Dedup primary key: `tweet_id`; secondary: SHA-1 of casefolded content.
- Concurrent row cleaning via `ThreadPoolExecutor`.
- 24-hour lookback filter applied after cleaning.
- Parquet with ZSTD compression, plus Hive-style partitions `date=*/hashtag=*` for selective reads at 10x scale.

## Signal construction

1. TF-IDF (1-2 grams, feature-capped, float32) for sparse textual magnitude.
2. Domain lexicon polarity for Indian market language (breakout, stoploss, etc.).
3. Engagement weight: `log1p(replies + retweets + likes)`.
4. Per-tweet signal blended and clipped to `[-1, 1]`.
5. Hourly aggregation with bootstrap confidence intervals.

This stays memory-efficient versus large embedding models while still producing actionable numeric features.

## Visualization

Plots consume aggregated buckets and sampled rows only. Raw tweet-level plotting is avoided so memory stays bounded as volume grows.

## Complexity

| Stage | Time | Space |
|---|---|---|
| Scroll parse | O(N) tweets | O(U) unique ids |
| Clean/dedupe | O(N) | O(N) |
| TF-IDF | O(N * L) average tokens | O(N * F) sparse |
| Bootstrap CI | O(B * K) per bucket | O(B) |

N = tweets, U = unique ids, L = tokens/tweet, F = TF-IDF features, B = bootstrap resamples, K = bucket size.

## Scalability (10x)

- Partitioned Parquet enables predicate pushdown by date/hashtag.
- TF-IDF feature cap and float32 bound vector memory.
- Collection can be sharded by hashtag/time window across machines; merge is Parquet concat + dedupe on `tweet_id`.

## Current results (this submission snapshot)

- 911 unique tweets collected across two scrape sessions
- 792 retained after cleaning, dedupe, and 24h filter
- Mean composite signal ≈ 0.046 (mildly bullish), with hourly CI bands in `outputs/`
- Further collection was limited by X rate-limiting / empty search renders after sustained scrolling

## Risks and mitigations

- X DOM/login challenges change frequently: selectors centralized; checkpoints preserve partial runs.
- Account login lockouts: cookie injection from a healthy browser session avoids the login form.
- Rate limits: jittered delays, multi-query/tab strategy, resume-from-disk collection.
- Secrets never committed; only `.env.example` is tracked.
