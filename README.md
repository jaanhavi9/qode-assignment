# Indian Market Tweet Intelligence

Selenium-based pipeline that collects Indian equity-market discussion from X (Twitter), stores it in Parquet, and converts text into quantitative trading signals. No paid APIs and no official Twitter API.

## Scope

- Collect tweets for `#nifty50`, `#sensex`, `#intraday`, `#banknifty`
- Extract username, timestamp, content, engagement, mentions, hashtags
- Target: at least 2000 unique tweets from recent market discussion
- Clean / normalize / dedupe, including Unicode-safe Indian-language text
- Parquet storage (flat + `date=*/hashtag=*` partitions)
- TF-IDF + market lexicon signals with bootstrap confidence intervals
- Memory-conscious plots via sampling and hourly aggregation

## Results snapshot

| Metric | Value |
|---|---|
| Unique tweets scraped | 2687 |
| After clean + dedupe | 2566 |
| Analyzed dataset | **2348** (>= 2000) |
| Time span (analyzed) | 2026-08-03 → 2026-08-04 |
| Mean composite signal | ~0.048 |
| Query tags | nifty50, sensex, intraday, banknifty, mixed |

Collection used Selenium against X search (`since:` window), cookie-session auth, rate-limit-aware pacing (scroll / query / round sleeps), checkpointed JSONL, and a single-run collector that continues until 2000 unique tweets. Analysis artifacts are under `outputs/` and sample rows under `data/samples/`.

## Project layout

```
config.yaml          # all tunables
src/
  collect.py         # Selenium collector
  process.py         # clean, dedupe, Parquet writer
  analyze.py         # TF-IDF, lexicon signals, CI, plots
  run.py             # CLI entrypoint
  config_loader.py   # YAML + env loading
data/raw/            # JSONL checkpoints (local, gitignored)
data/processed/      # Parquet dataset (local, gitignored)
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

Install Google Chrome. Configure secrets only in `.env` (never commit it).

### Authentication

Default `login_mode` in `config.yaml` is `cookies`:

1. Log in to https://x.com in a normal browser.
2. DevTools → Application → Cookies → `https://x.com`
3. Set in `.env`:

```
X_AUTH_TOKEN=...
X_CT0=...
```

Also supported: `manual` (interactive Selenium login) and `auto` (username/password via env vars).

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

Leave the Selenium Chrome window open during `collect`. By default `resume_from_existing` is `false`, so one `collect` invocation starts fresh and keeps going (with configured sleeps) until `target_tweet_count` (2000) is reached in a single run.

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

All tunables live in `config.yaml`. Credentials and tokens are read only from environment variables / `.env`.

## Design notes

See [`docs/approach.md`](docs/approach.md) for architecture, data structures, complexity, anti-bot handling, and signal methodology.
