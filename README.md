# Crypto Futures Bot with SPY Session Bias Filter

A Binance Futures trading system that combines a multi-indicator crypto strategy with a session-pinned SPY LSTM bias filter from the sibling `spy-predictor-remote` project. It includes an optimizer, a live trading bot, a web monitor, and optional copy-trade follower support.

Live monitor:

- https://trading-bot-3mbl.onrender.com/

Project presentation:

- https://docs.google.com/presentation/d/1hek5fDBWCMEg4ZhHSKIu8EP90_qPlgta/edit?usp=drivesdk&ouid=104891935307924335509&rtpof=true&sd=true

---

## What It Does

Run the live bot across 9 USDT perpetual pairs while enforcing a daily macro gate from SPY:

- _"Should new crypto entries be long-only or short-only today?"_
- _"What positions are currently open on Binance Futures?"_
- _"What does the live bot think right now on each candle close?"_
- _"How did the optimized parameter sets perform in crypto-only backtests?"_

The live system combines:
1. **Coin-specific multi-indicator logic** optimized from historical crypto data
2. **1-candle delayed execution** aligned with the live bot's intended backtest behavior
3. **Session-pinned SPY LSTM bias filter** fetched from the sibling `spy-predictor-remote` project
4. **Binance Futures execution** with market entries and exchange-side TP/SL orders
5. **Flask monitor UI** for balances, positions, logs, and SPY regime state

The canonical live application lives in `binance-futures-trading-bot/`.

---

## Architecture Overview

```text
User opens monitor UI
           |
           v
   +-------------------+
   | Flask Web Monitor |  <- trading-bot/web_server.py
   +---------+---------+
             |
             v
   +-------------------+
   | Live Trading Bot  |  <- trading-bot/main.py
   +---------+---------+
             |
             v
   +-------------------+
   | Technical Signals |  <- indicators.py + per-coin parameters
   +---------+---------+
             |
             v
   +-------------------+
   | SPY Session Gate  |  <- spy_integration.py -> sibling spy-predictor-remote
   +---------+---------+
             |
             v
   +-------------------+
   | Binance Futures   |  <- entries + TP/SL algo orders
   +---------+---------+
             |
             v
   +-------------------+
   | Logs / SQLite / UI|
   +-------------------+
```

### Signal Gate Logic

| Crypto Signal | SPY Regime | Result |
|---------------|------------|--------|
| LONG | `LONG_ONLY` | **Allowed** |
| SHORT | `SHORT_ONLY` | **Allowed** |
| LONG | `SHORT_ONLY` | Blocked |
| SHORT | `LONG_ONLY` | Blocked |

The SPY session bias is cached and reused across the active US trading session.

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| **Exchange API** | `python-binance`, Binance Futures Testnet/Mainnet |
| **Live Bot** | Python, pandas, NumPy |
| **Indicators** | `ta` + custom weighted signal logic |
| **Optimizer** | CMA-ES (`cma`), SciPy |
| **Monitor UI** | Flask, Flask-CORS, inline HTML/CSS/JS |
| **Persistence** | SQLite, rotating log files |
| **Notifications** | Telegram, Discord webhook |
| **Macro Filter** | Sibling `spy-predictor-remote` LSTM via `spy_integration.py` |

---

## Strategy Inputs

The live crypto bot uses optimized parameter sets per coin and combines several signal categories:

| Category | Examples |
|----------|----------|
| **Trend** | EMA direction, price vs EMA, higher-timeframe confirmation |
| **Momentum** | RSI, MACD-style momentum, stochastic behavior |
| **Volatility** | ATR-based checks, volatility filters |
| **Structure** | Support/resistance, market structure scoring |
| **Participation** | Volume spike and confirmation filters |
| **Macro Gate** | Daily SPY `LONG_ONLY` / `SHORT_ONLY` regime |

The optimizer/backtest path remains crypto-only. The SPY filter is currently applied to the live bot only.

---

## Project Structure

```text
crypto-futures-trading-bot/
├── README.md
├── .gitignore
└── binance-futures-trading-bot/
    ├── README.md
    ├── RUN_BOT.py                 # Local launcher for the monitor UI
    ├── optimization.py            # CMA-ES optimization / backtest path
    ├── spy_integration.py         # Daily SPY regime cache + filter
    ├── test_connection.py         # Binance testnet connectivity check
    ├── requirements.txt
    └── trading-bot/
        ├── main.py                # Live trading bot
        ├── web_server.py          # Monitor UI + JSON API
        ├── indicators.py          # Technical signal generation
        ├── config.py              # Coins, leverage, risk settings
        ├── environment.py         # Testnet/mainnet toggle
        ├── copy_trader.py         # Follower account mirroring
        ├── apikey_testnet.py      # Testnet API credentials
        ├── stats.py
        ├── enhanced_stats.py
        ├── telegram_notifier.py
        ├── discord_notifier.py
        └── parameters/            # Optimized JSON parameters for each coin
```

Runtime artifacts such as logs, SQLite databases, follower files, caches, and virtual environments are intentionally gitignored.

---

## Setup & Installation

### Prerequisites

- Python 3.10+
- A Binance Futures Testnet account
- A discoverable checkout of the sibling `spy-predictor` project

Recommended layout:

```text
parent-folder/
├── crypto-futures-trading-bot/
└── spy-predictor-remote/
```

---

### Live Bot Setup

#### 1. Clone the repository

```bash
git clone https://github.com/utkarsh-goel-21/crypto-futures-trading-bot.git
cd crypto-futures-trading-bot/binance-futures-trading-bot
```

#### 2. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

#### 3. Install dependencies

```bash
pip install -r requirements.txt
```

#### 4. Configure environment and API keys

Set testnet or mainnet mode in `trading-bot/environment.py`:

```python
USE_TESTNET = True
```

Add your Binance Futures testnet credentials to `trading-bot/apikey_testnet.py`.

#### 5. Verify the Binance connection

```bash
python test_connection.py
```

#### 6. Launch the monitor + live bot

```bash
python RUN_BOT.py
```

The monitor opens on `http://127.0.0.1:5000` by default unless that port is already occupied.

---

### Direct Monitor Launch

If you want to run the monitor directly:

```bash
cd trading-bot
python web_server.py
```

This starts:
- the Flask monitor UI
- the background live trading bot process
- account / position / log polling

---

## Monitor API Reference

### `GET /`
Serves the trading monitor UI.

### `GET /api/data`
Returns live monitor state.

**Response:**

```json
{
  "status": "Running",
  "environment": "TESTNET",
  "balance": 4918.59,
  "pnl": 0.0,
  "active_trades": [
    {
      "symbol": "BTCUSDT",
      "amount": 0.014,
      "entry_price": 71360.0,
      "pnl": -0.06,
      "side": "LONG"
    }
  ],
  "spy_regime": {
    "regime": "LONG_ONLY",
    "direction": "UP",
    "confidence": 0.8337,
    "as_of_date": "2026-04-08",
    "predicting_for": "2026-04-09",
    "market_data_source": "alphavantage",
    "cache_status": "cached"
  }
}
```

### `POST /api/start`
Starts the live bot process if it is not already running.

### `POST /api/stop`
Stops the live bot process.

### `GET /api/followers`
Lists configured copy-trade follower accounts.

### `POST /api/followers`
Adds a follower account.

### `POST /api/followers/<id>/toggle`
Enables or disables a follower account.

### `DELETE /api/followers/<id>`
Removes a follower account.

---

## Optimization & Backtesting

Run the crypto-only optimizer from the canonical app root:

```bash
python optimization.py
```

Optimization characteristics:
- **13 months** of 1-minute crypto candle data
- **CMA-ES** evolutionary search over strategy parameters
- **Trading costs included**: taker fees, slippage, spread
- **Per-coin parameter export** to `trading-bot/parameters/*.json`

### Optimization Snapshot

| Coin | Win Rate | Total Return | Sharpe Ratio | Max Drawdown |
|------|----------|--------------|--------------|--------------|
| **XRPUSDT** | 70.5% | +30.5% | 4.27 | -1.1% |
| **SOLUSDT** | 68.3% | +43.1% | 5.03 | -2.1% |
| **ETHUSDT** | 66.5% | +28.2% | 4.32 | -1.2% |
| **LINKUSDT** | 64.7% | +30.3% | 4.03 | -1.3% |
| **BNBUSDT** | 62.9% | +16.0% | 3.24 | -0.8% |
| **BTCUSDT** | 62.2% | +18.4% | 3.85 | -1.4% |
| **DOGEUSDT** | 62.2% | +34.5% | 4.05 | -1.6% |
| **ADAUSDT** | 61.3% | +33.0% | 3.78 | -1.6% |
| **BCHUSDT** | 60.6% | +23.5% | 3.71 | -1.0% |

---

## Live Execution Notes

- **SPY bias refresh**: cached for 24 hours in `spy_regime_cache.json`
- **Entry timing**: pending signal on one candle, execution on the next candle close
- **Exit orders**: TP/SL are placed as Binance Futures conditional algo orders
- **Positions**: one position per coin at a time
- **Mode toggle**: controlled by `trading-bot/environment.py`
- **Copy trading**: follower accounts can mirror master entries and exits from the monitor UI

---

## Notes

- The SPY integration expects a compatible `spy-predictor` checkout to be reachable from this repo's parent directory tree.
- The live bot and the optimizer are not identical today: the live bot uses the SPY daily gate, while the optimizer does not.
- The monitor is meant for local operation and lightweight control, not hardened production deployment.
- Testnet is strongly recommended before switching to mainnet.

---

## Disclaimer

This project is for educational and research purposes only. Crypto futures trading is high risk, leverage magnifies losses, and historical optimization results do not guarantee future performance. Use testnet first and never trade with money you cannot afford to lose.
