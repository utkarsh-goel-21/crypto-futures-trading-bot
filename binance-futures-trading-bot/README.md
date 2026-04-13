# Crypto Futures Bot with SPY Regime Filter

This is the canonical live bot folder. It runs a multi-coin Binance Futures strategy, gates entries with a daily SPY regime from `spy-predictor`, and exposes a Flask monitor for status, logs, positions, and copy-trade controls.

---

## What It Does

The live system combines:

1. Optimized per-coin crypto entries from `trading-bot/parameters/*.json`
2. 1-candle delayed execution so live behavior matches the intended backtest flow
3. Daily SPY `LONG_ONLY` / `SHORT_ONLY` gating from `spy_integration.py`
4. Binance Futures market entries with exchange-side TP/SL conditional orders
5. A Flask monitor for balances, open positions, logs, and follower management

---

## Current Architecture

```text
Monitor UI (/)
      |
      v
 web_server.py
      |
      v
   main.py
      |
      +--> Binance futures kline websockets
      |      Used for candle-close detection and signal generation
      |
      +--> Binance futures user-data websocket
      |      Used for order/account reconciliation before REST fallback
      |
      +--> spy_integration.py
             Daily SPY regime cache from deployed SPY API or local sibling project
```

### Execution Model

- Candle-close detection is websocket-driven.
- Signal generation uses buffered candles in memory, not repeated REST kline fetches.
- Entry orders are placed at the next candle close after a pending signal is created.
- TP/SL orders are exchange-side Binance futures conditional orders.
- Order/account state is reconciled from the futures private stream first, with REST only as a slower fallback.

### SPY Gate Logic

| Crypto Signal | SPY Regime | Result |
|---------------|------------|--------|
| LONG | `LONG_ONLY` | Allowed |
| SHORT | `SHORT_ONLY` | Allowed |
| LONG | `SHORT_ONLY` | Blocked |
| SHORT | `LONG_ONLY` | Blocked |

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Exchange API | `python-binance`, Binance USD-M futures |
| Strategy Engine | Python, pandas, NumPy, `ta` |
| Live Market Feed | Binance futures kline websockets |
| Order/Account Feed | Binance futures user-data websocket |
| Monitor UI | Flask, Flask-CORS |
| Persistence | SQLite + rotating logs locally, in-memory only on Render by default |
| Notifications | Telegram notifier hooks |
| Macro Filter | `spy_integration.py` with SPY predictor API or local sibling fallback |

---

## Project Structure

```text
binance-futures-trading-bot/
├── README.md
├── RUN_BOT.py
├── optimization.py
├── requirements.txt
├── runtime_config.py
├── spy_integration.py
├── test_connection.py
├── .env.example
└── trading-bot/
    ├── main.py
    ├── web_server.py
    ├── copy_trader.py
    ├── indicators.py
    ├── config.py
    ├── environment.py
    ├── apikey_testnet.py
    ├── stats.py
    ├── enhanced_stats.py
    ├── telegram_notifier.py
    ├── discord_notifier.py
    └── parameters/
```

---

## Setup

### Prerequisites

- Python 3.10+
- Binance Futures Testnet account
- Either:
  - the deployed SPY API URL, or
  - a discoverable sibling `spy-predictor` checkout

Recommended local layout if you want sibling-project fallback:

```text
parent-folder/
├── crypto-futures-trading-bot/
└── spy-predictor/
```

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure runtime

You can use environment variables or local key files.

Relevant env vars:

```bash
USE_TESTNET=true

BINANCE_TESTNET_API_KEY=...
BINANCE_TESTNET_SECRET_KEY=...

BINANCE_API_KEY=...
BINANCE_SECRET_KEY=...

SPY_PREDICTOR_API_URL=https://spy-predictor-api.onrender.com
```

Notes:

- `USE_TESTNET=true` is the safe default.
- If env vars are not set, the code falls back to local files such as `trading-bot/apikey_testnet.py`.
- If `SPY_PREDICTOR_API_URL` is not set, `spy_integration.py` falls back to a local sibling `spy-predictor` project.

### 3. Verify Binance connectivity

```bash
python test_connection.py
```

### 4. Start the monitor and bot

From the repo root:

```bash
python RUN_BOT.py
```

Or run the monitor directly:

```bash
cd trading-bot
python web_server.py
```

---

## Render Runtime Notes

This repo is deployable on Render as a web service through `trading-bot/web_server.py`.

Recommended Render root/start:

```bash
Root Directory: binance-futures-trading-bot
Start Command: python trading-bot/web_server.py
```

Render-specific runtime behavior:

- `IS_RENDER` is auto-detected from Render env vars.
- `ENABLE_RUNTIME_FILES` defaults to `false` on Render.
- That means logs, SQLite DB files, follower files, and SPY file cache are disabled on Render by default.
- The monitor still shows logs because child-process stdout is captured in memory.
- Copy-trade follower state works during the current process lifetime, but does not persist across Render restarts unless you explicitly re-enable runtime files or add external storage.

Keepalive behavior:

- `web_server.py` starts a simple self-ping thread for `/health`
- it is separate from the trading bot subprocess, so it keeps running even if the bot is stopped from the UI
- this is a best-effort Render keepalive, not a hard uptime guarantee

---

## Monitor API

### `GET /`

Serves the monitor UI.

### `GET /health`

Lightweight health endpoint used by Render and self-ping.

### `GET /api/data`

Returns live bot status, balances, positions, logs, and SPY regime info.

### `POST /api/start`

Starts the bot subprocess.

### `POST /api/stop`

Stops the bot subprocess.

### `GET /api/followers`

Lists copy-trade followers.

### `POST /api/followers`

Adds a follower account.

### `POST /api/followers/<id>/toggle`

Enables or disables a follower.

### `DELETE /api/followers/<id>`

Removes a follower.

---

## SPY Integration

`spy_integration.py` caches the SPY regime for 24 hours.

Current behavior:

- first request fetches the regime from `SPY_PREDICTOR_API_URL` if configured
- otherwise it falls back to the local sibling `spy-predictor`
- the result is cached in memory
- outside Render, it can also persist to `spy_regime_cache.json`
- on refresh failure, stale cache is reused if available

That means the crypto bot does not hit the SPY predictor on every coin signal.

---

## Live Trading Behavior

Important current rules:

- one active position per coin at a time
- new entries are skipped if that coin is already tracked as open
- blocked SPY-misaligned signals are re-evaluated on the next candle close, not permanently ignored
- TP/SL orders are tracked as conditional futures orders
- symbol filters are cached from Binance and used for exact quantity/price formatting

Recent live/runtime improvements:

- market-data REST polling was replaced by websocket candle handling
- futures private user stream is used for order/account reconciliation before REST fallback
- symbol `LOT_SIZE`, `MARKET_LOT_SIZE`, and `PRICE_FILTER` are cached once and used for precise order formatting
- REST backoff is applied when Binance returns `-1003`

---

## Optimization

Run the optimizer from this folder:

```bash
python optimization.py
```

Optimization notes:

- optimization remains crypto-only
- the SPY regime is a live gating layer, not part of the optimizer objective
- optimized parameters are exported to `trading-bot/parameters/*.json`

---

## Safety Notes

- Use testnet before any mainnet run.
- Mainnet mode uses real money and leverage.
- Render free-tier deployment is useful for demo/testing, but not a substitute for proper production operations.
- If you need persistent follower state, trade history, or durable logs in production, use external storage instead of relying on ephemeral Render filesystem behavior.

---

## Disclaimer

This project is for educational and research purposes only. Crypto futures trading is risky, leverage magnifies losses, and past optimization performance does not guarantee future results.
