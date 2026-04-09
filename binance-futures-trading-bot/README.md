# Crypto Futures Bot with SPY Regime Filter

The canonical live app lives in this folder. It combines a multi-indicator Binance Futures strategy with a daily SPY LSTM regime filter from the sibling `spy-predictor` project.

---

## What It Does

Trade 9 Binance USDT perpetual pairs through a live bot and monitor them through a local web UI.

The live system combines:
1. **Optimized multi-indicator crypto entries** per coin
2. **1-candle delayed execution** to match the live bot's intended evaluation flow
3. **Daily SPY `LONG_ONLY` / `SHORT_ONLY` bias** from `spy-predictor`
4. **Binance Futures execution** with exchange-side TP/SL orders
5. **A Flask monitor** for balances, positions, logs, and copy-trade followers

---

## Architecture Overview

```text
Monitor UI (/)
      |
      v
 web_server.py
      |
      v
   main.py
      |
      v
 indicator signals + per-coin parameters
      |
      v
 spy_integration.py -> sibling spy-predictor
      |
      v
 Binance Futures entries + TP/SL algo orders
      |
      v
 logs + SQLite + live monitor API
```

### SPY Gate Logic

| Crypto Signal | SPY Regime | Result |
|---------------|------------|--------|
| LONG | `LONG_ONLY` | **Allowed** |
| SHORT | `SHORT_ONLY` | **Allowed** |
| LONG | `SHORT_ONLY` | Blocked |
| SHORT | `LONG_ONLY` | Blocked |

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| **Exchange API** | `python-binance`, Binance Futures |
| **Strategy Engine** | Python, pandas, NumPy, `ta` |
| **Optimizer** | CMA-ES, SciPy |
| **Monitor UI** | Flask, Flask-CORS |
| **Persistence** | SQLite, rotating logs |
| **Notifications** | Telegram, Discord webhook |
| **Macro Filter** | `spy_integration.py` calling sibling `spy-predictor` |

---

## Project Structure

```text
binance-futures-trading-bot/
├── README.md
├── RUN_BOT.py
├── optimization.py
├── requirements.txt
├── spy_integration.py
├── test_connection.py
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

## Setup & Installation

### Prerequisites

- Python 3.10+
- Binance Futures Testnet account
- A discoverable sibling `spy-predictor` checkout

Recommended layout:

```text
parent-folder/
├── crypto-futures-trading-bot/
└── spy-predictor/
```

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure trading mode

Edit `trading-bot/environment.py`:

```python
USE_TESTNET = True
```

### 3. Add Binance credentials

Put your testnet credentials in `trading-bot/apikey_testnet.py`.

### 4. Verify Binance connectivity

```bash
python test_connection.py
```

### 5. Start the monitor + live bot

```bash
python RUN_BOT.py
```

Or run the monitor directly:

```bash
cd trading-bot
python web_server.py
```

---

## Monitor API Reference

### `GET /`
Serves the monitor UI.

### `GET /api/data`
Returns live bot status, balances, positions, logs, and SPY regime.

### `POST /api/start`
Starts the live bot.

### `POST /api/stop`
Stops the live bot.

### `GET /api/followers`
Lists copy-trade followers.

### `POST /api/followers`
Adds a follower account.

### `POST /api/followers/<id>/toggle`
Enables or disables a follower.

### `DELETE /api/followers/<id>`
Removes a follower.

---

## Optimization

Run the optimizer from this folder:

```bash
python optimization.py
```

Optimization characteristics:
- 13 months of 1-minute crypto data
- CMA-ES parameter search
- fees, slippage, and spread included
- parameter export to `trading-bot/parameters/*.json`

---

## Notes

- The live bot uses the SPY regime filter, but `optimization.py` remains crypto-only.
- The SPY regime is cached daily in `spy_regime_cache.json`.
- TP/SL orders are Binance Futures conditional algo orders and are tracked accordingly.
- Testnet should be used before any mainnet deployment.

---

## Disclaimer

This project is for educational and research purposes only. Crypto futures trading is risky, leverage magnifies losses, and past optimization performance does not guarantee future results.
