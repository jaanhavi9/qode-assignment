# Indian Market Tweet Intelligence

Selenium-based pipeline that collects Indian equity-market discussion from X (Twitter), stores it in Parquet, and converts text into quantitative trading signals. No paid APIs and no official Twitter API.

## Scope

- Collect tweets for `#nifty50`, `#sensex`, `#intraday`, `#banknifty`
- Extract username, timestamp, content, engagement, mentions, hashtags
- Target: 2000 tweets from the last 24 hours
- Clean / normalize / dedupe, including Unicode-safe Indian-language text
- Parquet storage (flat + `date=*/hashtag=*` partitions)
- TF-IDF + market lexicon signals with bootstrap confidence intervals
- Memory-conscious plots via sampling and hourly aggregation

## Current run snapshot

| Metric | Value |
|---|---|
| Raw unique tweets scraped | 911 |
| After clean + 24h filter | 792 |
| Mean composite signal | ~0.046 (slightly bullish) |
| Primary tags observed | nifty50, sensex, mixed |

X rate-limiting stopped further collection mid-session. Re-run `python -m src.run collect` later to top up toward 2000; processing merges all `data/raw/tweets_*.jsonl` files.

## Project layout

```
config.yaml          # all tunables (no hardcoded scrape/analysis knobs in code)
src/
  collect.py         # Selenium collector + cookie/manual/auto login
  process.py         # clean, dedupe, Parquet writer
  analyze.py         # TF-IDF, lexicon signals, CI, plots
  run.py             # CLI entrypoint
  config_loader.py   # YAML + env loading
data/raw/            # JSONL checkpoints (local)
data/processed/      # Parquet dataset (local)
data/samples/        # committed sample rows + aggregated signals
outputs/             # plots + summary artifacts
docs/approach.md     # technical write-up
```

## Setup

```bash
cd qode-assignment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Install Google Chrome. Default login mode in `config.yaml` is `cookies`.

### Cookie login (recommended)

1. Log in to [https://x.com](https://x.com) in normal Chrome.
2. DevTools → Application → Cookies → `https://x.com`
3. Copy `auth_token` and `ct0` into `.env`:

```
X_AUTH_TOKEN=...
X_CT0=...
```

Other modes: `manual` (interactive Selenium login) or `auto` (username/password from `.env`).

## Run

```bash
python -m src.run run-all
```

Or by stage:

```bash
python -m src.run collect
python -m src.run process
python -m src.run analyze
```

Leave the Selenium Chrome window open during `collect`. Collection resumes from existing raw files and continues until `target_tweet_count` or query exhaustion.

## Outputs

| Path | Description |
|---|---|
| `data/raw/tweets_*.jsonl` | Crash-safe raw checkpoints |
| `data/processed/tweets.parquet` | Cleaned deduplicated dataset |
| `data/processed/date=*/hashtag=*/` | Partitioned Parquet layout |
| `data/samples/` | Sample CSV/Parquet for review |
| `outputs/aggregated_signals.csv` | Hourly signal + confidence intervals |
| `outputs/*.png` | Signal / engagement plots |
| `outputs/summary.json` | Run summary |
| `logs/pipeline.log` | Run log |

## Configuration

All tunables live in `config.yaml`. Secrets stay in `.env` (gitignored).

## Design notes

See [`docs/approach.md`](docs/approach.md) for architecture, complexity, anti-bot handling, and signal methodology.
