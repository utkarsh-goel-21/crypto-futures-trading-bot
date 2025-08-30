"""
TRADING BOT CONFIGURATION
========================
Easy config to add/remove coins
WITH TESTNET/MAINNET SUPPORT
"""

# Import environment setting
from environment import USE_TESTNET

# ========================================
# ACTIVE COINS TO TRADE
# ========================================
# Just add coin symbols here as you optimize them
ACTIVE_COINS = [
    'BTCUSDT',
    'SOLUSDT',
    'ETHUSDT',
    'BNBUSDT',
    'XRPUSDT',
    'DOGEUSDT',
    # 'SUIUSDT',  # Commented out - no optimization results yet
    'ADAUSDT',
    'LINKUSDT',
    'BCHUSDT'
]
# Total: 9 coins active

# ========================================
# NOTIFICATIONS
# ========================================
TELEGRAM_ENABLED = False  # Set to True and add your bot token/chat ID
TELEGRAM_BOT_TOKEN = 'YOUR_TELEGRAM_BOT_TOKEN'
TELEGRAM_CHAT_ID = 'YOUR_TELEGRAM_CHAT_ID'

# ========================================
# DISCORD SETTINGS
# ========================================
DISCORD_ENABLED = False  # Set to True and add your webhook URL
DISCORD_WEBHOOK_URL = 'YOUR_DISCORD_WEBHOOK_URL'

# ========================================
# TRADING SETTINGS
# ========================================
LEVERAGE = 10
MARGIN_PER_TRADE = 100  # $100 USDT margin per trade
POSITION_VALUE = MARGIN_PER_TRADE * LEVERAGE  # $1000 USDT position size

# Margin type
MARGIN_TYPE = 'ISOLATED'

# ========================================
# TIMEFRAME MAPPINGS
# ========================================
# Map timeframe combos to actual intervals
# IMPORTANT: Order must match optimization script exactly!
TIMEFRAME_MAP = {
    0: {'entry': '5m', 'trend': '15m'},   # Most coins use this
    1: {'entry': '5m', 'trend': '30m'},   # Alternative combo
    2: {'entry': '10m', 'trend': '30m'},
    3: {'entry': '15m', 'trend': '1h'},
    4: {'entry': '30m', 'trend': '2h'},
    5: {'entry': '1h', 'trend': '4h'},
    6: {'entry': '2h', 'trend': '4h'},
}

# ========================================
# SAFETY SETTINGS
# ========================================
# Emergency stop - stops all trading if daily loss exceeds this
EMERGENCY_STOP_LOSS_PCT = 50  # Stop if lose 10% in a day

# Maximum positions at once (across all coins)
MAX_TOTAL_POSITIONS = 10

# Minimum balance to keep trading
MIN_BALANCE_TO_TRADE = 20  # Stop if balance < $50 USDT

# ========================================
# LOGGING & MONITORING
# ========================================
LOG_TRADES = True
LOG_FILE = 'bot_trades.log'
DATABASE_FILE = 'trades.db'

# Console output
SHOW_INDICATORS = False  # Set True to see indicator values
SHOW_SIGNALS = True      # Show entry/exit signals

# ========================================
# API SETTINGS
# ========================================
# Delay between API calls (to avoid rate limits)
API_DELAY = 0.1  # seconds

# WebSocket reconnect settings
WS_RECONNECT_DELAY = 5  # seconds
WS_MAX_RECONNECT_ATTEMPTS = 10

# ========================================
# INDICATOR CALCULATION
# ========================================
# How many candles to fetch for indicator calculation
CANDLES_REQUIRED = 200  # Enough for all indicators

# Update frequency
INDICATOR_UPDATE_SECONDS = 5  # Recalculate every 5 seconds

# ========================================
# NOTIFICATIONS (Optional)
# ========================================


# ========================================
# TRADING MODE SETTINGS
# ========================================
# TESTNET automatically enables safe trading
if USE_TESTNET:
    PAPER_TRADING = False  # Can use real orders on testnet (it's fake money)
    print("📌 Testnet mode: Real orders with FAKE money")
else:
    # For MAINNET - be extra careful
    PAPER_TRADING = False  # Set to True for safety on mainnet
    print("⚠️  Mainnet mode: Real orders with REAL money!")

# ========================================
# DO NOT MODIFY BELOW
# ========================================
import os

# Auto-generate parameter file paths
PARAM_FILES = {}
for coin in ACTIVE_COINS:
    param_file = f'parameters/{coin.lower()}_params.json'
    if os.path.exists(param_file):
        PARAM_FILES[coin] = param_file
    else:
        print(f"⚠️ Warning: Parameter file not found for {coin}: {param_file}")

print(f"✅ Config loaded: Trading {len(ACTIVE_COINS)} coins")
print(f"  Coins: {', '.join(ACTIVE_COINS)}")
print(f"  Paper Trading: {'YES' if PAPER_TRADING else 'NO (REAL MONEY)'}")