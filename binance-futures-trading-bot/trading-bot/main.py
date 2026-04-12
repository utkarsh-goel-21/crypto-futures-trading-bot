"""
MULTI-COIN TRADING BOT - SIGNAL REVERSAL VERSION
===============================================
Modified version that checks signals on every candle close and
reverses positions when opposing signals are detected
WITH LOG ROTATION AND DATABASE CLEANUP

KEY CHANGES FROM ORIGINAL:
1. Added signal checking on EVERY candle close (not just when no position)
2. Added close_position_market() method to close positions immediately
3. Modified check_signal_on_candle_close() to handle reversals
4. Added enter_new_position() method to separate entry logic
5. Exit reasons now include "SIGNAL_REVERSAL" in addition to TP/SL/MANUAL
"""

import json
import time
import logging
from logging.handlers import RotatingFileHandler
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, Any
import threading
import signal
import sys
import pandas as pd
import platform
import asyncio
from pathlib import Path

from binance.client import Client
from binance.exceptions import BinanceAPIException
from decimal import Decimal, ROUND_DOWN

PROJECT_ROOT = next(
    (parent for parent in Path(__file__).resolve().parents if (parent / "spy_integration.py").exists()),
    None
)
if PROJECT_ROOT is None:
    raise RuntimeError("Could not locate project root for SPY regime integration.")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Import configuration and indicators
from config import *
from environment import USE_TESTNET
from spy_integration import SpyRegimeFilter
from runtime_config import (
    TESTNET_BASE_URL,
    TESTNET_FUTURES_URL,
    get_binance_credentials,
)

api_key, secret_key = get_binance_credentials(USE_TESTNET)
    
from indicators import IndicatorCalculator
from telegram_notifier import notifier
from stats import stats_tracker
from enhanced_stats import enhanced_stats
from copy_trader import copy_open_to_followers, copy_close_to_followers


# Import Discord if available (DISABLED)
# Discord notifications are currently disabled
DISCORD_AVAILABLE = False
# try:
#     from discord_notifier import discord
#     DISCORD_AVAILABLE = True
# except ImportError:
#     DISCORD_AVAILABLE = False
#     print("⚠️ Discord notifier not found - continuing without Discord")

# ADD THE WINDOWS FIX HERE
if platform.system() == 'Windows':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ========================================
# UPDATED LOGGING SETUP WITH ROTATION
# ========================================
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# Rotating file handler - 10MB per file, keep 5 files
rotating_handler = RotatingFileHandler(
    LOG_FILE, 
    maxBytes=10*1024*1024,  # 10MB
    backupCount=5,  # Keep 5 old files
    encoding='utf-8'
)
rotating_handler.setFormatter(log_formatter)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    handlers=[rotating_handler, console_handler]
)

logger = logging.getLogger(__name__)


def serialize_order_reference(order_response):
    """Convert a Binance order payload into a stable reference string."""
    if not order_response:
        return None

    if isinstance(order_response, str):
        return order_response

    if isinstance(order_response, dict):
        order_id = order_response.get('orderId')
        if order_id not in (None, ''):
            return f"oid:{order_id}"

        algo_id = order_response.get('algoId')
        if algo_id not in (None, ''):
            return f"aid:{algo_id}"

        client_order_id = order_response.get('clientOrderId')
        if client_order_id:
            return f"cid:{client_order_id}"

        client_algo_id = order_response.get('clientAlgoId')
        if client_algo_id:
            return f"caid:{client_algo_id}"

    return None


def format_order_reference(order_ref):
    """Return a human-readable order reference for logs."""
    if not order_ref:
        return 'N/A'

    if isinstance(order_ref, str) and ':' in order_ref:
        return order_ref.split(':', 1)[1]

    return str(order_ref)

# ========================================
# DATABASE CLEANUP FUNCTION (NEW)
# ========================================
def cleanup_old_trades(days_to_keep=30):
    """Remove trades older than specified days from database"""
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        # Create table if not exists (removed DEFAULT CURRENT_TIMESTAMP)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME,
                coin TEXT,
                side TEXT,
                entry_price REAL,
                exit_price REAL,
                pnl_pct REAL,
                pnl_value REAL,
                exit_type TEXT
            )
        ''')
        
        # Delete old trades (using UTC)
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_to_keep)
        cursor.execute(
            "DELETE FROM trades WHERE timestamp < ?", 
            (cutoff_date.strftime('%Y-%m-%d %H:%M:%S'),)
        )
        
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        
        if deleted > 0:
            logger.info(f"🧹 Cleaned up {deleted} old trades from database")
            
    except Exception as e:
        logger.error(f"Database cleanup error: {e}")

def save_trade_to_db(trade_data):
    """Save trade to database with UTC timestamp"""
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        
        # Create table if not exists (removed DEFAULT CURRENT_TIMESTAMP)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME,
                coin TEXT,
                side TEXT,
                entry_price REAL,
                exit_price REAL,
                pnl_pct REAL,
                pnl_value REAL,
                exit_type TEXT
            )
        ''')
        
        # Get UTC timestamp explicitly
        utc_timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        
        # Insert trade with UTC timestamp
        cursor.execute('''
            INSERT INTO trades (timestamp, coin, side, entry_price, exit_price, pnl_pct, pnl_value, exit_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            utc_timestamp,  # Now explicitly UTC
            trade_data['coin'],
            trade_data['side'],
            trade_data['entry_price'],
            trade_data['exit_price'],
            trade_data['pnl_pct'],
            trade_data['pnl_value'],
            trade_data['exit_type']
        ))
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        logger.error(f"Database save error: {e}")

# ========================================
# POSITION TRACKER
# ========================================
class PositionTracker:
    """Tracks open positions for each coin"""
    
    def __init__(self):
        self.positions = {}  # {coin: position_data}
        self.lock = threading.Lock()
    
    def has_position(self, coin):
        """Check if coin has open position"""
        with self.lock:
            return coin in self.positions and self.positions[coin] is not None
    
    def add_position(self, coin, data):
        """Add new position"""
        with self.lock:
            self.positions[coin] = data
            logger.info(f"📈 Position opened: {coin} - {data['side']} @ ${data['entry_price']:.2f}")
            logger.info(f"   TP Order ID: {format_order_reference(data.get('tp_order_id'))}")
            logger.info(f"   SL Order ID: {format_order_reference(data.get('sl_order_id'))}")
    
    def remove_position(self, coin):
        """Remove closed position"""
        with self.lock:
            if coin in self.positions:
                del self.positions[coin]
                logger.info(f"📉 Position closed: {coin}")
    
    def get_position(self, coin):
        """Get position data"""
        with self.lock:
            return self.positions.get(coin)
    
    def update_position(self, coin, key, value):
        """Update specific position data"""
        with self.lock:
            if coin in self.positions:
                self.positions[coin][key] = value
    
    def total_positions(self):
        """Count total open positions"""
        with self.lock:
            return len(self.positions)

# ========================================
# COIN MANAGER
# ========================================
class CoinManager:
    """Manages parameters and state for each coin"""
    
    def __init__(self, coin, param_file):
        self.coin = coin
        self.params = self.load_parameters(param_file)
        self.latest_candles = {}  # Store latest candle data
        self.indicators = {}      # Store calculated indicators
        self.last_signal = None   # Track last signal to avoid duplicates
        
        # Initialize indicator calculator
        self.indicator_calc = IndicatorCalculator(coin, self.params)
        
        # Store dataframes and track candle closes
        self.entry_df = None  # Store entry timeframe data
        self.trend_df = None  # Store trend timeframe data
        self.candle_closed = {'entry': False, 'trend': False}  # Track closed candles
        
        # Extract timeframes
        tf_combo = TIMEFRAME_MAP[int(self.params['parameters']['timeframe_combo'])]
        self.entry_timeframe = tf_combo['entry']
        self.trend_timeframe = tf_combo['trend']
        
        logger.info(f"✅ Loaded {coin}: Entry={self.entry_timeframe}, Trend={self.trend_timeframe}")
        logger.info(f"   TP: {self.params['parameters']['tp_percent']*100:.2f}% | SL: {self.params['parameters']['sl_percent']*100:.2f}%")
    
    def load_parameters(self, param_file):
        """Load parameters from JSON file"""
        with open(param_file, 'r') as f:
            return json.load(f)

# ========================================
# MAIN TRADING BOT - REVERSAL VERSION
# ========================================
class TradingBot:
    """Main bot controller - OPTIMIZATION-ALIGNED (no reversal, 1-candle delay)"""
    
    def __init__(self):
        logger.info("="*60)
        logger.info("🤖 INITIALIZING TRADING BOT - OPTIMIZATION-ALIGNED VERSION")
        logger.info("📊 1-candle delay matching backtest logic (NO REVERSAL)")
        logger.info("⏰ Signal on candle N-1 → Execute at candle N close (market price)")
        logger.info("📈 Enhanced stats tracking enabled with per-coin win rates")
        
        # Show environment
        if USE_TESTNET:
            logger.info("🧪 TESTNET MODE - FAKE MONEY")
        else:
            logger.info("💰 MAINNET MODE - REAL MONEY")
        logger.info("="*60)
        
        # Initialize Binance client with testnet support
        if USE_TESTNET:
            self.client = Client(api_key, secret_key, testnet=True)
            # Override URLs for testnet
            self.client.API_URL = TESTNET_BASE_URL
            self.client.FUTURES_URL = TESTNET_FUTURES_URL
            self.client.FUTURES_DATA_URL = TESTNET_FUTURES_URL
            logger.info(f"📍 Connected to: {TESTNET_BASE_URL}")
        else:
            self.client = Client(api_key, secret_key)
        
        # Initialize components
        self.position_tracker = PositionTracker()
        self.coin_managers = {}
        self.running = False
        self.account_balance = 0
        self.daily_pnl = 0
        
        # OPTIMIZATION ALIGNMENT: Pending signals for delayed execution
        self.pending_signals = {}  # {coin: {'signal': 'LONG/SHORT', 'strength': float, 'filters': dict}}
        self.spy_regime_filter = SpyRegimeFilter(PROJECT_ROOT)

        stats_tracker.update_position_tracker(self.position_tracker)

        
        # Schedule daily cleanup at midnight
        self.last_cleanup = datetime.now(timezone.utc).date()
        
        # Load coin configurations
        self.load_coins()
        
        # Check account
        self.check_account()
        
        # Set up leverage and margin mode
        self.setup_trading_params()

        # Preload the daily SPY regime so entry gating uses a stable cached bias.
        self.log_active_spy_regime()

    def log_active_spy_regime(self):
        """Log the currently active cached SPY regime."""
        try:
            regime = self.spy_regime_filter.get_regime()
            logger.info(
                "🧭 Active SPY regime: %s | as_of=%s | predicting_for=%s | source=%s | cache=%s",
                regime.get('regime', 'unknown'),
                regime.get('as_of_date', 'unknown'),
                regime.get('predicting_for', 'unknown'),
                regime.get('market_data_source', 'unknown'),
                regime.get('cache_status', 'unknown'),
            )
        except Exception as exc:
            logger.error(f"❌ Failed to load SPY regime on startup: {exc}")

    def is_signal_allowed_by_spy(self, coin, signal, phase):
        """Allow only trades aligned with the cached daily SPY regime."""
        allowed, regime = self.spy_regime_filter.is_signal_allowed(signal)
        if regime is None:
            logger.warning(f"🚫 {coin} {phase} blocked: SPY regime unavailable")
            return False

        if allowed:
            return True

        logger.info(
            "🚫 %s %s blocked by SPY regime %s | signal=%s | as_of=%s | predicting_for=%s | cache=%s",
            coin,
            phase,
            regime.get('regime', 'unknown'),
            signal,
            regime.get('as_of_date', 'unknown'),
            regime.get('predicting_for', 'unknown'),
            regime.get('cache_status', 'unknown'),
        )
        return False
    
    def load_coins(self):
        """Load all coin configurations"""
        for coin in ACTIVE_COINS:
            if coin in PARAM_FILES:
                self.coin_managers[coin] = CoinManager(coin, PARAM_FILES[coin])
            else:
                logger.warning(f"⚠️ No parameters for {coin}, skipping")
    
    def check_account(self):
        """Check account balance and status"""
        try:
            account = self.client.futures_account()
            
            # Find USDT balance
            for asset in account['assets']:
                if asset['asset'] == 'USDT':
                    self.account_balance = float(asset['availableBalance'])
                    break
            
            logger.info(f"💰 Account Balance: ${self.account_balance:.2f} USDT")
            
            
            if self.account_balance < MIN_BALANCE_TO_TRADE:
                logger.error(f"❌ Insufficient balance! Need ${MIN_BALANCE_TO_TRADE} USDT")
                sys.exit(1)
                
        except Exception as e:
            logger.error(f"❌ Failed to check account: {e}")
            sys.exit(1)
    
    def setup_trading_params(self):
        """Set leverage and margin mode for all coins"""
        for coin in ACTIVE_COINS:
            try:
                # Set leverage
                self.client.futures_change_leverage(symbol=coin, leverage=LEVERAGE)
                
                # Set margin type to isolated
                try:
                    self.client.futures_change_margin_type(symbol=coin, marginType=MARGIN_TYPE)
                except BinanceAPIException as e:
                    if 'No need to change' not in str(e):
                        logger.warning(f"⚠️ Could not set margin type for {coin}: {e}")
                
                logger.info(f"✅ {coin}: {LEVERAGE}X leverage, {MARGIN_TYPE} margin")
                
            except Exception as e:
                logger.error(f"❌ Failed to setup {coin}: {e}")
    
    def calculate_position_size(self, coin, price):
        """Calculate position size for a coin"""
        try:
            # Get symbol info
            exchange_info = self.client.futures_exchange_info()
            symbol_info = None
            
            for symbol in exchange_info['symbols']:
                if symbol['symbol'] == coin:
                    symbol_info = symbol
                    break
            
            if not symbol_info:
                return None
            
            # Extract filters
            step_size = None
            min_qty = None
            
            for filter in symbol_info['filters']:
                if filter['filterType'] == 'LOT_SIZE':
                    step_size = float(filter['stepSize'])
                    min_qty = float(filter['minQty'])
            
            # Calculate quantity
            raw_qty = POSITION_VALUE / price
            
            # Round to step size
            step_decimal = Decimal(str(step_size))
            qty_decimal = Decimal(str(raw_qty))
            final_qty = float(qty_decimal.quantize(step_decimal, rounding=ROUND_DOWN))
            
            # Check minimum
            if final_qty < min_qty:
                logger.warning(f"⚠️ {coin} position too small: {final_qty} < {min_qty}")
                return None
            
            return final_qty
            
        except Exception as e:
            logger.error(f"❌ Error calculating position size for {coin}: {e}")
            return None
    
    def format_price(self, coin, price):
        """Format price according to exchange tick size"""
        try:
            exchange_info = self.client.futures_exchange_info()
            for symbol in exchange_info['symbols']:
                if symbol['symbol'] == coin:
                    for filter in symbol['filters']:
                        if filter['filterType'] == 'PRICE_FILTER':
                            tick_size = float(filter['tickSize'])
                            tick_decimal = Decimal(str(tick_size))
                            price_decimal = Decimal(str(price))
                            formatted_price = float(price_decimal.quantize(tick_decimal, rounding=ROUND_DOWN))
                            return formatted_price
            return price
        except:
            return price

    def resolve_order_lookup_params(self, coin, order_ref):
        """Build Binance lookup params from a stored order reference."""
        if not order_ref or order_ref == 'N/A':
            return None

        if isinstance(order_ref, str):
            if order_ref.startswith('paper:') or 'PAPER' in order_ref:
                return None

            if order_ref.startswith('oid:'):
                raw_order_id = order_ref.split(':', 1)[1]
                try:
                    order_id = int(raw_order_id)
                except ValueError:
                    order_id = raw_order_id
                return {'symbol': coin, 'orderId': order_id}

            if order_ref.startswith('aid:'):
                raw_algo_id = order_ref.split(':', 1)[1]
                try:
                    algo_id = int(raw_algo_id)
                except ValueError:
                    algo_id = raw_algo_id
                return {'symbol': coin, 'algoId': algo_id}

            if order_ref.startswith('cid:'):
                return {'symbol': coin, 'origClientOrderId': order_ref.split(':', 1)[1]}

            if order_ref.startswith('caid:'):
                return {'symbol': coin, 'clientAlgoId': order_ref.split(':', 1)[1]}

            if order_ref.isdigit():
                return {'symbol': coin, 'orderId': int(order_ref)}

            return {'symbol': coin, 'origClientOrderId': order_ref}

        if isinstance(order_ref, (int, float)):
            return {'symbol': coin, 'orderId': int(order_ref)}

        return None
    
    def close_position_market(self, coin, reason="SIGNAL_REVERSAL"):
        """
        NEW METHOD: Close position with market order
        This is called when we need to reverse a position
        """
        try:
            position = self.position_tracker.get_position(coin)
            if not position:
                return False
                
            # Cancel existing TP/SL orders first
            self.cancel_order_safe(coin, position.get('tp_order_id'))
            self.cancel_order_safe(coin, position.get('sl_order_id'))
            
            # Get current price for PnL calculation
            ticker = self.client.futures_symbol_ticker(symbol=coin)
            current_price = float(ticker['price'])
            
            if not PAPER_TRADING:
                # Place market order to close position
                quantity = position['quantity']
                if position['side'] == 'LONG':
                    # Close LONG by selling
                    self.client.futures_create_order(
                        symbol=coin,
                        side='SELL',
                        type='MARKET',
                        quantity=quantity
                    )
                else:
                    # Close SHORT by buying
                    self.client.futures_create_order(
                        symbol=coin,
                        side='BUY',
                        type='MARKET',
                        quantity=quantity
                    )
            
            # Handle exit accounting
            self.handle_exit(coin, reason, current_price)
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to close position for {coin}: {e}")
            return False
    
    def place_entry_with_tp_sl(self, coin, side, quantity, entry_price):
        """Place entry order with TP and SL orders"""
        try:
            manager = self.coin_managers[coin]
            params = manager.params['parameters']
            
            # Place market entry order
            if PAPER_TRADING:
                logger.info(f"📝 PAPER TRADE: {side} {quantity} {coin} @ ${entry_price:.2f}")
                entry_order_ref = f"paper:entry:{time.time()}"
                tp_order_ref = f"paper:tp:{time.time()}"
                sl_order_ref = f"paper:sl:{time.time()}"
            else:
                # Place real entry order
                if side == 'BUY':
                    entry_order = self.client.futures_create_order(
                        symbol=coin,
                        side='BUY',
                        type='MARKET',
                        quantity=quantity
                    )
                else:
                    entry_order = self.client.futures_create_order(
                        symbol=coin,
                        side='SELL',
                        type='MARKET',
                        quantity=quantity
                    )
                
                logger.info(f"✅ Entry order placed: {side} {quantity} {coin}")
                entry_order_ref = serialize_order_reference(entry_order)
                if not entry_order_ref:
                    logger.error(f"❌ Entry order response missing identifiers for {coin}: {entry_order}")
                    return None, None, None
                
                # Calculate TP and SL prices
                if side == 'BUY':  # LONG position
                    tp_price = entry_price * (1 + params['tp_percent'])
                    sl_price = entry_price * (1 - params['sl_percent'])
                    exit_side = 'SELL'
                else:  # SHORT position
                    tp_price = entry_price * (1 - params['tp_percent'])
                    sl_price = entry_price * (1 + params['sl_percent'])
                    exit_side = 'BUY'
                
                # Format prices according to tick size
                tp_price = self.format_price(coin, tp_price)
                sl_price = self.format_price(coin, sl_price)
                
                logger.info(f"📊 Setting TP @ ${tp_price:.2f} (+{params['tp_percent']*100:.2f}%)")
                logger.info(f"📊 Setting SL @ ${sl_price:.2f} (-{params['sl_percent']*100:.2f}%)")
                
                # Place TP order
                try:
                    tp_order = self.client.futures_create_order(
                        symbol=coin,
                        side=exit_side,
                        type='TAKE_PROFIT_MARKET',
                        stopPrice=tp_price,
                        quantity=quantity,
                        reduceOnly=True,
                        workingType='MARK_PRICE'
                    )
                    tp_order_ref = serialize_order_reference(tp_order)
                    if tp_order_ref:
                        logger.info(f"✅ TP order placed: {format_order_reference(tp_order_ref)}")
                    else:
                        logger.error(f"❌ TP order response missing identifiers for {coin}: {tp_order}")
                except Exception as e:
                    logger.error(f"❌ Failed to place TP order: {e}")
                    tp_order_ref = None
                
                # Place SL order
                try:
                    sl_order = self.client.futures_create_order(
                        symbol=coin,
                        side=exit_side,
                        type='STOP_MARKET',
                        stopPrice=sl_price,
                        quantity=quantity,
                        reduceOnly=True,
                        workingType='MARK_PRICE'
                    )
                    sl_order_ref = serialize_order_reference(sl_order)
                    if sl_order_ref:
                        logger.info(f"✅ SL order placed: {format_order_reference(sl_order_ref)}")
                    else:
                        logger.error(f"❌ SL order response missing identifiers for {coin}: {sl_order}")
                except Exception as e:
                    logger.error(f"❌ Failed to place SL order: {e}")
                    sl_order_ref = None

                notifier.send_trade_alert(coin, side, entry_price)
                # Discord disabled for now
                # if DISCORD_AVAILABLE:
                #     discord.send_trade_alert(coin, side, entry_price,tp_price,sl_price)
            
            return entry_order_ref, tp_order_ref, sl_order_ref
            
        except Exception as e:
            logger.error(f"❌ Failed to place orders for {coin}: {e}")
            return None, None, None
    
    def cancel_order_safe(self, coin, order_id):
        """Safely cancel an order"""
        lookup_params = self.resolve_order_lookup_params(coin, order_id)
        if not lookup_params:
            return True
        
        try:
            self.client.futures_cancel_order(**lookup_params)
            logger.info(f"✅ Cancelled order {format_order_reference(order_id)} for {coin}")
            return True
        except BinanceAPIException as e:
            if 'Unknown order' in str(e) or 'ORDER_DOES_NOT_EXIST' in str(e):
                # Order already filled or cancelled
                return True
            logger.error(f"❌ Failed to cancel order {format_order_reference(order_id)}: {e}")
            return False
    
    def check_order_status(self, coin, order_id):
        """Check if an order is filled"""
        if not order_id:
            return 'PAPER' if PAPER_TRADING else 'MISSING'

        if isinstance(order_id, str) and (order_id.startswith('paper:') or 'PAPER' in order_id):
            return 'PAPER'

        lookup_params = self.resolve_order_lookup_params(coin, order_id)
        if not lookup_params:
            return 'MISSING'
        
        try:
            order = self.client.futures_get_order(**lookup_params)
            return order['status']
        except:
            return 'UNKNOWN'
    
    def check_signal_on_candle_close(self, coin):
        """
        OPTIMIZATION-ALIGNED VERSION: Implements 1-candle delay like backtest
        - Executes pending signals from previous candle at MARKET PRICE
        - Calculates new signals using previous candle data  
        - Stores signals for execution on next candle close
        
        EXECUTION TIMING:
        - When candle closes, we execute pending signal at current market price
        - This market price = "next candle open" in backtesting terms
        - Then calculate new signal from just-closed candle for future execution
        """
        try:
            manager = self.coin_managers[coin]
            
            # Fetch fresh historical data
            entry_df = manager.indicator_calc.fetch_historical_data(
                self.client, 
                manager.entry_timeframe,
                limit=200
            )
            trend_df = manager.indicator_calc.fetch_historical_data(
                self.client,
                manager.trend_timeframe,
                limit=200
            )
            
            if entry_df is None or trend_df is None:
                return
            
            # Calculate indicators on fresh data
            entry_df = manager.indicator_calc.calculate_all_indicators(entry_df)
            trend_df = manager.indicator_calc.calculate_all_indicators(trend_df)
            
            # Get current position
            position = self.position_tracker.get_position(coin)
            
            # Skip if we already have a position (no reversal logic)
            if position:
                return
            
            # STEP 1: Execute pending signal if exists (from previous candle)
            if coin in self.pending_signals:
                pending = self.pending_signals[coin]
                signal = pending['signal']
                strength = pending['strength']
                filters = pending['filters']

                if not self.is_signal_allowed_by_spy(coin, signal, "pending entry"):
                    del self.pending_signals[coin]
                    return
                
                # Check balance and position limits
                if self.account_balance >= MARGIN_PER_TRADE and \
                   self.position_tracker.total_positions() < MAX_TOTAL_POSITIONS:
                    
                    # Get CURRENT MARKET PRICE for execution
                    # This is equivalent to "next candle open" in backtesting
                    ticker = self.client.futures_symbol_ticker(symbol=coin)
                    market_price = float(ticker['price'])
                    
                    logger.info(f"⏰ {coin} Executing PENDING signal: {signal} @ MARKET ${market_price:.2f} (Strength: {strength:.2f})")
                    
                    # Log active filters
                    active_filters = [f"{k}:{v['signal']:.2f}" for k, v in filters.items() if abs(v['signal']) > 0.1]
                    if active_filters:
                        logger.info(f"   Active filters: {', '.join(active_filters[:5])}")
                    
                    self.enter_new_position(coin, signal, market_price)
                
                # Clear pending signal after execution attempt
                del self.pending_signals[coin]
                return  # Don't calculate new signal in same iteration
            
            # STEP 2: No position and no pending - check for new entry signal
            # Use DELAYED signal calculation (previous candle data)
            signal, strength, filters = manager.indicator_calc.get_entry_signal_delayed(entry_df, trend_df)
            
            if signal:
                if not self.is_signal_allowed_by_spy(coin, signal, "new signal"):
                    return

                # Store as pending signal for execution on NEXT candle close
                self.pending_signals[coin] = {
                    'signal': signal,
                    'strength': strength,
                    'filters': filters
                }
                logger.info(f"📌 {coin} NEW signal detected: {signal} (Strength: {strength:.2f}) - PENDING for next candle")
                
                # Log active filters
                active_filters = [f"{k}:{v['signal']:.2f}" for k, v in filters.items() if abs(v['signal']) > 0.1]
                if active_filters:
                    logger.info(f"   Pending filters: {', '.join(active_filters[:5])}")
                    
        except Exception as e:
            logger.error(f"Error checking signal for {coin}: {e}")
    
    def enter_new_position(self, coin, signal, entry_price):
        """
        OPTIMIZATION-ALIGNED: Enter position at specified price
        Note: entry_price should be current candle's OPEN (not close) for realistic execution
        """
        try:
            manager = self.coin_managers[coin]
            
            logger.info(f"🎯 {coin} EXECUTING Entry: {signal} @ ${entry_price:.2f}")
            
            # Calculate position size
            quantity = self.calculate_position_size(coin, entry_price)
            
            if quantity:
                # Place entry with TP/SL orders
                side = 'BUY' if signal == 'LONG' else 'SELL'
                entry_order_ref, tp_order_ref, sl_order_ref = self.place_entry_with_tp_sl(
                    coin, side, quantity, entry_price
                )
                
                if entry_order_ref:
                    # Track position with all order IDs
                    self.position_tracker.add_position(coin, {
                        'side': signal,
                        'entry_price': entry_price,
                        'quantity': quantity,
                        'entry_time': datetime.now(),
                        'entry_order_id': entry_order_ref,
                        'tp_order_id': tp_order_ref,
                        'sl_order_id': sl_order_ref,
                        'tp_price': entry_price * (1 + manager.params['parameters']['tp_percent']) if signal == 'LONG' else entry_price * (1 - manager.params['parameters']['tp_percent']),
                        'sl_price': entry_price * (1 - manager.params['parameters']['sl_percent']) if signal == 'LONG' else entry_price * (1 + manager.params['parameters']['sl_percent'])
                             
                        
                    })
                                        # COPY-TRADE ADDON (ENTRY) - ADD ONLY
                    try:
                        copy_side = 'BUY' if signal == 'LONG' else 'SELL'
                        copy_results = copy_open_to_followers(
                            symbol=coin,
                            side=copy_side,
                            quantity=quantity
                        )
                        ok_count = sum(1 for r in copy_results if r.get("ok"))
                        fail_count = len(copy_results) - ok_count
                        logger.info(f"📋 Copy ENTRY {coin}: success={ok_count}, failed={fail_count}")
                    except Exception as copy_err:
                        logger.error(f"❌ Copy ENTRY failed for {coin}: {copy_err}")

                    
        except Exception as e:
            logger.error(f"Error entering position for {coin}: {e}")
    
    def check_exit_conditions(self, coin):
        """Check if TP or SL orders have filled"""
        try:
            position = self.position_tracker.get_position(coin)
            if not position:
                return None
            
            manager = self.coin_managers[coin]
            
            # Check TP order status
            tp_status = self.check_order_status(coin, position.get('tp_order_id'))
            sl_status = self.check_order_status(coin, position.get('sl_order_id'))
            
            # If in paper trading mode, check prices manually
            if PAPER_TRADING:
                ticker = self.client.futures_symbol_ticker(symbol=coin)
                current_price = float(ticker['price'])
                
                if position['side'] == 'LONG':
                    if current_price >= position['tp_price']:
                        logger.info(f"📊 {coin} TP Hit (Paper): ${current_price:.2f}")
                        self.handle_exit(coin, 'TP', current_price)
                        return 'TP'
                    elif current_price <= position['sl_price']:
                        logger.info(f"📊 {coin} SL Hit (Paper): ${current_price:.2f}")
                        self.handle_exit(coin, 'SL', current_price)
                        return 'SL'
                else:  # SHORT
                    if current_price <= position['tp_price']:
                        logger.info(f"📊 {coin} TP Hit (Paper): ${current_price:.2f}")
                        self.handle_exit(coin, 'TP', current_price)
                        return 'TP'
                    elif current_price >= position['sl_price']:
                        logger.info(f"📊 {coin} SL Hit (Paper): ${current_price:.2f}")
                        self.handle_exit(coin, 'SL', current_price)
                        return 'SL'
            
            # Check if TP filled
            if tp_status == 'FILLED':
                logger.info(f"📊 {coin} TP Order Filled!")
                # Cancel SL order
                self.cancel_order_safe(coin, position.get('sl_order_id'))
                # Handle exit
                self.handle_exit(coin, 'TP', position['tp_price'])
                return 'TP'
            
            # Check if SL filled
            if sl_status == 'FILLED':
                logger.info(f"📊 {coin} SL Order Filled!")
                # Cancel TP order
                self.cancel_order_safe(coin, position.get('tp_order_id'))
                # Handle exit
                self.handle_exit(coin, 'SL', position['sl_price'])
                return 'SL'
            
            # Check if both orders somehow disappeared (wait longer on testnet)
            missing_statuses = {'UNKNOWN', 'MISSING'}
            if tp_status in missing_statuses and sl_status in missing_statuses and not PAPER_TRADING:
                # On testnet, give orders more time to appear (testnet is slower)
                position_age = (datetime.now() - position.get('entry_time', datetime.now())).total_seconds()
                if position_age < 30:  # Wait 30 seconds before panicking on testnet
                    return None  # Don't close yet, orders might still be syncing
                
                try:
                    # Try to get current price first
                    ticker = self.client.futures_symbol_ticker(symbol=coin)
                    current_price = float(ticker['price'])
                    
                    # Only proceed if we successfully got price
                    logger.warning(f"⚠️ Both orders missing for {coin} after 30s! Closing position manually")
                    
                    # Place market order to close
                    quantity = position['quantity']
                    if position['side'] == 'LONG':
                        self.client.futures_create_order(
                            symbol=coin,
                            side='SELL',
                            type='MARKET',
                            quantity=quantity
                        )
                    else:
                        self.client.futures_create_order(
                            symbol=coin,
                            side='BUY',
                            type='MARKET',
                            quantity=quantity
                        )
                    
                    self.handle_exit(coin, 'MANUAL', current_price)
                    return 'MANUAL'
                    
                except Exception as e:
                    logger.error(f"Cannot verify order status for {coin} - connection issue: {e}")
                    return None
            
            return None
            
        except Exception as e:
            logger.error(f"Error checking exit for {coin}: {e}")
            return None
    
    def handle_exit(self, coin, exit_type, exit_price):
        """Handle position exit and calculate PnL"""
        try:
            position = self.position_tracker.get_position(coin)
            if not position:
                return
            
            # Calculate PnL
            entry_price = position['entry_price']
            if position['side'] == 'LONG':
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100
            else:
                pnl_pct = ((entry_price - exit_price) / entry_price) * 100
            
            pnl_value = (pnl_pct / 100) * MARGIN_PER_TRADE
            
            logger.info(f"💰 {coin} Closed: {exit_type} @ ${exit_price:.2f} | PnL: {pnl_pct:.2f}% (${pnl_value:.2f})")

            # Send notifications
            notifier.send_exit_alert(coin, exit_type, pnl_pct, pnl_value)
            # Discord disabled for now
            # if DISCORD_AVAILABLE:
            #     discord.send_exit_alert(coin, exit_type, pnl_pct, pnl_value)
            
            # Prepare trade data
            trade_data = {
                'coin': coin,
                'side': position['side'],
                'entry_price': entry_price,
                'exit_price': exit_price,
                'pnl_pct': pnl_pct,
                'pnl_value': pnl_value,
                'exit_type': exit_type,
                'position_size': POSITION_VALUE,
                'leverage': LEVERAGE
            }
            
            # Save trade to database
            save_trade_to_db(trade_data)
            
            # Record in enhanced stats
            enhanced_stats.record_trade(trade_data)
            
            # Send detailed stats notification
            detailed_msg = enhanced_stats.format_trade_close_message(trade_data)
            notifier.send_message(detailed_msg)

                        # COPY-TRADE ADDON (EXIT) - ADD ONLY
            try:
                close_side = 'SELL' if position['side'] == 'LONG' else 'BUY'
                copy_results = copy_close_to_followers(
                    symbol=coin,
                    side=close_side,
                    quantity=position['quantity']
                )
                ok_count = sum(1 for r in copy_results if r.get("ok"))
                fail_count = len(copy_results) - ok_count
                logger.info(f"📋 Copy EXIT {coin}: success={ok_count}, failed={fail_count}")
            except Exception as copy_err:
                logger.error(f"❌ Copy EXIT failed for {coin}: {copy_err}")

            
            # Remove position
            self.position_tracker.remove_position(coin)
            
            # Update balance
            self.check_account()
            
        except Exception as e:
            logger.error(f"Error handling exit for {coin}: {e}")
    
    def start_polling(self):
        """Start polling loop instead of WebSocket"""
        logger.info("🌐 Starting polling mode (checking every 30 seconds)...")
        logger.info("📊 NORMAL MODE - Exits on TP/SL only (matching optimization)")
        
        self.running = True
        
        # Track last candle times to detect closes
        self.last_candles = {}
        
        # Initialize last candle times
        for coin, manager in self.coin_managers.items():
            self.last_candles[coin] = {
                manager.entry_timeframe: None,
                manager.trend_timeframe: None
            }
        
        # Start the polling loop in a separate thread
        polling_thread = threading.Thread(target=self.polling_loop)
        polling_thread.daemon = True
        polling_thread.start()
    
    def polling_loop(self):
        """Poll for candle closes every 30 seconds"""
        while self.running:
            try:
                for coin, manager in self.coin_managers.items():
                    # Check entry timeframe
                    self.check_candle_close(coin, manager.entry_timeframe)
                    
                    # Check trend timeframe
                    self.check_candle_close(coin, manager.trend_timeframe)
                
                # Wait 30 seconds before next check
                time.sleep(30)
                
            except Exception as e:
                logger.error(f"Error in polling loop: {e}")
                time.sleep(30)
    
    def check_candle_close(self, coin, timeframe):
        """
        MODIFIED METHOD: Check if a candle has closed
        Now checks signals on EVERY entry timeframe candle close
        """
        try:
            manager = self.coin_managers[coin]
            
            # Fetch latest candle
            klines = self.client.futures_klines(
                symbol=coin,
                interval=timeframe,
                limit=2  # Get last 2 candles
            )
            
            if not klines:
                return
            
            # Get the last complete candle (not the current forming one)
            last_complete = klines[-2]  # Second to last is complete
            candle_time = last_complete[0]  # Timestamp
            close_price = float(last_complete[4])  # Close price
            
            # Check if this is a new candle close
            if self.last_candles[coin][timeframe] != candle_time:
                # New candle closed!
                self.last_candles[coin][timeframe] = candle_time
                
                logger.info(f"📊 {coin} {timeframe} candle closed at ${close_price:.2f}")

                # Optional: Send to Telegram (you might want to comment this out to reduce spam)
                notifier.send_message(f"📊 {coin} {timeframe} closed at ${close_price:.2f}")
                
                # Check signals on entry candle close
                # Not just when no position exists
                if timeframe == manager.entry_timeframe:
                    self.check_signal_on_candle_close(coin)
                    
        except Exception as e:
            logger.error(f"Error checking candle close for {coin} {timeframe}: {e}")
    
    def trading_loop(self):
        """Main trading loop - checks if TP/SL orders filled"""
        logger.info("🚀 Starting trading loop...")
        thresholds = ", ".join([f"{coin.replace('USDC','')}={manager.params['parameters']['entry_threshold']:.3f}" 
                               for coin, manager in self.coin_managers.items()])
        logger.info(f"   Entry thresholds: {thresholds}")
        logger.info("⏳ Polling for candle closes every 30 seconds...")
        logger.info("📊 TP/SL orders will be placed on Binance for instant execution")
        logger.info("📊 Positions exit on TP/SL only (no reversals)")
        
        while self.running:
            try:
                # Daily cleanup and stats check
                current_date = datetime.now(timezone.utc).date()
                if current_date > self.last_cleanup:
                    cleanup_old_trades(10)  # Keep only last 10 days
                    self.last_cleanup = current_date
                    logger.info(f"✅ Daily cleanup completed for {current_date} UTC")
                    
                    # Send daily summary to Telegram
                    daily_summary = enhanced_stats.format_daily_summary()
                    notifier.send_message(daily_summary)
                    logger.info("📊 Daily summary sent to Telegram (UTC midnight)")
                    
                    # Discord disabled for now
                    # if DISCORD_AVAILABLE:
                    #     summary = stats_tracker.format_daily_discord_summary()
                    #     if summary:
                    #         discord.send_message(summary)
                
                # Check each coin for order fills
                for coin in ACTIVE_COINS:
                    if coin not in self.coin_managers:
                        continue
                    
                    # Only check exits if position exists
                    if self.position_tracker.has_position(coin):
                        exit_signal = self.check_exit_conditions(coin)
                        if exit_signal:
                            logger.info(f"✅ {coin} position closed: {exit_signal}")
                
                # Sleep before next iteration
                time.sleep(5)  # Check order status every 5 seconds
                
            except Exception as e:
                logger.error(f"❌ Error in trading loop: {e}")
                time.sleep(5)
    
    def cleanup_open_orders(self):
        """Cancel all open TP/SL orders on shutdown"""
        logger.info("🧹 Cleaning up open orders...")
        
        for coin in ACTIVE_COINS:
            position = self.position_tracker.get_position(coin)
            if position:
                # Cancel TP order
                if position.get('tp_order_id'):
                    self.cancel_order_safe(coin, position['tp_order_id'])
                
                # Cancel SL order
                if position.get('sl_order_id'):
                    self.cancel_order_safe(coin, position['sl_order_id'])
                
                logger.warning(f"⚠️ {coin} position still open at shutdown - orders cancelled")
    
    def start(self):
        """Start the bot"""
        logger.info("🚀 Starting bot...")
        environment_label = "TESTNET" if USE_TESTNET else "MAINNET"
        execution_label = (
            "PAPER TRADING"
            if PAPER_TRADING
            else ("LIVE TESTNET ORDERS" if USE_TESTNET else "LIVE MAINNET ORDERS")
        )
        logger.info(f"   Environment: {environment_label}")
        logger.info(f"   Execution: {execution_label}")
        logger.info(f"   Strategy: NORMAL TP/SL EXITS - Matching optimization backtest")
        logger.info(f"   Coins: {', '.join(ACTIVE_COINS)}")
        logger.info(f"   Balance: ${self.account_balance:.2f} USDT")
        logger.info(f"   TP/SL: Orders placed on Binance for instant execution")
        logger.info(f"   Log Rotation: Max 5 files of 10MB each")
        logger.info(f"   Database Cleanup: Keeping last 30 days of trades")
        
        self.running = True
        
        # Start polling instead of WebSocket
        self.start_polling()
        
        # Wait for initial data
        logger.info("⏳ Waiting for initial data (10 seconds)...")
        time.sleep(10)
        
        # Start trading loop
        self.trading_loop()
    
    def stop(self):
        """Stop the bot"""
        logger.info("🛑 Stopping bot...")
        self.running = False
        
        # Clean up open orders
        # self.cleanup_open_orders()
        
        logger.info("✅ Bot stopped")

# ========================================
# SIGNAL HANDLERS
# ========================================
def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully"""
    logger.info("\n⚠️ Shutdown signal received...")
    if bot:
        bot.stop()
    sys.exit(0)

# ========================================
# MAIN ENTRY POINT
# ========================================
if __name__ == "__main__":
    # Set up signal handler
    signal.signal(signal.SIGINT, signal_handler)
    
    # Create and start bot
    bot = TradingBot()
    
    try:
        bot.start()
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        bot.stop()
