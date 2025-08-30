# Crypto Futures Trading Bot with CMA-ES Optimization

An automated cryptocurrency futures trading bot for Binance with advanced parameter optimization using CMA-ES algorithm. Features multi-indicator analysis, risk management, and backtested strategies across 9 major USDT perpetual pairs.

## Features

- **CMA-ES Optimization**: Advanced evolutionary algorithm for finding optimal trading parameters
- **Multi-Indicator Strategy**: Combines RSI, MACD, EMA, Bollinger Bands, and volume analysis
- **Risk Management**: Automated stop-loss and take-profit with position sizing
- **Live Trading**: Supports both Binance Testnet and Mainnet
- **9 Optimized Pairs**: BTC, ETH, SOL, BNB, XRP, DOGE, ADA, LINK, BCH (all USDT perpetuals)
- **Telegram/Discord Notifications**: Real-time trade alerts and performance updates

## Performance Metrics

The bot has been optimized using CMA-ES algorithm with the following backtested results:

| Coin | Win Rate | Sharpe Ratio | Net Return | Max Drawdown |
|------|----------|--------------|------------|--------------|
| BTCUSDT | 62.2% | 3.85 | +18.4% | -1.4% |
| ETHUSDT | 66.5% | 4.32 | +28.2% | -1.2% |
| SOLUSDT | 68.3% | 5.03 | +43.1% | -2.1% |
| BNBUSDT | 62.9% | 3.24 | +16.0% | -0.8% |
| XRPUSDT | 70.5% | 4.27 | +30.5% | -1.1% |
| DOGEUSDT | 62.2% | 4.05 | +34.5% | -1.6% |
| ADAUSDT | 61.3% | 3.78 | +33.0% | -1.6% |
| LINKUSDT | 64.7% | 4.03 | +30.3% | -1.3% |
| BCHUSDT | 60.6% | 3.71 | +23.5% | -1.0% |

*Results from CMA-ES optimization backtesting with $100 margin per trade at 10x leverage. Net returns shown are from backtesting periods. Past performance does not guarantee future results.*

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/crypto-futures-trading-bot.git
cd crypto-futures-trading-bot
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure API credentials:
```python
# Edit trading-bot/credentials.py
BINANCE_API_KEY = 'your_api_key'
BINANCE_API_SECRET = 'your_api_secret'
```

4. Set trading parameters:
```python
# Edit trading-bot/config.py
LEVERAGE = 10
MARGIN_PER_TRADE = 100  # USDT
```

## Usage

### Running the Trading Bot

For testnet (recommended for testing):
```bash
cd trading-bot
python main.py --testnet
```

For mainnet (real trading):
```bash
cd trading-bot
python main.py
```

### Running Parameter Optimization

To optimize parameters for a specific coin:
```bash
python optimization.py
```

The optimization script will:
1. Load historical price data
2. Run CMA-ES optimization (100+ generations)
3. Save optimized parameters to `parameters/` folder
4. Generate performance reports

## Project Structure

```
crypto-futures-trading-bot/
├── optimization.py           # CMA-ES parameter optimization
├── trading-bot/
│   ├── main.py              # Main trading bot
│   ├── config.py            # Trading configuration
│   ├── credentials.py       # API credentials (add your keys)
│   ├── environment.py       # Testnet/Mainnet toggle
│   ├── indicators.py        # Technical indicators
│   ├── stats.py            # Performance tracking
│   ├── parameters/         # Optimized parameters for each coin
│   │   ├── btcusdt_params.json
│   │   ├── ethusdt_params.json
│   │   └── ...
│   ├── telegram_notifier.py # Telegram notifications
│   └── discord_notifier.py  # Discord webhooks
├── requirements.txt         # Python dependencies
└── LICENSE                 # MIT License
```

## How It Works

### Trading Strategy

The bot uses a weighted signal system combining multiple technical indicators:

1. **RSI (Relative Strength Index)**: Identifies overbought/oversold conditions
2. **MACD**: Tracks momentum and trend changes
3. **EMA Crossovers**: Fast/slow moving average signals
4. **Bollinger Bands**: Volatility-based entry points
5. **Volume Analysis**: Confirms signal strength

Each indicator generates a signal (-1 to +1) with optimized weights. When the combined signal exceeds the threshold, a trade is executed.

### CMA-ES Optimization

The Covariance Matrix Adaptation Evolution Strategy (CMA-ES) optimizes:
- Indicator weights (importance of each signal)
- Entry/exit thresholds
- Risk parameters (TP/SL ratios)
- Timeframe-specific settings

The optimization maximizes Sharpe ratio while maintaining reasonable win rates and controlling drawdown.

## Risk Management

- **Position Sizing**: Fixed margin per trade with configurable leverage
- **Stop Loss**: Automatically set based on optimized parameters
- **Take Profit**: Multiple TP levels for partial exits
- **Max Positions**: Limits concurrent trades per coin
- **Isolated Margin**: Each position is isolated to prevent cascade liquidations

## Requirements

- Python 3.8+
- Binance account with Futures enabled
- API keys with Futures trading permissions
- Minimum recommended balance: $1000 USDT

## Safety Features

- Testnet support for risk-free testing
- Position size limits
- Automatic error handling and recovery
- Detailed logging of all trades
- Emergency stop functionality

## Disclaimer

**⚠️ IMPORTANT: EDUCATIONAL PURPOSE ONLY**

This software is provided for **EDUCATIONAL AND INFORMATIONAL PURPOSES ONLY**. It is NOT financial advice or a recommendation to trade.

**USE AT YOUR OWN RISK**

- The author(s) bear **NO RESPONSIBILITY** for any losses incurred through use of this software
- Cryptocurrency trading involves **SUBSTANTIAL RISK OF LOSS**
- You may lose **ALL of your investment**
- Past performance does **NOT** guarantee future results
- Always test thoroughly on testnet before using real funds
- Never invest money you cannot afford to lose
- By using this software, you accept **FULL RESPONSIBILITY** for all trading decisions and outcomes

## Contributing

Contributions are welcome! Please feel free to submit pull requests.

## License

MIT License - see LICENSE file for details

## Support

For issues and questions, please open an issue on GitHub.

---

**Note**: Always start with testnet to understand the bot's behavior before risking real funds.