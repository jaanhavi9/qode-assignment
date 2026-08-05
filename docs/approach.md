# Technical Approach

## Problem

Build a market-intelligence pipeline for Indian equities discussion on X under a hard constraint: no official/paid Twitter APIs. Collection must still approach a practical volume (2000 tweets / 24h), survive rate limiting, and feed a quantitative signal stack suitable for research / algo inputs.

## Architecture

```
collect (Selenium) -> process (clean/dedupe/Parquet) -> analyze (TF-IDF + lexicon + CI + plots)
```

Stages are independently runnable via `python -m src.run <stage>`. Raw JSONL checkpoints make collection crash-safe. Default collection is a single continuous run to `target_tweet_count`.

## Collection

- Browser automation via Selenium + Chrome (assignment requirement).
- Auth modes (config-driven):
  - `cookies`: inject `auth_token` + `ct0` from a normal browser session
  - `manual`: operator completes login in the Selenium window
  - `auto`: username/password from environment variables
- Single-run mode by default (`resume_from_existing: false`): one `collect` process starts at 0 and continues until 2000 unique tweets.
- Search queries scoped with `since:YYYY-MM-DD`; combined OR query first, then per-hashtag Live/Latest tabs across multiple rounds.
- Rate-limit pacing (tunable in `config.yaml`):
  - scroll sleep + jitter between infinite-scroll steps
  - pause between search navigations (`query_pause_sec`)
  - longer backoff when a search page renders empty (`empty_query_backoff_sec`)
  - cool-down between full query rounds (`between_round_pause_sec`)
- In-memory ordered set keyed by `tweet_id` for O(1) ingest dedupe.
- Checkpoint writes every N tweets; session recovery restarts Chrome if the browser disconnects.

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

- 2687 unique tweets collected via Selenium (no paid/Twitter API)
- 2566 retained after cleaning/dedupe
- **2348 tweets analyzed** (above the 2000 unique requirement)
- Coverage spans recent Indian market discussion (2026-08-03 → 2026-08-04)
- All required hashtag queries covered (`nifty50`, `sensex`, `intraday`, `banknifty`, plus combined/mixed)
- Composite signal artifacts and CI bands written under `outputs/summary.json` and plots

## Data structures

- `OrderedDict` / set of `tweet_id` for O(1) ingest dedupe
- JSONL append-only checkpoints for crash-safe streaming writes
- Pandas DataFrame for batch clean/transform; Parquet/ZSTD for columnar storage
- SciPy/sklearn sparse TF-IDF matrix (float32, feature-capped) for text vectors

## Risks and mitigations

- X DOM/login challenges change frequently: selectors centralized; checkpoints preserve partial runs.
- Account login lockouts: cookie injection from a healthy browser session avoids the login form.
- Rate limits: jittered scroll delays, query pauses, empty-page backoff, multi-round cool-downs.
- Secrets never committed; only `.env.example` is tracked.
