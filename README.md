# Indian Market Tweet Intelligence

Selenium-based pipeline that collects Indian equity-market discussion from X (Twitter), stores it in Parquet, and converts text into quantitative trading signals. No paid APIs and no official Twitter API.

## Scope

- Collect tweets for `#nifty50`, `#sensex`, `#intraday`, `#banknifty`
- Extract username, timestamp, content, engagement, mentions, hashtags
- Target: at least 2000 tweets from the last 24 hours
- Clean / normalize / dedupe, including Unicode-safe Indian-language text
- Parquet storage (flat + `date=*/hashtag=*` partitions)
- TF-IDF + market lexicon signals with bootstrap confidence intervals
- Memory-conscious plots via sampling and hourly aggregation

## Results snapshot

| Metric | Value |
|---|---|
| Unique tweets scraped | 2237 |
| After clean + dedupe | 2134 |
| After clean + 24h filter | 1822 |
| Mean composite signal | see `outputs/summary.json` |
| Query tags | nifty50, sensex, intraday, banknifty, mixed |

Collection used Selenium against X search with cookie-session auth, jittered scrolling, checkpointed JSONL, and resume-from-disk dedupe. X rate limits constrained further growth inside the rolling 24h window.

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

Leave the Selenium Chrome window open during `collect`. Collection resumes from existing raw files until `target_tweet_count` or query exhaustion.

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
