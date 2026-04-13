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
import re
from logging.handlers import RotatingFileHandler
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, Tuple
import threading
import signal
import sys
import pandas as pd
import platform
import asyncio
from pathlib import Path

from binance import ThreadedWebsocketManager
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
handlers = []

if LOG_TO_FILE:
    rotating_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5,  # Keep 5 old files
        encoding='utf-8'
    )
    rotating_handler.setFormatter(log_formatter)
    handlers.append(rotating_handler)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
handlers.append(console_handler)

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    handlers=handlers
)

logger = logging.getLogger(__name__)
RATE_LIMIT_BAN_UNTIL_RE = re.compile(r"banned until (\d+)")


def is_rate_limit_error(exc):
    message = str(exc)
    return "code=-1003" in message or "Too many requests" in message or "banned until" in message


def parse_rate_limit_retry_at(exc):
    match = RATE_LIMIT_BAN_UNTIL_RE.search(str(exc))
    if not match:
        return None
    return (int(match.group(1)) / 1000.0) + 1


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


def extract_order_status(order_payload):
    """Read status from either standard futures orders or algo/conditional futures orders."""
    if not order_payload:
        return None
    return (
        order_payload.get('status')
        or order_payload.get('algoStatus')
        or order_payload.get('X')
        or order_payload.get('x')
    )


def extract_order_type(order_payload):
    """Read order type from either standard or algo/conditional futures order payloads."""
    if not order_payload:
        return None
    return (
        order_payload.get('type')
        or order_payload.get('origType')
        or order_payload.get('orderType')
    )


def extract_order_trigger_price(order_payload):
    """Read trigger/stop price from either standard or algo/conditional futures order payloads."""
    if not order_payload:
        return None
    raw_value = (
        order_payload.get('stopPrice')
        or order_payload.get('triggerPrice')
        or order_payload.get('price')
    )
    if raw_value in (None, '', '0', '0.0', '0.000', '0.00000'):
        return None
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return None

# ========================================
# DATABASE CLEANUP FUNCTION (NEW)
# ========================================
def cleanup_old_trades(days_to_keep=30):
    """Remove trades older than specified days from database"""
    if not DATABASE_ENABLED:
        return
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
    if not DATABASE_ENABLED:
        return
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

    def restore_position(self, coin, data):
        """Restore an already-open exchange position into local bot state."""
        with self.lock:
            self.positions[coin] = data

        protection_status = "protected" if data.get('tp_order_id') and data.get('sl_order_id') else "missing-protection"
        logger.warning(
            f"♻️ Restored Binance position: {coin} - {data['side']} @ ${data['entry_price']:.2f} "
            f"| qty={data['quantity']:.6f} | protection={protection_status}"
        )
        logger.info(f"   Restored TP Order ID: {format_order_reference(data.get('tp_order_id'))}")
        logger.info(f"   Restored SL Order ID: {format_order_reference(data.get('sl_order_id'))}")
    
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

    def get_all_positions(self):
        """Return a shallow copy of tracked open positions."""
        with self.lock:
            return dict(self.positions)

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
        self.data_lock = threading.Lock()
        
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
        self.ws_manager = None
        self.ws_streams = []
        self.rest_backoff_until = 0.0
        self.symbol_exchange_rules = {}
        self.user_order_cache = {}
        self.user_stream_name = None
        self.user_stream_started_at = 0.0
        self.user_stream_last_message_at = 0.0
        
        # OPTIMIZATION ALIGNMENT: Pending signals for delayed execution
        self.pending_signals = {}  # {coin: {'signal': 'LONG/SHORT', 'strength': float, 'filters': dict}}
        self.spy_regime_filter = SpyRegimeFilter(PROJECT_ROOT)
        self.active_spy_regime = None

        stats_tracker.update_position_tracker(self.position_tracker)

        
        # Schedule daily cleanup at midnight
        self.last_cleanup = datetime.now(timezone.utc).date()
        
        # Load coin configurations
        self.load_coins()
        self.load_symbol_exchange_rules()
        
        # Wait through startup-time Binance REST rate limits instead of exiting and
        # forcing the web supervisor to be manually restarted later.
        self.ensure_exchange_ready()
        self.restore_existing_exchange_positions()

        # Preload the daily SPY regime so entry gating uses a stable cached bias.
        self.log_active_spy_regime()
        self.maybe_handle_startup_spy_bias_alignment()

    def log_active_spy_regime(self):
        """Log the currently active cached SPY regime."""
        try:
            regime = self.spy_regime_filter.get_regime()
            self.active_spy_regime = {
                'regime': regime.get('regime'),
                'as_of_date': regime.get('as_of_date'),
                'predicting_for': regime.get('predicting_for'),
            }
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

    def position_conflicts_with_regime(self, position_side, regime_name):
        """Whether a tracked position is directionally against the active SPY regime."""
        if regime_name == 'LONG_ONLY':
            return position_side == 'SHORT'
        if regime_name == 'SHORT_ONLY':
            return position_side == 'LONG'
        return False

    def calculate_unrealized_position_pnl(self, coin, position):
        """Calculate the latest unrealized PnL for one tracked position."""
        ticker = self.client.futures_symbol_ticker(symbol=coin)
        current_price = float(ticker['price'])
        entry_price = float(position['entry_price'])

        if position['side'] == 'LONG':
            pnl_pct = ((current_price - entry_price) / entry_price) * 100
        else:
            pnl_pct = ((entry_price - current_price) / entry_price) * 100

        pnl_value = (pnl_pct / 100) * MARGIN_PER_TRADE
        return current_price, pnl_pct, pnl_value

    def collect_active_basket_snapshots(self, active_positions):
        """Capture a consistent unrealized PnL snapshot for all active positions."""
        basket_snapshots = []
        basket_pnl_value = 0.0

        for coin, position in active_positions.items():
            current_price, pnl_pct, pnl_value = self.calculate_unrealized_position_pnl(coin, position)
            basket_snapshots.append({
                'coin': coin,
                'side': position['side'],
                'current_price': current_price,
                'pnl_pct': pnl_pct,
                'pnl_value': pnl_value,
            })
            basket_pnl_value += pnl_value

        return basket_snapshots, basket_pnl_value

    def close_active_basket_for_spy_bias(self, basket_snapshots, basket_pnl_value, trigger_label):
        """Close the currently tracked basket because the active SPY bias says to flatten risk."""
        logger.warning(
            "🧭 %s: closing %s active position(s) because basket unrealized PnL is positive ($%.2f)",
            trigger_label,
            len(basket_snapshots),
            basket_pnl_value,
        )

        for snapshot in basket_snapshots:
            coin = snapshot['coin']
            if not self.position_tracker.has_position(coin):
                continue

            closed = self.close_position_market(coin, reason="BIAS_CHANGE")
            if closed:
                logger.info(
                    "🧭 %s closed by SPY bias change @ $%.2f | unrealized snapshot: %.2f%% ($%.2f)",
                    coin,
                    snapshot['current_price'],
                    snapshot['pnl_pct'],
                    snapshot['pnl_value'],
                )

    def maybe_handle_startup_spy_bias_alignment(self):
        """Run a one-time startup alignment check for restored positions against the active SPY regime."""
        regime = self.active_spy_regime
        if not regime:
            return

        active_positions = self.position_tracker.get_all_positions()
        if not active_positions:
            return

        conflicting_positions = [
            coin for coin, position in active_positions.items()
            if self.position_conflicts_with_regime(position['side'], regime.get('regime'))
        ]
        if not conflicting_positions:
            return

        try:
            basket_snapshots, basket_pnl_value = self.collect_active_basket_snapshots(active_positions)
        except Exception as exc:
            logger.error(f"❌ Failed to evaluate startup SPY bias alignment: {exc}")
            return

        logger.info(
            "🧭 Startup SPY alignment check: regime=%s | conflicting positions=%s | active basket unrealized PnL=$%.2f",
            regime.get('regime', 'unknown'),
            ", ".join(conflicting_positions),
            basket_pnl_value,
        )

        if basket_pnl_value <= 0:
            logger.info("🧭 Startup SPY alignment close skipped because active basket unrealized PnL is not positive")
            return

        self.close_active_basket_for_spy_bias(
            basket_snapshots,
            basket_pnl_value,
            "Startup SPY bias alignment triggered",
        )

    def maybe_handle_spy_bias_change(self):
        """Close the current basket once when the daily SPY bias flips and basket PnL is positive."""
        try:
            regime = self.spy_regime_filter.get_regime()
        except Exception as exc:
            logger.error(f"❌ Failed to refresh SPY regime during trading loop: {exc}")
            return

        current_regime = {
            'regime': regime.get('regime'),
            'as_of_date': regime.get('as_of_date'),
            'predicting_for': regime.get('predicting_for'),
        }

        previous_regime = self.active_spy_regime
        if previous_regime is None:
            self.active_spy_regime = current_regime
            return

        if current_regime == previous_regime:
            return

        logger.info(
            "🧭 SPY regime updated: %s (%s -> %s) => %s (%s -> %s)",
            previous_regime.get('regime', 'unknown'),
            previous_regime.get('as_of_date', 'unknown'),
            previous_regime.get('predicting_for', 'unknown'),
            current_regime.get('regime', 'unknown'),
            current_regime.get('as_of_date', 'unknown'),
            current_regime.get('predicting_for', 'unknown'),
        )

        self.active_spy_regime = current_regime

        if previous_regime.get('regime') == current_regime.get('regime'):
            return

        active_positions = self.position_tracker.get_all_positions()
        if not active_positions:
            logger.info("🧭 SPY bias changed but there are no active positions to review")
            return

        for coin, position in active_positions.items():
            if self.position_conflicts_with_regime(position['side'], current_regime.get('regime')):
                break
        else:
            logger.info("🧭 SPY bias changed but active positions already align with the new regime")
            return

        try:
            basket_snapshots, basket_pnl_value = self.collect_active_basket_snapshots(active_positions)
        except Exception as exc:
            logger.error(f"❌ Failed to evaluate active basket for SPY bias change: {exc}")
            return

        logger.info(
            "🧭 SPY bias flip detected: %s -> %s | active basket unrealized PnL: $%.2f across %s position(s)",
            previous_regime.get('regime', 'unknown'),
            current_regime.get('regime', 'unknown'),
            basket_pnl_value,
            len(basket_snapshots),
        )

        if basket_pnl_value <= 0:
            logger.info("🧭 Bias-change close skipped because active basket unrealized PnL is not positive")
            return

        self.close_active_basket_for_spy_bias(
            basket_snapshots,
            basket_pnl_value,
            "Bias-change close triggered",
        )

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

    def note_rate_limit(self, source, exc, fallback_seconds=30):
        """Record a temporary Binance REST backoff window after a rate-limit error."""
        retry_at = parse_rate_limit_retry_at(exc) or (time.time() + fallback_seconds)
        self.rest_backoff_until = max(self.rest_backoff_until, retry_at)
        retry_dt = datetime.fromtimestamp(self.rest_backoff_until, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        logger.warning(f"⚠️ Binance REST backoff after {source} until {retry_dt}: {exc}")

    def rest_backoff_active(self):
        """Whether Binance REST calls should currently be skipped."""
        return time.time() < self.rest_backoff_until

    def wait_for_rest_backoff(self, context):
        """Sleep until the current Binance REST ban/backoff window expires."""
        if not self.rest_backoff_active():
            return
        wait_seconds = max(1.0, self.rest_backoff_until - time.time())
        logger.warning(
            f"⚠️ Waiting {wait_seconds:.1f}s before {context} because Binance REST is rate-limited"
        )
        time.sleep(wait_seconds)

    def ensure_exchange_ready(self):
        """Block startup until account access and one-time exchange setup can succeed."""
        while True:
            self.wait_for_rest_backoff("checking Binance account")
            if self.check_account(fatal=False):
                break
            if self.rest_backoff_active():
                continue
            logger.warning("⚠️ Binance account check failed during startup. Retrying in 10.0s")
            time.sleep(10)

        while True:
            self.wait_for_rest_backoff("setting leverage and margin")
            if self.setup_trading_params():
                return
            if self.rest_backoff_active():
                continue
            logger.warning("⚠️ Trading parameter setup failed during startup. Retrying in 10.0s")
            time.sleep(10)

    def load_symbol_exchange_rules(self):
        """Cache Binance symbol filters once so order formatting is exact and REST-light."""
        try:
            exchange_info = self.client.futures_exchange_info()
            rules = {}

            for symbol in exchange_info.get('symbols', []):
                coin = symbol.get('symbol')
                if coin not in ACTIVE_COINS:
                    continue

                filters = {item['filterType']: item for item in symbol.get('filters', [])}
                lot_filter = filters.get('LOT_SIZE', {})
                market_lot_filter = filters.get('MARKET_LOT_SIZE', lot_filter)
                price_filter = filters.get('PRICE_FILTER', {})

                qty_step_str = market_lot_filter.get('stepSize') or lot_filter.get('stepSize') or '1'
                min_qty_str = market_lot_filter.get('minQty') or lot_filter.get('minQty') or '0'
                tick_size_str = price_filter.get('tickSize') or '0.01'

                rules[coin] = {
                    'qty_step_str': qty_step_str,
                    'qty_step': Decimal(qty_step_str),
                    'min_qty': Decimal(min_qty_str),
                    'tick_size_str': tick_size_str,
                    'tick_size': Decimal(tick_size_str),
                }

            self.symbol_exchange_rules = rules
            logger.info(f"✅ Cached exchange rules for {len(rules)} active symbols")

        except Exception as exc:
            if is_rate_limit_error(exc):
                self.note_rate_limit("exchange info", exc, fallback_seconds=60)
            logger.error(f"❌ Failed to cache exchange rules: {exc}")

    def get_symbol_exchange_rules(self, coin):
        """Return cached exchange rules for a symbol."""
        rules = self.symbol_exchange_rules.get(coin)
        if rules is None:
            self.load_symbol_exchange_rules()
            rules = self.symbol_exchange_rules.get(coin)
        return rules

    def floor_to_step(self, value, step):
        """Floor a Decimal to the nearest valid exchange step."""
        if step <= 0:
            return value
        return (value // step) * step

    def format_exchange_decimal(self, value):
        """Convert Decimal to a non-scientific string accepted by Binance."""
        return format(value.normalize(), 'f') if value != 0 else '0'

    def normalize_quantity(self, coin, quantity):
        """Floor quantity to the exact MARKET_LOT_SIZE / LOT_SIZE step."""
        rules = self.get_symbol_exchange_rules(coin)
        if not rules:
            return None

        quantity_decimal = Decimal(str(quantity))
        normalized = self.floor_to_step(quantity_decimal, rules['qty_step'])
        return normalized if normalized > 0 else Decimal('0')

    def format_quantity(self, coin, quantity):
        """Return an exchange-safe quantity string."""
        normalized = self.normalize_quantity(coin, quantity)
        if normalized is None:
            return None
        return self.format_exchange_decimal(normalized)

    def normalize_price(self, coin, price):
        """Floor price to the exact PRICE_FILTER tick size."""
        rules = self.get_symbol_exchange_rules(coin)
        if not rules:
            return Decimal(str(price))

        price_decimal = Decimal(str(price))
        normalized = self.floor_to_step(price_decimal, rules['tick_size'])
        return normalized if normalized > 0 else rules['tick_size']

    def format_price_param(self, coin, price):
        """Return an exchange-safe stop/limit price string."""
        return self.format_exchange_decimal(self.normalize_price(coin, price))

    def seed_order_cache(self, symbol, order_response):
        """Prime local order-status cache from create-order responses before websocket confirms them."""
        order_ref = serialize_order_reference(order_response)
        if not order_ref:
            return None

        cache_payload = {
            'status': extract_order_status(order_response) or 'NEW',
            'symbol': symbol,
            'updated_at': time.time(),
            'source': 'create_order',
        }
        self.user_order_cache[order_ref] = cache_payload

        client_order_id = order_response.get('clientOrderId')
        if client_order_id:
            self.user_order_cache[f"cid:{client_order_id}"] = cache_payload

        return order_ref

    def get_cached_order_status(self, order_ref):
        """Return locally cached order status from websocket or create-order responses."""
        candidates = []
        if isinstance(order_ref, str):
            candidates.append(order_ref)
            if order_ref.isdigit():
                candidates.append(f"oid:{order_ref}")
        elif isinstance(order_ref, (int, float)):
            candidates.append(f"oid:{int(order_ref)}")

        for candidate in candidates:
            cached = self.user_order_cache.get(candidate)
            if cached:
                return cached.get('status')
        return None

    def user_stream_healthy(self):
        """Whether the futures user-data stream is recent enough to trust over REST."""
        if not self.user_stream_name:
            return False

        now = time.time()
        if self.user_stream_last_message_at:
            return (now - self.user_stream_last_message_at) < 3600
        return (now - self.user_stream_started_at) < 300

    def load_initial_candle_buffers(self):
        """Fetch initial history once, then keep the buffers live via WebSocket candle closes."""
        logger.info("📚 Loading initial candle buffers for indicator calculations...")
        loaded_pairs = 0

        for coin, manager in self.coin_managers.items():
            buffers = {}
            for timeframe in {manager.entry_timeframe, manager.trend_timeframe}:
                df = None
                for attempt in range(1, 4):
                    if self.rest_backoff_active():
                        wait_seconds = max(1.0, self.rest_backoff_until - time.time())
                        logger.warning(
                            f"⚠️ Waiting {wait_seconds:.1f}s before loading {coin} {timeframe} history because Binance REST is rate-limited"
                        )
                        time.sleep(wait_seconds)

                    try:
                        df = manager.indicator_calc.fetch_historical_data(
                            self.client,
                            timeframe,
                            limit=CANDLES_REQUIRED,
                        )
                    except Exception as exc:
                        if is_rate_limit_error(exc):
                            self.note_rate_limit(f"initial history {coin} {timeframe}", exc, fallback_seconds=60)
                            wait_seconds = max(1.0, self.rest_backoff_until - time.time())
                            logger.warning(
                                f"⚠️ Failed to load {coin} {timeframe} history (attempt {attempt}/3). "
                                f"Waiting {wait_seconds:.1f}s for Binance REST backoff"
                            )
                            time.sleep(wait_seconds)
                            continue
                        raise

                    if df is not None and len(df) >= 2:
                        df = df.tail(CANDLES_REQUIRED).copy()
                        break

                    wait_seconds = max(API_DELAY, 0.5) * attempt
                    logger.warning(
                        f"⚠️ Failed to load {coin} {timeframe} history (attempt {attempt}/3). "
                        f"Retrying in {wait_seconds:.1f}s"
                    )
                    time.sleep(wait_seconds)

                if df is None or len(df) < 2:
                    raise RuntimeError(f"Unable to load initial candle history for {coin} {timeframe}")

                buffers[timeframe] = df
                loaded_pairs += 1
                time.sleep(API_DELAY)

            with manager.data_lock:
                manager.entry_df = buffers[manager.entry_timeframe].copy()
                manager.trend_df = buffers[manager.trend_timeframe].copy()

        logger.info(f"✅ Loaded initial candle buffers for {loaded_pairs} symbol/timeframe pairs")

    def upsert_closed_candle(self, df, kline):
        """Insert or replace a closed candle in an in-memory dataframe."""
        candle_time = pd.to_datetime(int(kline['t']), unit='ms')
        candle_row = pd.DataFrame(
            [{
                'open': float(kline['o']),
                'high': float(kline['h']),
                'low': float(kline['l']),
                'close': float(kline['c']),
                'volume': float(kline['v']),
            }],
            index=[candle_time],
        )

        if df is None or df.empty:
            updated = candle_row
        else:
            updated = pd.concat([df[df.index != candle_time], candle_row]).sort_index()

        max_rows = CANDLES_REQUIRED + 5
        if len(updated) > max_rows:
            updated = updated.iloc[-max_rows:]

        return updated

    def update_candle_buffers(self, coin, timeframe, kline):
        """Update cached indicator inputs from a closed WebSocket candle."""
        manager = self.coin_managers[coin]
        with manager.data_lock:
            if timeframe == manager.entry_timeframe:
                manager.entry_df = self.upsert_closed_candle(manager.entry_df, kline)
            if timeframe == manager.trend_timeframe:
                manager.trend_df = self.upsert_closed_candle(manager.trend_df, kline)
    
    def load_coins(self):
        """Load all coin configurations"""
        for coin in ACTIVE_COINS:
            if coin in PARAM_FILES:
                self.coin_managers[coin] = CoinManager(coin, PARAM_FILES[coin])
            else:
                logger.warning(f"⚠️ No parameters for {coin}, skipping")
    
    def check_account(self, fatal=True):
        """Check account balance and status"""
        try:
            balances = self.client.futures_account_balance()
            
            # Find USDT balance using the lighter balance endpoint.
            for asset in balances:
                if asset.get('asset') == 'USDT':
                    available_balance = asset.get('availableBalance') or asset.get('balance') or asset.get('crossWalletBalance')
                    self.account_balance = float(available_balance)
                    break
            
            logger.info(f"💰 Account Balance: ${self.account_balance:.2f} USDT")
            
            
            if self.account_balance < MIN_BALANCE_TO_TRADE:
                logger.error(f"❌ Insufficient balance! Need ${MIN_BALANCE_TO_TRADE} USDT")
                if fatal:
                    sys.exit(1)
                return False
            return True
                
        except Exception as e:
            if is_rate_limit_error(e):
                self.note_rate_limit("account balance", e, fallback_seconds=60)
            logger.error(f"❌ Failed to check account: {e}")
            if fatal:
                sys.exit(1)
            return False
    
    def setup_trading_params(self):
        """Set leverage and margin mode for all coins"""
        for coin in ACTIVE_COINS:
            if self.rest_backoff_active():
                return False
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
                if is_rate_limit_error(e):
                    self.note_rate_limit(f"setup trading params {coin}", e, fallback_seconds=60)
                    return False
                logger.error(f"❌ Failed to setup {coin}: {e}")
        return True
    
    def calculate_position_size(self, coin, price):
        """Calculate position size for a coin"""
        try:
            rules = self.get_symbol_exchange_rules(coin)
            if not rules:
                return None

            raw_qty = Decimal(str(POSITION_VALUE)) / Decimal(str(price))
            final_qty = self.normalize_quantity(coin, raw_qty)

            if final_qty is None or final_qty < rules['min_qty']:
                logger.warning(f"⚠️ {coin} position too small: {final_qty} < {rules['min_qty']}")
                return None
            
            return float(final_qty)
            
        except Exception as e:
            logger.error(f"❌ Error calculating position size for {coin}: {e}")
            return None
    
    def format_price(self, coin, price):
        """Format price according to exchange tick size"""
        try:
            return float(self.normalize_price(coin, price))
        except:
            return price

    def is_truthy_exchange_flag(self, value):
        """Interpret Binance boolean-ish order flags consistently."""
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"true", "1", "yes"}

    def derive_expected_exit_price(self, coin, side, exit_type, entry_price):
        """Fallback TP/SL price when an imported order does not expose stopPrice."""
        params = self.coin_managers[coin].params['parameters']
        if side == 'LONG':
            raw_price = (
                entry_price * (1 + params['tp_percent'])
                if exit_type == 'TP'
                else entry_price * (1 - params['sl_percent'])
            )
        else:
            raw_price = (
                entry_price * (1 - params['tp_percent'])
                if exit_type == 'TP'
                else entry_price * (1 + params['sl_percent'])
            )
        return self.format_price(coin, raw_price)

    def fetch_open_conditional_orders(self, coin):
        """Load currently open conditional orders for a symbol, respecting REST backoff."""
        while True:
            self.wait_for_rest_backoff(f"loading protective orders for {coin}")
            try:
                return self.client.futures_get_open_orders(symbol=coin, conditional=True)
            except Exception as exc:
                if is_rate_limit_error(exc):
                    self.note_rate_limit(f"startup protective-order reconciliation {coin}", exc, fallback_seconds=60)
                    continue
                logger.error(f"❌ Failed to load protective orders for {coin}: {exc}")
                return []

    def identify_protective_orders(
        self,
        coin: str,
        position_side: str,
        open_orders,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Pick the active TP and SL orders that belong to an already-open position."""
        exit_side = 'SELL' if position_side == 'LONG' else 'BUY'
        candidates = []

        for order in open_orders or []:
            order_side = str(order.get('side') or order.get('S') or '').upper()
            order_type = str(extract_order_type(order) or '').upper()
            if order_side != exit_side:
                continue
            if not (
                order_type.startswith('TAKE_PROFIT')
                or order_type.startswith('STOP')
            ):
                continue

            candidates.append(order)

        candidates.sort(
            key=lambda order: (
                int(order.get('updateTime') or order.get('time') or 0),
                int(order.get('orderId') or 0),
            ),
            reverse=True,
        )

        tp_order = None
        sl_order = None

        for order in candidates:
            order_type = str(extract_order_type(order) or '').upper()
            if order_type.startswith('TAKE_PROFIT') and tp_order is None:
                tp_order = order
            elif order_type.startswith('STOP') and sl_order is None:
                sl_order = order

            if tp_order and sl_order:
                break

        return tp_order, sl_order

    def restore_existing_exchange_positions(self):
        """Import live Binance positions into local state so restart cannot stack extra exposure."""
        while True:
            self.wait_for_rest_backoff("reconciling existing Binance positions")
            try:
                positions = self.client.futures_position_information()
                break
            except Exception as exc:
                if is_rate_limit_error(exc):
                    self.note_rate_limit("startup position reconciliation", exc, fallback_seconds=60)
                    continue
                logger.error(f"❌ Failed to load existing Binance positions: {exc}")
                return

        restored_count = 0
        missing_protection_count = 0

        for raw_position in positions:
            coin = raw_position.get('symbol')
            if coin not in ACTIVE_COINS:
                continue

            try:
                amount = float(raw_position.get('positionAmt', 0))
            except (TypeError, ValueError):
                amount = 0.0

            if amount == 0:
                continue

            side = 'LONG' if amount > 0 else 'SHORT'
            quantity = abs(amount)
            entry_price = float(raw_position.get('entryPrice') or 0)

            open_orders = self.fetch_open_conditional_orders(coin)
            tp_order, sl_order = self.identify_protective_orders(coin, side, open_orders)

            tp_order_ref = self.seed_order_cache(coin, tp_order) if tp_order else None
            sl_order_ref = self.seed_order_cache(coin, sl_order) if sl_order else None

            tp_stop_price = extract_order_trigger_price(tp_order) or 0.0
            sl_stop_price = extract_order_trigger_price(sl_order) or 0.0

            tp_price = tp_stop_price or self.derive_expected_exit_price(coin, side, 'TP', entry_price)
            sl_price = sl_stop_price or self.derive_expected_exit_price(coin, side, 'SL', entry_price)

            restored_data = {
                'side': side,
                'entry_price': entry_price,
                'quantity': quantity,
                'entry_time': datetime.now(),
                'entry_order_id': f"restored:{coin}:{int(time.time())}",
                'tp_order_id': tp_order_ref,
                'sl_order_id': sl_order_ref,
                'tp_price': tp_price,
                'sl_price': sl_price,
                'restored_from_exchange': True,
                'protection_reconciled': bool(tp_order_ref and sl_order_ref),
            }
            self.position_tracker.restore_position(coin, restored_data)
            restored_count += 1

            if not restored_data['protection_reconciled']:
                missing_protection_count += 1
                logger.warning(
                    f"⚠️ {coin} was restored without both TP/SL orders. "
                    "New entries for this symbol remain blocked until the position is resolved."
                )

        if restored_count == 0:
            logger.info("✅ No existing Binance positions to restore on startup")
            return

        logger.warning(f"♻️ Restored {restored_count} existing Binance position(s) on startup")
        if missing_protection_count:
            logger.warning(
                f"⚠️ {missing_protection_count} restored position(s) are missing TP/SL protection. "
                "The bot will keep those symbols blocked from new entries."
            )

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

    def order_matches_reference(self, order_ref, order_payload):
        """Check whether a Binance order payload matches a stored order reference."""
        if not order_ref or not order_payload:
            return False

        if isinstance(order_ref, str):
            if order_ref.startswith('oid:'):
                return str(order_payload.get('orderId')) == order_ref.split(':', 1)[1]
            if order_ref.startswith('aid:'):
                return str(order_payload.get('algoId')) == order_ref.split(':', 1)[1]
            if order_ref.startswith('cid:'):
                return str(order_payload.get('clientOrderId')) == order_ref.split(':', 1)[1]
            if order_ref.startswith('caid:'):
                return str(order_payload.get('clientAlgoId')) == order_ref.split(':', 1)[1]
            if order_ref.isdigit():
                return str(order_payload.get('orderId')) == order_ref

        return False

    def lookup_conditional_order_status(self, coin, order_ref):
        """Fallback lookup for TP/SL conditional orders when direct query does not find them."""
        try:
            open_orders = self.client.futures_get_open_orders(symbol=coin, conditional=True)
            for order in open_orders:
                if self.order_matches_reference(order_ref, order):
                    return extract_order_status(order) or 'NEW'
        except Exception as exc:
            if is_rate_limit_error(exc):
                self.note_rate_limit(f"conditional open orders {coin}", exc)
                return 'RATE_LIMITED'

        try:
            all_orders = self.client.futures_get_all_orders(symbol=coin, conditional=True, limit=20)
            for order in reversed(all_orders):
                if self.order_matches_reference(order_ref, order):
                    return extract_order_status(order) or 'UNKNOWN'
        except Exception as exc:
            if is_rate_limit_error(exc):
                self.note_rate_limit(f"conditional all orders {coin}", exc)
                return 'RATE_LIMITED'

        return 'UNKNOWN'
    
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
            self.cancel_order_safe(coin, position.get('tp_order_id'), conditional=True)
            self.cancel_order_safe(coin, position.get('sl_order_id'), conditional=True)
            
            # Get current price for PnL calculation
            ticker = self.client.futures_symbol_ticker(symbol=coin)
            current_price = float(ticker['price'])
            
            if not PAPER_TRADING:
                # Place market order to close position
                quantity = position['quantity']
                formatted_quantity = self.format_quantity(coin, quantity)
                if not formatted_quantity:
                    logger.error(f"❌ Failed to format close quantity for {coin}: {quantity}")
                    return False
                if position['side'] == 'LONG':
                    # Close LONG by selling
                    self.client.futures_create_order(
                        symbol=coin,
                        side='SELL',
                        type='MARKET',
                        quantity=formatted_quantity
                    )
                else:
                    # Close SHORT by buying
                    self.client.futures_create_order(
                        symbol=coin,
                        side='BUY',
                        type='MARKET',
                        quantity=formatted_quantity
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
                formatted_quantity = self.format_quantity(coin, quantity)
                if not formatted_quantity:
                    logger.error(f"❌ Invalid formatted quantity for {coin}: {quantity}")
                    return None, None, None

                # Place real entry order
                if side == 'BUY':
                    entry_order = self.client.futures_create_order(
                        symbol=coin,
                        side='BUY',
                        type='MARKET',
                        quantity=formatted_quantity
                    )
                else:
                    entry_order = self.client.futures_create_order(
                        symbol=coin,
                        side='SELL',
                        type='MARKET',
                        quantity=formatted_quantity
                    )
                
                logger.info(f"✅ Entry order placed: {side} {quantity} {coin}")
                entry_order_ref = self.seed_order_cache(coin, entry_order)
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
                tp_price_param = self.format_price_param(coin, tp_price)
                sl_price_param = self.format_price_param(coin, sl_price)
                
                logger.info(f"📊 Setting TP @ ${tp_price:.2f} (+{params['tp_percent']*100:.2f}%)")
                logger.info(f"📊 Setting SL @ ${sl_price:.2f} (-{params['sl_percent']*100:.2f}%)")
                
                # Place TP order
                try:
                    tp_order = self.client.futures_create_order(
                        symbol=coin,
                        side=exit_side,
                        type='TAKE_PROFIT_MARKET',
                        stopPrice=tp_price_param,
                        quantity=formatted_quantity,
                        reduceOnly=True,
                        workingType='MARK_PRICE'
                    )
                    tp_order_ref = self.seed_order_cache(coin, tp_order)
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
                        stopPrice=sl_price_param,
                        quantity=formatted_quantity,
                        reduceOnly=True,
                        workingType='MARK_PRICE'
                    )
                    sl_order_ref = self.seed_order_cache(coin, sl_order)
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
    
    def cancel_order_safe(self, coin, order_id, conditional=False):
        """Safely cancel an order"""
        lookup_params = self.resolve_order_lookup_params(coin, order_id)
        if not lookup_params:
            return True

        if conditional:
            lookup_params = {**lookup_params, 'conditional': True}
        
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
    
    def check_order_status(self, coin, order_id, conditional=False):
        """Check if an order is filled"""
        if not order_id:
            return 'PAPER' if PAPER_TRADING else 'MISSING'

        if isinstance(order_id, str) and (order_id.startswith('paper:') or 'PAPER' in order_id):
            return 'PAPER'

        lookup_params = self.resolve_order_lookup_params(coin, order_id)
        if not lookup_params:
            return 'MISSING'

        cached_status = self.get_cached_order_status(order_id)
        if cached_status:
            return cached_status

        if conditional and self.user_stream_healthy():
            return 'PENDING_WS'

        if self.rest_backoff_active():
            return 'RATE_LIMITED'

        if conditional:
            lookup_params = {**lookup_params, 'conditional': True}
        
        try:
            order = self.client.futures_get_order(**lookup_params)
            return extract_order_status(order) or 'UNKNOWN'
        except Exception as exc:
            if is_rate_limit_error(exc):
                self.note_rate_limit(f"order status check {coin}", exc)
                return 'RATE_LIMITED'
            if conditional:
                return self.lookup_conditional_order_status(coin, order_id)
            return 'UNKNOWN'
    
    def check_signal_on_candle_close(self, coin, execution_price=None):
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

            with manager.data_lock:
                if manager.entry_df is None or manager.trend_df is None:
                    return
                entry_df = manager.entry_df.copy()
                trend_df = manager.trend_df.copy()

            if len(entry_df) < 2 or len(trend_df) < 2:
                return
            
            # Calculate indicators on buffered data that is updated by WebSocket closes.
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
                    
                    # Use the closed candle price delivered by the WebSocket event instead of a fresh REST ticker call.
                    market_price = execution_price if execution_price is not None else float(entry_df.iloc[-1]['close'])
                    
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
                    if not PAPER_TRADING and (not tp_order_ref or not sl_order_ref):
                        logger.error(f"❌ {coin} entry is missing TP/SL protection. Closing the position immediately.")
                        try:
                            formatted_quantity = self.format_quantity(coin, quantity)
                            if not formatted_quantity:
                                logger.error(f"❌ Invalid formatted protection-close quantity for {coin}: {quantity}")
                                return
                            self.client.futures_create_order(
                                symbol=coin,
                                side='SELL' if signal == 'LONG' else 'BUY',
                                type='MARKET',
                                quantity=formatted_quantity,
                                reduceOnly=True
                            )
                            logger.info(f"✅ Closed unprotected {coin} entry immediately")
                        except Exception as protection_error:
                            logger.error(f"❌ Failed to close unprotected {coin} entry: {protection_error}")
                        return

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
            tp_status = self.check_order_status(coin, position.get('tp_order_id'), conditional=True)
            sl_status = self.check_order_status(coin, position.get('sl_order_id'), conditional=True)
            
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
                self.cancel_order_safe(coin, position.get('sl_order_id'), conditional=True)
                # Handle exit
                self.handle_exit(coin, 'TP', position['tp_price'])
                return 'TP'
            
            # Check if SL filled
            if sl_status == 'FILLED':
                logger.info(f"📊 {coin} SL Order Filled!")
                # Cancel TP order
                self.cancel_order_safe(coin, position.get('tp_order_id'), conditional=True)
                # Handle exit
                self.handle_exit(coin, 'SL', position['sl_price'])
                return 'SL'
            
            # Check if both orders somehow disappeared (wait longer on testnet)
            missing_statuses = {'UNKNOWN', 'MISSING'}
            if tp_status == 'RATE_LIMITED' or sl_status == 'RATE_LIMITED':
                return None

            if tp_status == 'PENDING_WS' or sl_status == 'PENDING_WS':
                return None

            if tp_status in missing_statuses and sl_status in missing_statuses and not PAPER_TRADING:
                if self.user_stream_healthy():
                    return None

                # On testnet, give orders more time to appear (testnet is slower)
                position_age = (datetime.now() - position.get('entry_time', datetime.now())).total_seconds()
                if position_age < 90:  # Give exchange-side TP/SL and websocket reconciliation time before panicking.
                    return None  # Don't close yet, orders might still be syncing
                
                try:
                    # Try to get current price first
                    ticker = self.client.futures_symbol_ticker(symbol=coin)
                    current_price = float(ticker['price'])
                    
                    # Only proceed if we successfully got price
                    logger.warning(f"⚠️ Both orders missing for {coin} after 90s with no private-stream confirmation! Closing position manually")
                    
                    # Place market order to close
                    quantity = position['quantity']
                    formatted_quantity = self.format_quantity(coin, quantity)
                    if not formatted_quantity:
                        logger.error(f"❌ Invalid formatted manual-close quantity for {coin}: {quantity}")
                        return None
                    if position['side'] == 'LONG':
                        self.client.futures_create_order(
                            symbol=coin,
                            side='SELL',
                            type='MARKET',
                            quantity=formatted_quantity
                        )
                    else:
                        self.client.futures_create_order(
                            symbol=coin,
                            side='BUY',
                            type='MARKET',
                            quantity=formatted_quantity
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
            self.check_account(fatal=False)
            
        except Exception as e:
            logger.error(f"Error handling exit for {coin}: {e}")

    def reconcile_exit_fill_from_user_stream(self, order):
        """Close tracked positions immediately when a protective exit fill arrives on the private stream."""
        try:
            symbol = order.get('s')
            position = self.position_tracker.get_position(symbol)
            if not position:
                return False

            order_status = str(order.get('X') or order.get('x') or '').upper()
            if order_status != 'FILLED':
                return False

            order_side = str(order.get('S') or '').upper()
            expected_exit_side = 'SELL' if position['side'] == 'LONG' else 'BUY'
            if order_side != expected_exit_side:
                return False

            order_type = str(order.get('o') or '').upper()
            original_type = str(order.get('ot') or '').upper()
            reduce_only = self.is_truthy_exchange_flag(order.get('R'))
            close_position = self.is_truthy_exchange_flag(order.get('cp'))

            # Ignore fills that do not look like protective or reducing exits.
            if not (
                order_type.startswith('TAKE_PROFIT')
                or order_type.startswith('STOP')
                or original_type.startswith('TAKE_PROFIT')
                or original_type.startswith('STOP')
                or reduce_only
                or close_position
            ):
                return False

            if order_type.startswith('TAKE_PROFIT') or original_type.startswith('TAKE_PROFIT'):
                exit_type = 'TP'
            elif order_type.startswith('STOP') or original_type.startswith('STOP'):
                exit_type = 'SL'
            else:
                trigger_price = None
                try:
                    raw_trigger = order.get('sp')
                    if raw_trigger not in (None, '', '0', '0.0', '0.000', '0.00000'):
                        trigger_price = float(raw_trigger)
                except (TypeError, ValueError):
                    trigger_price = None

                if trigger_price is not None:
                    tp_distance = abs(trigger_price - float(position.get('tp_price', trigger_price)))
                    sl_distance = abs(trigger_price - float(position.get('sl_price', trigger_price)))
                    exit_type = 'TP' if tp_distance <= sl_distance else 'SL'
                else:
                    exit_type = 'MANUAL'

            exit_price = None
            for candidate in (order.get('ap'), order.get('L'), order.get('sp')):
                if candidate in (None, '', '0', '0.0', '0.000', '0.00000'):
                    continue
                try:
                    exit_price = float(candidate)
                    break
                except (TypeError, ValueError):
                    continue

            if exit_price is None or exit_price <= 0:
                if exit_type == 'TP':
                    exit_price = float(position.get('tp_price', position['entry_price']))
                elif exit_type == 'SL':
                    exit_price = float(position.get('sl_price', position['entry_price']))
                else:
                    exit_price = float(position['entry_price'])

            sibling_order_ref = (
                position.get('sl_order_id')
                if exit_type == 'TP'
                else position.get('tp_order_id')
            )
            if exit_type in {'TP', 'SL'} and sibling_order_ref:
                self.cancel_order_safe(symbol, sibling_order_ref, conditional=True)

            logger.info(
                f"📊 {symbol} {exit_type} reconciled from private-stream fill @ ${exit_price:.2f}"
            )
            self.handle_exit(symbol, exit_type, exit_price)
            return True

        except Exception as exc:
            logger.error(f"Error reconciling user-stream exit fill: {exc}")
            return False

    def start_market_streams(self):
        """Use WebSocket kline streams for candle-close detection."""
        logger.info("🌐 Starting futures kline WebSocket streams...")
        logger.info("📊 NORMAL MODE - Exits on TP/SL only (matching optimization)")

        self.running = True
        self.last_candles = {}

        for coin, manager in self.coin_managers.items():
            self.last_candles[coin] = {
                manager.entry_timeframe: None,
                manager.trend_timeframe: None
            }

        self.ws_manager = ThreadedWebsocketManager(
            api_key=api_key,
            api_secret=secret_key,
            testnet=USE_TESTNET,
        )
        self.ws_manager.start()
        self.user_stream_started_at = time.time()
        self.user_stream_name = self.ws_manager.start_futures_user_socket(
            callback=self.handle_user_stream_message
        )
        self.ws_streams.append(self.user_stream_name)
        logger.info("✅ Futures user-data stream active for order/account reconciliation")

        started = set()
        for coin, manager in self.coin_managers.items():
            for timeframe in {manager.entry_timeframe, manager.trend_timeframe}:
                stream_key = (coin, timeframe)
                if stream_key in started:
                    continue
                stream_name = self.ws_manager.start_kline_futures_socket(
                    callback=self.handle_kline_message,
                    symbol=coin,
                    interval=timeframe,
                )
                self.ws_streams.append(stream_name)
                started.add(stream_key)

        logger.info("✅ WebSocket market streams active for %s symbol/timeframe pairs", len(started))

    def handle_user_stream_message(self, msg):
        """Track order/account updates from the futures private stream before falling back to REST."""
        try:
            if not isinstance(msg, dict):
                return

            if msg.get('e') == 'error':
                logger.error(f"User WebSocket error: {msg.get('type')} - {msg.get('m')}")
                return

            self.user_stream_last_message_at = time.time()
            event_type = msg.get('e')

            if event_type == 'ORDER_TRADE_UPDATE':
                order = msg.get('o', {})
                symbol = order.get('s')
                order_status = order.get('X') or order.get('x') or 'UNKNOWN'
                order_id = order.get('i')
                client_order_id = order.get('c')

                cache_payload = {
                    'status': order_status,
                    'symbol': symbol,
                    'updated_at': self.user_stream_last_message_at,
                    'source': 'user_stream',
                    'execution_type': order.get('x'),
                }

                references = []
                if order_id not in (None, ''):
                    references.append(f"oid:{order_id}")
                if client_order_id:
                    references.append(f"cid:{client_order_id}")

                for reference in references:
                    self.user_order_cache[reference] = cache_payload

                if order_status in {'FILLED', 'CANCELED', 'EXPIRED', 'REJECTED'}:
                    display_ref = references[0] if references else order_id
                    logger.info(f"📨 {symbol} order update {format_order_reference(display_ref)}: {order_status}")
                    if order_status == 'FILLED':
                        self.reconcile_exit_fill_from_user_stream(order)

            elif event_type == 'ACCOUNT_UPDATE':
                account = msg.get('a', {})
                for balance in account.get('B', []):
                    if balance.get('a') == 'USDT':
                        wallet_balance = balance.get('wb') or balance.get('cw')
                        if wallet_balance is not None:
                            self.account_balance = float(wallet_balance)
                        break

        except Exception as exc:
            logger.error(f"Error handling user stream message: {exc}")

    def handle_kline_message(self, msg):
        """Handle futures kline messages from WebSocket streams."""
        try:
            if not isinstance(msg, dict):
                return

            if msg.get('e') == 'error':
                logger.error(f"WebSocket error: {msg.get('type')} - {msg.get('m')}")
                return

            kline = msg.get('k')
            if not kline or not kline.get('x'):
                return

            coin = msg.get('s') or msg.get('ps') or kline.get('s') or kline.get('ps')
            timeframe = kline.get('i')
            if not coin or not timeframe or coin not in self.coin_managers:
                return

            candle_time = kline.get('t')
            close_price = float(kline.get('c', 0))
            manager = self.coin_managers[coin]

            if self.last_candles[coin].get(timeframe) == candle_time:
                return

            self.last_candles[coin][timeframe] = candle_time
            self.update_candle_buffers(coin, timeframe, kline)
            logger.info(f"📊 {coin} {timeframe} candle closed at ${close_price:.2f}")

            if timeframe == manager.entry_timeframe:
                self.check_signal_on_candle_close(coin, execution_price=close_price)

        except Exception as e:
            logger.error(f"Error handling kline message: {e}")
    
    def trading_loop(self):
        """Main trading loop - checks if TP/SL orders filled"""
        logger.info("🚀 Starting trading loop...")
        thresholds = ", ".join([f"{coin.replace('USDC','')}={manager.params['parameters']['entry_threshold']:.3f}" 
                               for coin, manager in self.coin_managers.items()])
        logger.info(f"   Entry thresholds: {thresholds}")
        logger.info("⏳ Waiting for WebSocket candle-close events...")
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

                self.maybe_handle_spy_bias_change()
                
                # Check each coin for order fills
                for coin in ACTIVE_COINS:
                    if coin not in self.coin_managers:
                        continue
                    
                    # Only check exits if position exists
                    if self.position_tracker.has_position(coin):
                        exit_signal = self.check_exit_conditions(coin)
                        if exit_signal:
                            logger.info(f"✅ {coin} position closed: {exit_signal}")
                
                # Sleep before next iteration.
                # Binance already enforces TP/SL on-exchange, so local reconciliation does not need 5-second REST polling.
                time.sleep(10)
                
            except Exception as e:
                logger.error(f"❌ Error in trading loop: {e}")
                time.sleep(10)
    
    def cleanup_open_orders(self):
        """Cancel all open TP/SL orders on shutdown"""
        logger.info("🧹 Cleaning up open orders...")
        
        for coin in ACTIVE_COINS:
            position = self.position_tracker.get_position(coin)
            if position:
                # Cancel TP order
                if position.get('tp_order_id'):
                    self.cancel_order_safe(coin, position['tp_order_id'], conditional=True)
                
                # Cancel SL order
                if position.get('sl_order_id'):
                    self.cancel_order_safe(coin, position['sl_order_id'], conditional=True)
                
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

        self.load_initial_candle_buffers()
        
        # Start WebSocket market streams instead of REST polling
        self.start_market_streams()
        
        # Wait for initial data
        logger.info("⏳ Waiting for initial data (10 seconds)...")
        time.sleep(10)
        
        # Start trading loop
        self.trading_loop()
    
    def stop(self):
        """Stop the bot"""
        logger.info("🛑 Stopping bot...")
        self.running = False
        if self.ws_manager is not None:
            try:
                self.ws_manager.stop()
            except Exception as exc:
                logger.warning(f"⚠️ Failed to stop WebSocket manager cleanly: {exc}")
        
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
