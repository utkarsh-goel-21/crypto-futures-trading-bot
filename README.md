# 🤖 Crypto Futures Trading Bot with CMA-ES Optimization
[![Python](https://img.shields.io/badge/Python-3.8%2B-blue)](https://www.python.org/)
[![Binance](https://img.shields.io/badge/Exchange-Binance-yellow)](https://www.binance.com/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Active-success)](https://github.com/utkarsh-goel-21/crypto-futures-trading-bot)

A futures trading bot optimized with CMA-ES algorithm on 13 months of historical data. Currently running on Binance with these 9 USDT perpetual pairs.

## 📊 Performance Results

### Optimization Results (13 Months of Data)

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

**Optimization details:** 
- Dataset: 13 months of 1-minute candles
- Costs included: 0.04% taker fees, 0.03% slippage, 0.01% spread
- Settings: 10x leverage, $100 USDT margin per trade

## 🚀 Key Features

### Key Features
- **Multi-indicator strategy**: Combines 14+ technical indicators with weighted signals
- **CMA-ES optimization**: Evolutionary algorithm for parameter optimization
- **Risk management**: Daily loss limits, position sizing, consecutive loss protection
- **Live trading**: Supports both testnet and mainnet on Binance Futures
- **Notifications**: Telegram and Discord alerts for trades

### Live Trading Features
- Runs on 9 pairs simultaneously
- Telegram/Discord notifications for every trade
- Full testnet support (practice with fake money first)
- SQLite database tracks everything
- WebSocket connections for real-time data

## 📁 Repository Structure

```
crypto-futures-trading-bot/
├── optimization.py                    # CMA-ES optimization engine
├── trading-bot/
│   ├── main.py                       # Main trading bot logic
│   ├── indicators.py                 # Technical indicator calculations
│   ├── config.py                     # Bot configuration
│   ├── credentials.py                # API credentials (user adds keys)
│   ├── environment.py                # Testnet/Mainnet toggle
│   ├── stats.py                      # Performance tracking
│   ├── enhanced_stats.py             # Advanced analytics
│   ├── telegram_notifier.py          # Telegram notifications
│   ├── discord_notifier.py           # Discord notifications
│   └── parameters/                   # Optimized parameters for each coin
│       ├── btcusdt_params.json
│       ├── ethusdt_params.json
│       └── ... (all 9 pairs)
├── requirements.txt                  # Python dependencies
├── LICENSE                          # MIT License with disclaimers
└── README.md                        # This file
```

## 🛠️ Installation

### What You Need
- Python 3.8+
- Binance account with Futures enabled
- Linux/Mac/Windows (WSL2)
- 4GB RAM minimum

### Quick Setup

1. **Clone the repository**
```bash
git clone https://github.com/utkarsh-goel-21/crypto-futures-trading-bot.git
cd crypto-futures-trading-bot
```

2. **Install dependencies**
```bash
pip install -r requirements.txt
```

3. **Configure API credentials**
```python
# Edit trading-bot/credentials.py
if USE_TESTNET:
    BINANCE_API_KEY = 'your_testnet_api_key'
    BINANCE_API_SECRET = 'your_testnet_api_secret'
else:
    BINANCE_API_KEY = 'your_mainnet_api_key'
    BINANCE_API_SECRET = 'your_mainnet_api_secret'
```

4. **Configure trading settings**
```python
# Edit trading-bot/config.py
LEVERAGE = 10                    # Futures leverage
MARGIN_PER_TRADE = 100           # USDT per trade
TELEGRAM_ENABLED = True          # Enable notifications
```

5. **Run the bot**
```bash
# For testnet (recommended for testing)
cd trading-bot
python main.py --testnet

# For mainnet (real trading)
python main.py
```

## ⚙️ Configuration

### Choose Your Mode
Edit `trading-bot/environment.py`:
```python
USE_TESTNET = True  # Start here! Switch to False when ready for real money
```

### Set Your Risk
Edit `trading-bot/config.py`:
```python
ACTIVE_COINS = ['BTCUSDT', 'ETHUSDT', ...]  # Pick your coins
LEVERAGE = 10                                # 10x is what I tested with
MARGIN_PER_TRADE = 100                      # $100 per trade
MAX_DAILY_TRADES = 50                       # Stop after 50 trades/day
```

### Get Notifications (Optional)
**Telegram:** Message @BotFather, create a bot, get your chat ID from @userinfobot

**Discord:** Right-click your channel → Integrations → Webhooks → Create

## 📈 How the Optimization Works

1. **Data**: 13 months of 1-minute candles for each coin

2. **Indicators**: 14 technical indicators calculated - RSI, MACD, Bollinger Bands, EMA crossovers, volume analysis, etc.

3. **CMA-ES Algorithm**: Evolutionary optimization over 100+ generations to find optimal indicator weights and parameters

4. **Backtesting**: Includes Binance fees (0.04%), slippage (0.03%), and spread (0.01%)

### Optimization Hardware Requirements

**Recommended**: Google Colab Pro with v5e-1 TPU runtime (96 cores, 300+ GB RAM) for fast optimization

**Alternative**: Any system with 8+ CPU cores and 16GB+ RAM (will take longer)

The optimization uses parallel processing - more cores = faster results. On Colab Pro with TPU runtime, each coin optimizes in 5-6 hours. On a regular 4-core laptop, expect 18-22 hours per coin.

### Signal Generation

```python
# Each indicator produces a signal between -1 and +1
# Signals are weighted and combined
Total Signal = Σ(Indicator Signal × Optimized Weight)

if Total Signal > Entry Threshold:
    Open LONG
elif Total Signal < -Entry Threshold:
    Open SHORT
```

## 🔄 Trading Logic

### Entry:
- Combined signal exceeds threshold
- No existing position for that coin
- Daily loss limit not reached

### Exit:
- Take profit reached
- Stop loss triggered
- Maximum holding time exceeded

## 📊 Performance Tracking

All trades are logged to SQLite database:
- Entry/exit prices and times
- Profit/loss per trade
- Win rates and statistics
- Signal strength at entry

## 🚀 Deployment

### Local/VPS
```bash
# Using screen for persistent sessions
screen -S trading-bot
python trading-bot/main.py
# Ctrl+A+D to detach
```

### AWS EC2
- Use t2.micro (free tier)
- Ubuntu 22.04 recommended
- Run with screen or systemd

### Docker
```bash
docker build -t trading-bot .
docker run -d trading-bot
```

## 🔒 Security

- Never commit API keys to git
- Test on testnet before mainnet
- Use stop losses (automated)
- Monitor trades via notifications
- Secure your server properly

## 🧪 Testing

### Run optimization
```bash
python optimization.py
# Note: Use Google Colab Pro with v5e-1 TPU for best performance
# Or run on a system with 8+ CPU cores
```

### Testnet trading
```bash
cd trading-bot
python main.py --testnet
```

## ⚠️ Disclaimer

**EDUCATIONAL PURPOSE ONLY**

This software is for learning algorithmic trading. The optimization results shown are from historical backtesting.

- No responsibility for financial losses
- Cryptocurrency trading carries high risk
- Past performance doesn't guarantee future results
- Only trade with money you can afford to lose

By using this software, you accept all risks and responsibility for your trading decisions.

## 📝 Contributing

Contributions welcome. Fork the repo and submit a pull request.

## 📜 License

This project is licensed under the MIT License with additional disclaimers - see [LICENSE](LICENSE) file for details.

## 🙏 Credits

- Binance API
- CMA-ES algorithm
- Open source Python community

## 📧 Support

For questions or issues: [GitHub Issues](https://github.com/utkarsh-goel-21/crypto-futures-trading-bot/issues)

---

⭐ **If you find this useful, please star the repository**

*August 2025*