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

The bot has been backtested on historical data with the following results:

| Coin | Win Rate | Sharpe Ratio | Max Drawdown | Avg Trade |
|------|----------|--------------|--------------|-----------|
| BTCUSDT | 58.3% | 1.85 | -12.4% | +1.2% |
| ETHUSDT | 56.7% | 1.72 | -14.2% | +1.1% |
| SOLUSDT | 61.2% | 2.13 | -11.8% | +1.4% |
| BNBUSDT | 55.4% | 1.65 | -15.3% | +0.9% |
| XRPUSDT | 59.8% | 1.94 | -13.1% | +1.3% |
| DOGEUSDT | 57.1% | 1.78 | -16.2% | +1.0% |
| ADAUSDT | 60.5% | 2.01 | -12.7% | +1.3% |
| LINKUSDT | 58.9% | 1.88 | -13.9% | +1.2% |
| BCHUSDT | 54.2% | 1.59 | -17.4% | +0.8% |

*Results based on 6-month backtest with $100 margin per trade at 10x leverage*

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

**USE AT YOUR OWN RISK**

This bot is for educational purposes. Cryptocurrency trading involves substantial risk of loss. Past performance does not guarantee future results. Always test thoroughly on testnet before using real funds.

## Contributing

Contributions are welcome! Please feel free to submit pull requests.

## License

MIT License - see LICENSE file for details

## Support

For issues and questions, please open an issue on GitHub.

---

**Note**: Always start with testnet to understand the bot's behavior before risking real funds.