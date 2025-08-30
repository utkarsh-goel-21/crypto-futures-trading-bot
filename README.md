# 🤖 Advanced Crypto Futures Trading System
[![Python](https://img.shields.io/badge/Python-3.8%2B-blue)](https://www.python.org/)
[![Binance](https://img.shields.io/badge/Exchange-Binance-yellow)](https://www.binance.com/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Active-success)](https://github.com/utkarsh-goel-21/crypto-futures-trading-bot)

A sophisticated algorithmic trading system featuring CMA-ES optimized strategies, multi-indicator analysis, and automated risk management. This bot implements advanced technical analysis with evolutionary optimization to achieve consistent profitable trading across 9 major cryptocurrency futures pairs.

## 📊 Performance Overview

### Backtested Results (Optimized with CMA-ES)

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

*Results from CMA-ES optimization with realistic execution costs (fees, slippage, spread). Leverage: 10x, Position size: $100 USDT margin per trade.*

## 🚀 Key Features

### Trading Strategy
- **🎯 Weighted Signal System**: Combines 14+ technical indicators with optimized weights
- **📈 Multi-Timeframe Analysis**: Simultaneous analysis across multiple timeframes
- **🔄 Dynamic Position Management**: Automated entry/exit with TP/SL optimization
- **🛡️ Risk Management**: Daily loss limits, consecutive loss protection, position sizing

### Technical Architecture
- **🧬 CMA-ES Optimization**: State-of-the-art evolutionary algorithm for parameter tuning
- **⚡ High-Performance Computing**: Optimized backtesting with parallel processing
- **📊 TA Integration**: Comprehensive technical indicator library
- **🔧 Modular Design**: Clean, maintainable code structure

### Operational Features
- **💰 Multi-Coin Support**: Trade up to 9 cryptocurrency pairs simultaneously
- **📡 Real-Time Monitoring**: WebSocket connections for instant market data
- **📱 Notification System**: Telegram and Discord alerts for trades and status
- **💾 Database Tracking**: SQLite database for trade history and analytics
- **🧪 Testnet Support**: Full testnet environment for risk-free testing

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

### Prerequisites
- Python 3.8 or higher
- Binance account with Futures API access
- Ubuntu/Debian Linux (recommended) or Windows with WSL2
- Minimum 4GB RAM (8GB recommended)

### Quick Start

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

### Environment Settings
Edit `trading-bot/environment.py`:
```python
USE_TESTNET = True  # Set to False for mainnet trading
```

### Trading Parameters
Edit `trading-bot/config.py`:
```python
ACTIVE_COINS = ['BTCUSDT', 'ETHUSDT', ...]  # Coins to trade
LEVERAGE = 10                                # 1-125x leverage
MARGIN_PER_TRADE = 100                      # Position size in USDT
MAX_DAILY_TRADES = 50                       # Risk limit
```

### Notifications
**Telegram Setup:**
1. Create a bot with @BotFather
2. Get your chat ID with @userinfobot
3. Add to config.py

**Discord Setup:**
1. Create a webhook in your Discord server
2. Add webhook URL to config.py

## 📈 How the Optimization Works

### 1. Data Collection
- Downloads historical 1-minute candle data
- Processes into multiple timeframes
- Ensures data quality and continuity

### 2. Indicator Calculation
The system calculates 14+ technical indicators:
- **Momentum**: RSI, Stochastic, ADX, Momentum oscillator
- **Trend**: EMA crossovers, MACD, Market structure
- **Volatility**: Bollinger Bands, ATR
- **Volume**: Volume spikes, Volume moving averages
- **Support/Resistance**: Dynamic S/R levels

### 3. CMA-ES Optimization
- **Algorithm**: Covariance Matrix Adaptation Evolution Strategy
- **Population**: Adaptive population sizing
- **Iterations**: 100+ generations
- **Evaluation**: 10,000+ parameter combinations tested
- **Objective**: Maximize Sharpe ratio with risk constraints

### 4. Signal Generation
```python
Total Signal = Σ(Indicator Weight × Indicator Signal)
if Total Signal > Entry Threshold: OPEN LONG
if Total Signal < -Entry Threshold: OPEN SHORT
```

### 5. Backtesting Validation
- Realistic execution modeling (fees, slippage, spread)
- Walk-forward analysis
- Out-of-sample testing
- Risk-adjusted performance metrics

## 🔄 Trading Logic

### Entry Conditions
- Combined weighted signal exceeds threshold
- Risk management checks pass
- Market conditions favorable
- No conflicting positions

### Exit Conditions
- Take Profit reached (optimized levels)
- Stop Loss triggered (risk protection)
- Maximum holding time exceeded
- Opposing signal generated

## 📊 Performance Monitoring

### Real-Time Statistics
The bot tracks comprehensive metrics:
- Win rate by time period
- Average profit/loss per trade
- Maximum consecutive wins/losses
- Risk-adjusted returns (Sharpe, Sortino)
- Drawdown analysis

### Database Schema
All trades are logged with:
- Entry/exit prices and times
- Position size and leverage
- Profit/loss calculation
- Signal strength at entry
- Market conditions

## 🚀 Deployment

### Local Machine
```bash
# Run in background with nohup
nohup python trading-bot/main.py > bot.log 2>&1 &

# Or use screen
screen -S trading-bot
python trading-bot/main.py
# Detach with Ctrl+A+D
```

### AWS EC2 (Free Tier)
1. Launch Ubuntu 22.04 t2.micro instance
2. Install Python and dependencies
3. Clone repository
4. Configure API keys
5. Run with systemd or screen

### Docker Deployment
```dockerfile
FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "trading-bot/main.py"]
```

## 🔒 Security Best Practices

- ⚠️ **Never commit API keys** - Use environment variables
- 🧪 **Always test on testnet first** - Validate strategies safely
- 🛡️ **Set strict stop losses** - Protect your capital
- 📊 **Monitor regularly** - Set up alerts for unusual activity
- 🔐 **Secure your server** - Use firewall rules and SSH keys

## 🧪 Testing

### Running Optimization
```bash
python optimization.py
# Follow prompts to select coin and parameters
```

### Testnet Trading
```bash
# Ensure USE_TESTNET = True in environment.py
cd trading-bot
python main.py --testnet
```

## ⚠️ Risk Disclaimer

**IMPORTANT: EDUCATIONAL PURPOSE ONLY**

This software is provided for **EDUCATIONAL AND INFORMATIONAL PURPOSES ONLY**. 

### ⛔ Critical Warnings:
- The author(s) bear **NO RESPONSIBILITY** for any losses incurred
- Cryptocurrency trading involves **SUBSTANTIAL RISK OF LOSS**
- You may lose **ALL of your investment**
- Past performance does **NOT** guarantee future results
- **NEVER** invest money you cannot afford to lose

### 📚 Educational Use
This bot is intended for:
- Learning algorithmic trading concepts
- Understanding technical analysis
- Studying optimization techniques
- Research and backtesting strategies

By using this software, you acknowledge that you:
- Understand the risks involved
- Accept **FULL RESPONSIBILITY** for all outcomes
- Will not hold the authors liable for any losses

## 📝 Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Submit a pull request

## 📜 License

This project is licensed under the MIT License with additional disclaimers - see [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **Binance** for comprehensive API access
- **CMA-ES** algorithm developers
- **TA-Lib** for technical analysis tools
- **Python** community for excellent libraries

## 📧 Support

For issues and questions:
- Open an issue on [GitHub](https://github.com/utkarsh-goel-21/crypto-futures-trading-bot/issues)
- Review existing issues before creating new ones
- Provide detailed information for bug reports

---

⭐ **If you find this project useful, please consider giving it a star!**

*Last updated: August 2025*