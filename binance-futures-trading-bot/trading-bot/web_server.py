"""
Web server for trading bot monitoring
Provides API endpoints and serves frontend
"""

import os
import re
import requests
import sqlite3
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request
from flask_cors import CORS
from copy_trader import (
    list_followers, add_follower, toggle_follower, delete_follower
)


# Import bot configuration
APP_ROOT = next(
    (parent for parent in Path(__file__).resolve().parents if (parent / "spy_integration.py").exists()),
    None
)
if APP_ROOT is None:
    raise RuntimeError("Could not locate application root for SPY regime integration.")
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from config import DATABASE_ENABLED, DATABASE_FILE, LOG_FILE, LOG_TO_FILE
from environment import USE_TESTNET
from spy_integration import SpyRegimeFilter
from runtime_config import get_binance_credentials

from binance.client import Client
from binance.exceptions import BinanceAPIException

api_key, secret_key = get_binance_credentials(USE_TESTNET)

app = Flask(__name__)
CORS(app)

BOT_DIR = Path(__file__).resolve().parent
DATABASE_PATH = (BOT_DIR / DATABASE_FILE) if DATABASE_FILE else None
LOG_PATH = (BOT_DIR / LOG_FILE) if LOG_FILE else None
ENVIRONMENT_LABEL = "TESTNET" if USE_TESTNET else "MAINNET"
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
CONFIGURED_SELF_PING_URL = os.getenv("SELF_PING_URL", "").strip()
if RENDER_EXTERNAL_URL:
    SELF_PING_URL = f"{RENDER_EXTERNAL_URL}/health"
else:
    SELF_PING_URL = CONFIGURED_SELF_PING_URL or "http://127.0.0.1:5000/health"
SELF_PING_INTERVAL_SECONDS = 600
ACCOUNT_REFRESH_SECONDS = 30
TP_SL_CACHE_REFRESH_SECONDS = 15 * 60
RATE_LIMIT_BAN_UNTIL_RE = re.compile(r"banned until (\d+)")
TRADE_EXIT_RE = re.compile(
    r"Closed:\s+([A-Z_]+)\s+@\s+\$[-+]?\d+(?:\.\d+)?\s+\|\s+PnL:\s+([-+]?\d+(?:\.\d+)?)%"
)

# Global variables for storing bot data
bot_data = {
    'balance': 0,
    'active_trades': [],
    'wins': 0,
    'losses': 0,
    'total_trades': 0,
    'win_rate': 0,
    'pnl': 0,
    'status': 'Starting...',
    'logs': deque(maxlen=100),  # Keep last 100 log lines
    'last_update': None,
    'environment': ENVIRONMENT_LABEL,
    'spy_regime': None,
    'spy_regime_error': None,
}

live_trade_stats = {
    'total': 0,
    'wins': 0,
    'losses': 0,
    'tp': 0,
    'sl': 0,
    'manual': 0,
    'bias_change': 0,
}

# Binance client
client = None
bot_process = None
spy_regime_filter = SpyRegimeFilter(APP_ROOT)
log_file_offset = 0
monitor_next_account_refresh_at = 0.0
monitor_rate_limit_until = 0.0
monitor_position_targets_cache = {}
bot_session_started_at = None
bot_session_start_balance = None
bot_session_start_balance_pending = False


def is_rate_limit_error(exc):
    message = str(exc)
    return "code=-1003" in message or "Too many requests" in message or "banned until" in message


def parse_rate_limit_retry_at(exc):
    match = RATE_LIMIT_BAN_UNTIL_RE.search(str(exc))
    if not match:
        return None
    return (int(match.group(1)) / 1000.0) + 1


def note_monitor_rate_limit(exc, source):
    global monitor_rate_limit_until
    retry_at = parse_rate_limit_retry_at(exc) or (time.time() + 60)
    monitor_rate_limit_until = max(monitor_rate_limit_until, retry_at)
    retry_dt = datetime.fromtimestamp(monitor_rate_limit_until).strftime("%Y-%m-%d %H:%M:%S")
    append_bot_log_line(f"[WARN] Binance monitor backoff after {source} until {retry_dt}: {exc}")


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


def identify_monitor_protective_orders(position_side, open_orders):
    """Pick TP and SL conditional orders for a visible exchange position."""
    exit_side = 'SELL' if position_side == 'LONG' else 'BUY'
    tp_order = None
    sl_order = None

    for order in sorted(
        open_orders or [],
        key=lambda item: (
            int(item.get('updateTime') or item.get('time') or 0),
            int(item.get('orderId') or item.get('algoId') or 0),
        ),
        reverse=True,
    ):
        order_side = str(order.get('side') or order.get('S') or '').upper()
        order_type = str(extract_order_type(order) or '').upper()

        if order_side != exit_side:
            continue

        if order_type.startswith('TAKE_PROFIT') and tp_order is None:
            tp_order = order
        elif order_type.startswith('STOP') and sl_order is None:
            sl_order = order

        if tp_order and sl_order:
            break

    return tp_order, sl_order


def build_monitor_position_cache_key(position_payload):
    """Capture the position identity that should invalidate cached TP/SL targets."""
    return (
        position_payload.get('side'),
        round(abs(float(position_payload.get('amount', 0))), 8),
        round(float(position_payload.get('entry_price', 0)), 8),
    )


def get_cached_monitor_targets(symbol, position_cache_key, now):
    """Return reusable TP/SL targets when the cached position still matches and is fresh."""
    cached = monitor_position_targets_cache.get(symbol)
    if not cached:
        return None
    if cached.get('position_key') != position_cache_key:
        return None
    fetched_at = float(cached.get('fetched_at') or 0.0)
    if now - fetched_at >= TP_SL_CACHE_REFRESH_SECONDS:
        return None
    return cached


def record_trade_exit_from_log(line):
    """Update in-memory dashboard counters from bot exit logs when DB persistence is disabled."""
    match = TRADE_EXIT_RE.search(line)
    if not match:
        return

    exit_type = match.group(1)
    pnl_pct = float(match.group(2))
    live_trade_stats['total'] += 1

    if exit_type == 'TP':
        live_trade_stats['tp'] += 1
        live_trade_stats['wins'] += 1
    elif exit_type == 'SL':
        live_trade_stats['sl'] += 1
        live_trade_stats['losses'] += 1
    elif exit_type == 'MANUAL':
        live_trade_stats['manual'] += 1
    elif exit_type == 'BIAS_CHANGE':
        live_trade_stats['bias_change'] += 1
        if pnl_pct > 0:
            live_trade_stats['wins'] += 1
        else:
            live_trade_stats['losses'] += 1


def append_bot_log_line(line):
    """Append a bot log line and update any in-memory derived dashboard stats."""
    bot_data['logs'].append(line)
    record_trade_exit_from_log(line)


def apply_live_trade_stats():
    """Expose in-memory trade counters in the dashboard when file/database persistence is off."""
    wins = live_trade_stats['wins']
    losses = live_trade_stats['losses']
    closed_trades = live_trade_stats['total']
    decisive_trades = wins + losses

    bot_data['wins'] = wins
    bot_data['losses'] = losses
    bot_data['total_trades'] = closed_trades
    bot_data['win_rate'] = (wins / decisive_trades * 100) if decisive_trades > 0 else 0


def mark_bot_session_start():
    """Start a fresh dashboard session baseline for the current bot process lifetime."""
    global bot_session_started_at, bot_session_start_balance, bot_session_start_balance_pending
    bot_session_started_at = datetime.now(timezone.utc)

    current_balance = bot_data.get('balance', 0)
    if current_balance and current_balance > 0:
        bot_session_start_balance = float(current_balance)
        bot_session_start_balance_pending = False
    else:
        bot_session_start_balance = None
        bot_session_start_balance_pending = True


def get_bot_session_stats():
    """Return bot-session stats for the dashboard modal."""
    current_balance = float(bot_data.get('balance') or 0.0)
    unrealized_pnl = float(bot_data.get('pnl') or 0.0)

    if bot_session_start_balance is None:
        realized_pnl = 0.0
        total_pnl = unrealized_pnl
        roi_pct = 0.0
    else:
        realized_pnl = current_balance - bot_session_start_balance
        total_pnl = realized_pnl + unrealized_pnl
        roi_pct = (total_pnl / bot_session_start_balance * 100) if bot_session_start_balance else 0.0

    return {
        'started_at': bot_session_started_at.isoformat() if bot_session_started_at else None,
        'start_balance': bot_session_start_balance,
        'current_balance': current_balance,
        'realized_pnl': realized_pnl,
        'unrealized_pnl': unrealized_pnl,
        'total_pnl': total_pnl,
        'roi_pct': roi_pct,
        'active_positions': len(bot_data.get('active_trades') or []),
        'wins': int(bot_data.get('wins') or 0),
        'losses': int(bot_data.get('losses') or 0),
        'total_closed': int(bot_data.get('total_trades') or 0),
        'manual_closes': int(live_trade_stats.get('manual') or 0),
        'bias_change_closes': int(live_trade_stats.get('bias_change') or 0),
        'pending_start_balance': bool(bot_session_start_balance_pending),
    }


def start_self_ping():
    """Keep the web service warm independently of bot start/stop state."""
    if RENDER_EXTERNAL_URL and CONFIGURED_SELF_PING_URL and CONFIGURED_SELF_PING_URL.rstrip("/") != SELF_PING_URL:
        print(
            f"web self-ping override ignored: SELF_PING_URL={CONFIGURED_SELF_PING_URL} "
            f"does not match Render URL {SELF_PING_URL}",
            flush=True,
        )

    def ping_loop():
        while True:
            time.sleep(SELF_PING_INTERVAL_SECONDS)
            try:
                response = requests.get(SELF_PING_URL, timeout=30)
                response.raise_for_status()
                print(f"web self-ping ok: {SELF_PING_URL}", flush=True)
            except Exception as exc:
                print(f"web self-ping failed for {SELF_PING_URL}: {exc}", flush=True)

    thread = threading.Thread(target=ping_loop, daemon=True)
    thread.start()
    print(
        f"web self-ping enabled: {SELF_PING_URL} every {SELF_PING_INTERVAL_SECONDS}s",
        flush=True,
    )

def init_binance_client():
    """Initialize Binance client"""
    global client
    try:
        if USE_TESTNET:
            client = Client(api_key, secret_key, testnet=True)
            client.API_URL = 'https://testnet.binance.vision/api'
            client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'
        else:
            client = Client(api_key, secret_key)
        refresh_account_snapshot(force=True)
        return True
    except Exception as e:
        append_bot_log_line(f"[ERROR] Failed to initialize Binance client: {str(e)}")
        return False


def stream_bot_output(process):
    """Capture child bot stdout for in-memory UI logs and Render console logs."""
    if process.stdout is None:
        return

    for raw_line in process.stdout:
        line = raw_line.strip()
        if not line:
            continue
        append_bot_log_line(line)
        print(line, flush=True)

def refresh_account_snapshot(force=False):
    """Refresh balance and positions with lighter Binance endpoints and rate-limit backoff."""
    global bot_data, monitor_next_account_refresh_at, monitor_position_targets_cache
    global bot_session_start_balance, bot_session_start_balance_pending

    if not client:
        return

    now = time.time()
    if not force:
        if now < monitor_rate_limit_until:
            return
        if now < monitor_next_account_refresh_at:
            return

    try:
        balances = client.futures_account_balance()
        positions = client.futures_position_information()

        total_balance = bot_data['balance']
        for asset in balances:
            if asset.get('asset') == 'USDT':
                total_balance = float(asset.get('balance', 0))
                break

        active_positions = []
        active_symbols = set()
        total_unrealized = 0.0
        for pos in positions:
            amount = float(pos.get('positionAmt', 0))
            unrealized = float(pos.get('unRealizedProfit', 0))
            total_unrealized += unrealized
            if amount == 0:
                continue
            active_position = {
                'symbol': pos['symbol'],
                'amount': amount,
                'entry_price': float(pos.get('entryPrice', 0)),
                'pnl': unrealized,
                'side': 'LONG' if amount > 0 else 'SHORT',
                'tp_target': None,
                'sl_target': None,
            }
            active_symbols.add(active_position['symbol'])
            position_cache_key = build_monitor_position_cache_key(active_position)
            cached_targets = get_cached_monitor_targets(
                active_position['symbol'],
                position_cache_key,
                now,
            )

            if cached_targets:
                active_position['tp_target'] = cached_targets.get('tp_target')
                active_position['sl_target'] = cached_targets.get('sl_target')
            else:
                fallback_targets = monitor_position_targets_cache.get(active_position['symbol'])
                try:
                    conditional_orders = client.futures_get_open_orders(
                        symbol=pos['symbol'],
                        conditional=True,
                    )
                    tp_order, sl_order = identify_monitor_protective_orders(
                        active_position['side'],
                        conditional_orders,
                    )
                    active_position['tp_target'] = extract_order_trigger_price(tp_order)
                    active_position['sl_target'] = extract_order_trigger_price(sl_order)
                    monitor_position_targets_cache[active_position['symbol']] = {
                        'position_key': position_cache_key,
                        'tp_target': active_position['tp_target'],
                        'sl_target': active_position['sl_target'],
                        'fetched_at': now,
                    }
                except Exception as exc:
                    if (
                        fallback_targets
                        and fallback_targets.get('position_key') == position_cache_key
                    ):
                        active_position['tp_target'] = fallback_targets.get('tp_target')
                        active_position['sl_target'] = fallback_targets.get('sl_target')
                    if is_rate_limit_error(exc):
                        note_monitor_rate_limit(exc, f"conditional snapshot {pos['symbol']}")
                    else:
                        append_bot_log_line(
                            f"[WARN] Failed to load TP/SL snapshot for {pos['symbol']}: {exc}"
                        )

            active_positions.append(active_position)

        bot_data['balance'] = total_balance
        bot_data['pnl'] = total_unrealized
        bot_data['active_trades'] = active_positions
        if bot_session_start_balance_pending and total_balance > 0:
            bot_session_start_balance = total_balance
            bot_session_start_balance_pending = False
        monitor_position_targets_cache = {
            symbol: cached_targets
            for symbol, cached_targets in monitor_position_targets_cache.items()
            if symbol in active_symbols
        }
        monitor_next_account_refresh_at = now + ACCOUNT_REFRESH_SECONDS
    except Exception as exc:
        if is_rate_limit_error(exc):
            note_monitor_rate_limit(exc, "account snapshot")
            return
        append_bot_log_line(f"[ERROR] Failed to refresh account snapshot: {str(exc)}")

def update_trade_stats():
    """Update trade statistics from database"""
    global bot_data
    if not DATABASE_ENABLED or DATABASE_PATH is None:
        apply_live_trade_stats()
        return
    try:
        if DATABASE_PATH.exists():
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(trades)")
            columns = {row[1] for row in cursor.fetchall()}

            if "pnl_value" in columns:
                cursor.execute("SELECT COUNT(*) FROM trades")
                total = cursor.fetchone()
                bot_data['total_trades'] = total[0] if total else 0

                if "exit_type" in columns:
                    cursor.execute(
                        "SELECT COUNT(*) FROM trades WHERE exit_type != 'MANUAL' AND pnl_value > 0"
                    )
                    wins = cursor.fetchone()
                    bot_data['wins'] = wins[0] if wins else 0

                    cursor.execute(
                        "SELECT COUNT(*) FROM trades WHERE exit_type != 'MANUAL' AND pnl_value <= 0"
                    )
                    losses = cursor.fetchone()
                    bot_data['losses'] = losses[0] if losses else 0
                else:
                    cursor.execute("SELECT COUNT(*) FROM trades WHERE pnl_value > 0")
                    wins = cursor.fetchone()
                    bot_data['wins'] = wins[0] if wins else 0

                    cursor.execute("SELECT COUNT(*) FROM trades WHERE pnl_value <= 0")
                    losses = cursor.fetchone()
                    bot_data['losses'] = losses[0] if losses else 0
            elif {"exit_time", "profit"}.issubset(columns):
                cursor.execute("SELECT COUNT(*) FROM trades WHERE exit_time IS NOT NULL")
                total = cursor.fetchone()
                bot_data['total_trades'] = total[0] if total else 0

                cursor.execute("SELECT COUNT(*) FROM trades WHERE exit_time IS NOT NULL AND profit > 0")
                wins = cursor.fetchone()
                bot_data['wins'] = wins[0] if wins else 0

                cursor.execute("SELECT COUNT(*) FROM trades WHERE exit_time IS NOT NULL AND profit <= 0")
                losses = cursor.fetchone()
                bot_data['losses'] = losses[0] if losses else 0
            else:
                bot_data['total_trades'] = 0
                bot_data['wins'] = 0
                bot_data['losses'] = 0

            # Calculate win rate
            if bot_data['total_trades'] > 0:
                bot_data['win_rate'] = (bot_data['wins'] / bot_data['total_trades']) * 100
            else:
                bot_data['win_rate'] = 0

            conn.close()
    except Exception as e:
        append_bot_log_line(f"[ERROR] Failed to update trade stats: {str(e)}")

def monitor_bot_logs():
    """Monitor bot log file for updates"""
    global bot_data, log_file_offset

    try:
        if not LOG_TO_FILE or LOG_PATH is None:
            return
        if not LOG_PATH.exists():
            return

        file_size = LOG_PATH.stat().st_size
        if file_size < log_file_offset:
            log_file_offset = 0

        with open(LOG_PATH, 'r', encoding='utf-8', errors='ignore') as f:
            f.seek(log_file_offset)
            for line in f:
                if line.strip():
                    append_bot_log_line(line.strip())
            log_file_offset = f.tell()
    except Exception as e:
        append_bot_log_line(f"[ERROR] Failed to read log file: {str(e)}")

def update_spy_regime():
    """Load the session-pinned SPY regime used by the live bot."""
    try:
        bot_data['spy_regime'] = spy_regime_filter.get_regime()
        bot_data['spy_regime_error'] = None
    except Exception as e:
        bot_data['spy_regime_error'] = str(e)
        if bot_data.get('spy_regime') is None:
            bot_data['spy_regime'] = None

def update_loop():
    """Background thread to update data"""
    while True:
        try:
            refresh_account_snapshot()
            update_trade_stats()
            update_spy_regime()
            monitor_bot_logs()
            bot_data['last_update'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            bot_data['status'] = 'Running' if bot_process and bot_process.poll() is None else 'Stopped'
        except Exception as e:
            print(f"Error in update loop: {e}")
        time.sleep(5)  # Update every 5 seconds

def start_bot():
    """Start the trading bot"""
    global bot_process
    try:
        if bot_process is None or bot_process.poll() is not None:
            popen_kwargs = {
                "args": [sys.executable, "main.py"],
                "cwd": BOT_DIR,
                "stderr": subprocess.STDOUT,
                "text": True,
            }
            if LOG_TO_FILE:
                popen_kwargs["stdout"] = subprocess.DEVNULL
            else:
                popen_kwargs["stdout"] = subprocess.PIPE
                popen_kwargs["bufsize"] = 1

            bot_process = subprocess.Popen(**popen_kwargs)
            mark_bot_session_start()

            if not LOG_TO_FILE:
                threading.Thread(
                    target=stream_bot_output,
                    args=(bot_process,),
                    daemon=True,
                ).start()

            append_bot_log_line("[INFO] Trading bot started")
            return True
    except Exception as e:
        append_bot_log_line(f"[ERROR] Failed to start bot: {str(e)}")
        return False

@app.route('/')
def index():
    """Serve the main page"""
    return render_template_string(HTML_TEMPLATE)

@app.route('/health')
def health():
    """Lightweight health endpoint for Render and self-ping."""
    return jsonify({
        'status': 'ok',
        'environment': ENVIRONMENT_LABEL,
        'bot_status': bot_data.get('status', 'Unknown'),
    })

@app.route('/api/data')
def get_data():
    """API endpoint for bot data"""
    # Convert deque to list for JSON serialization
    data = bot_data.copy()
    data['logs'] = list(bot_data['logs'])
    data['session_stats'] = get_bot_session_stats()
    return jsonify(data)

@app.route('/api/start', methods=['POST'])
def start_bot_api():
    """API endpoint to start the bot"""
    if start_bot():
        return jsonify({'success': True, 'message': 'Bot started'})
    else:
        return jsonify({'success': False, 'message': 'Failed to start bot'})

@app.route('/api/stop', methods=['POST'])
def stop_bot_api():
    """API endpoint to stop the bot"""
    global bot_process
    try:
        if bot_process and bot_process.poll() is None:
            bot_process.terminate()
            append_bot_log_line("[INFO] Trading bot stopped")
            return jsonify({'success': True, 'message': 'Bot stopped'})
        else:
            return jsonify({'success': False, 'message': 'Bot not running'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/followers', methods=['GET'])
def followers_list_api():
    try:
        return jsonify({'success': True, 'followers': list_followers(mask_keys=True)})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/followers', methods=['POST'])
def followers_add_api():
    try:
        body = request.get_json(force=True)
        label = body.get('label', '').strip()
        api_key = body.get('api_key', '').strip()
        secret_key = body.get('secret_key', '').strip()

        if not api_key or not secret_key:
            return jsonify({'success': False, 'message': 'api_key and secret_key required'}), 400

        rec = add_follower(label, api_key, secret_key)
        return jsonify({'success': True, 'follower': {'id': rec['id'], 'label': rec['label'], 'active': rec['active']}})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/api/followers/<fid>/toggle', methods=['POST'])
def followers_toggle_api(fid):
    try:
        rec = toggle_follower(fid)
        return jsonify({'success': True, 'id': rec['id'], 'active': rec['active']})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/api/followers/<fid>', methods=['DELETE'])
def followers_delete_api(fid):
    try:
        delete_follower(fid)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 400


# HTML Template
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Trading Bot</title>
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='16' fill='%230d1117'/%3E%3Cpath d='M14 42 L25 31 L34 37 L49 20' fill='none' stroke='%2388d58f' stroke-width='5' stroke-linecap='round' stroke-linejoin='round'/%3E%3Ccircle cx='49' cy='20' r='5' fill='%2388d58f'/%3E%3C/svg%3E">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@500;700;800&family=IBM+Plex+Mono:wght@400;500;600&family=Outfit:wght@500;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #0b1016;
            --bg-soft: #121923;
            --panel: rgba(18, 24, 34, 0.82);
            --panel-strong: rgba(15, 20, 29, 0.92);
            --card: rgba(255, 255, 255, 0.05);
            --border: rgba(255, 255, 255, 0.09);
            --border-strong: rgba(255, 255, 255, 0.16);
            --text: #f7f8fb;
            --muted: #a5afbe;
            --green: #88d58f;
            --green-soft: #f5f7fb;
            --green-tint: rgba(136, 213, 143, 0.10);
            --green-line: rgba(136, 213, 143, 0.24);
            --green-pill-tint: rgba(143, 207, 140, 0.12);
            --green-pill-line: rgba(143, 207, 140, 0.22);
            --red: #ff8b8b;
            --red-tint: rgba(255, 139, 139, 0.14);
            --red-line: rgba(255, 139, 139, 0.24);
            --blue: #9bafff;
            --shadow: 0 18px 44px rgba(4, 7, 13, 0.28);
            --shadow-soft: 0 10px 24px rgba(4, 7, 13, 0.16);
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, sans-serif;
            background:
                radial-gradient(circle at 10% 0%, rgba(155, 175, 255, 0.13), transparent 24%),
                radial-gradient(circle at 84% 8%, rgba(136, 213, 143, 0.09), transparent 20%),
                linear-gradient(180deg, #0c1118 0%, #101722 54%, #0b1017 100%);
            min-height: 100vh;
            color: var(--text);
            padding: 30px 24px 48px;
            letter-spacing: -0.015em;
            position: relative;
            overflow-x: hidden;
        }

        body::before {
            content: '';
            position: fixed;
            inset: 0;
            background:
                radial-gradient(circle at top, rgba(255, 255, 255, 0.10), transparent 22%),
                linear-gradient(180deg, rgba(255, 255, 255, 0.03), transparent 18%);
            pointer-events: none;
            opacity: 0.12;
        }

        .container {
            max-width: 1320px;
            margin: 0 auto;
            position: relative;
            z-index: 1;
        }

        .header {
            margin-bottom: 24px;
        }

        .hero {
            position: relative;
            overflow: hidden;
            padding: 30px;
            border-radius: 30px;
            background:
                radial-gradient(circle at top left, rgba(255, 255, 255, 0.08), transparent 28%),
                linear-gradient(180deg, rgba(20, 26, 37, 0.95), rgba(14, 19, 28, 0.95));
            border: 1px solid var(--border);
            box-shadow: var(--shadow);
        }

        .hero::before {
            content: '';
            position: absolute;
            left: 30px;
            right: 30px;
            top: 0;
            height: 1px;
            background: linear-gradient(90deg, rgba(255, 255, 255, 0.24), rgba(255, 255, 255, 0.04) 48%, transparent);
            pointer-events: none;
        }

        .hero::after {
            content: '';
            position: absolute;
            width: 240px;
            height: 240px;
            border-radius: 50%;
            background: radial-gradient(circle, rgba(255, 255, 255, 0.08), transparent 70%);
            top: -90px;
            right: -60px;
            pointer-events: none;
        }

        .hero-top {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 28px;
            flex-wrap: wrap;
        }

        .hero-copy {
            max-width: 700px;
        }

        .eyebrow {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 10px 14px;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(255, 255, 255, 0.08);
            color: #ced6e2;
            font-size: 12px;
            font-weight: 700;
            letter-spacing: -0.01em;
            margin-bottom: 18px;
        }

        .eyebrow::before {
            content: '';
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--green);
            box-shadow: 0 0 0 6px rgba(136, 213, 143, 0.10);
        }

        .title-row {
            display: flex;
            align-items: center;
            gap: 16px;
            flex-wrap: wrap;
        }

        .title {
            font-family: 'Outfit', sans-serif;
            font-size: clamp(40px, 5vw, 58px);
            font-weight: 800;
            line-height: 0.96;
            letter-spacing: -0.045em;
        }

        .title-text {
            color: var(--text);
            display: inline;
        }

        .subtitle {
            display: none;
        }

        .hero-meta {
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
            margin-top: 22px;
        }

        .status {
            display: inline-flex;
            align-items: center;
            gap: 10px;
            padding: 11px 16px;
            border-radius: 100px;
            font-size: 12px;
            font-weight: 700;
            letter-spacing: -0.01em;
            border: 1px solid var(--border-strong);
            background: rgba(255, 255, 255, 0.04);
        }

        .status::before {
            content: '';
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: currentColor;
            box-shadow: 0 0 0 5px rgba(255, 255, 255, 0.06);
        }

        .status-running {
            background: rgba(143, 207, 140, 0.12);
            color: var(--green);
            border-color: rgba(143, 207, 140, 0.24);
        }

        .status-stopped {
            background: rgba(255, 75, 75, 0.14);
            color: var(--red) !important;
            border-color: rgba(255, 75, 75, 0.28);
            -webkit-text-fill-color: var(--red) !important;
        }

        .meta-chip,
        .last-update {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 11px 15px;
            border-radius: 999px;
            background: rgba(255, 255, 255, 0.045);
            border: 1px solid var(--border);
            color: var(--muted);
            font-size: 12px;
            font-weight: 700;
            letter-spacing: -0.01em;
        }

        .meta-button {
            appearance: none;
            font-family: 'DM Sans', sans-serif;
            cursor: pointer;
            color: var(--text);
            transition: transform 0.16s ease, border-color 0.16s ease, background 0.16s ease;
        }

        .meta-button:hover {
            transform: translateY(-1px);
            border-color: rgba(155, 175, 255, 0.24);
            background: rgba(155, 175, 255, 0.10);
        }

        .controls {
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
            justify-content: flex-end;
            align-items: flex-start;
            padding: 8px;
            border-radius: 24px;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.08);
            box-shadow: var(--shadow-soft);
        }

        .metrics {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
            gap: 16px;
            margin-bottom: 28px;
        }

        .metric {
            position: relative;
            overflow: hidden;
            background: linear-gradient(180deg, rgba(255, 255, 255, 0.06), rgba(255, 255, 255, 0.028));
            border: 1px solid var(--border);
            border-radius: 24px;
            padding: 22px;
            transition: transform 0.2s ease, border-color 0.2s ease, background 0.2s ease;
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.02), var(--shadow-soft);
        }

        .metric::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 1px;
            background: linear-gradient(90deg, rgba(255, 255, 255, 0.16), transparent 72%);
            opacity: 1;
        }

        .metric:hover {
            transform: translateY(-2px);
            background: linear-gradient(180deg, rgba(255, 255, 255, 0.075), rgba(255, 255, 255, 0.03));
            border-color: rgba(255, 255, 255, 0.13);
        }

        .metric-label {
            font-size: 12px;
            color: var(--muted);
            letter-spacing: -0.01em;
            margin-bottom: 12px;
            font-weight: 700;
        }

        .metric-value {
            font-size: 31px;
            font-weight: 800;
            line-height: 1.05;
            font-variant-numeric: tabular-nums;
        }

        .metric-value.green {
            color: var(--green);
        }

        .metric-value.red {
            color: var(--red);
        }

        .dashboard-grid {
            display: grid;
            grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.85fr);
            gap: 22px;
            margin-bottom: 22px;
            align-items: start;
        }

        .regime-card {
            display: flex;
            flex-direction: column;
            gap: 18px;
        }

        .regime-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
        }

        .regime-badge {
            padding: 10px 14px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 700;
            letter-spacing: -0.01em;
            border: 1px solid transparent;
        }

        .regime-long {
            color: var(--green);
            background: var(--green-pill-tint);
            border-color: var(--green-pill-line);
        }

        .regime-short {
            color: var(--red);
            background: var(--red-tint);
            border-color: var(--red-line);
        }

        .regime-copy {
            color: #c4ccd6;
            font-size: 14px;
            line-height: 1.6;
            max-width: 52ch;
        }

        .detail-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px;
        }

        .detail-card {
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.07);
            border-radius: 18px;
            padding: 16px;
        }

        .detail-key {
            font-size: 11px;
            color: var(--muted);
            letter-spacing: -0.01em;
            margin-bottom: 8px;
            font-weight: 700;
        }

        .detail-value {
            font-size: 14px;
            font-weight: 800;
            line-height: 1.5;
            word-break: break-word;
            font-variant-numeric: tabular-nums;
        }

        .pill-row {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }

        .pill {
            padding: 8px 12px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: -0.01em;
            border: 1px solid rgba(255, 255, 255, 0.08);
        }

        .pill-warning {
            color: #ffcf5b;
            background: rgba(255, 207, 91, 0.12);
            border-color: rgba(255, 207, 91, 0.25);
        }

        .pill-muted {
            color: #b7b7b7;
            background: rgba(255, 255, 255, 0.03);
        }

        .regime-actions {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }

        .external-link-btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 11px 14px;
            border-radius: 14px;
            border: 1px solid rgba(255, 255, 255, 0.08);
            background: rgba(255, 255, 255, 0.04);
            color: var(--text);
            text-decoration: none;
            font-size: 12px;
            font-weight: 800;
            letter-spacing: -0.01em;
            transition: transform 0.16s ease, border-color 0.16s ease, background 0.16s ease;
        }

        .external-link-btn:hover {
            transform: translateY(-1px);
            border-color: rgba(136, 213, 143, 0.26);
            background: rgba(136, 213, 143, 0.09);
        }

        .positions {
            background: linear-gradient(180deg, rgba(19, 25, 35, 0.94), rgba(14, 19, 28, 0.94));
            border: 1px solid var(--border);
            border-radius: 28px;
            padding: 26px;
            box-shadow: var(--shadow);
        }

        .section-header {
            font-family: 'Outfit', sans-serif;
            font-size: 22px;
            font-weight: 700;
            margin-bottom: 20px;
            color: var(--text);
            letter-spacing: -0.03em;
        }

        .position {
            position: relative;
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 16px 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
            outline: none;
        }

        .position:last-child {
            border-bottom: none;
        }

        .position-tooltip {
            position: absolute;
            left: 0;
            bottom: calc(100% + 10px);
            display: grid;
            gap: 6px;
            min-width: 160px;
            padding: 12px 14px;
            border-radius: 16px;
            border: 1px solid rgba(255, 255, 255, 0.08);
            background: rgba(8, 11, 17, 0.96);
            box-shadow: 0 18px 36px rgba(0, 0, 0, 0.28);
            opacity: 0;
            pointer-events: none;
            transform: translateY(6px);
            transition: opacity 0.16s ease, transform 0.16s ease;
            z-index: 20;
        }

        .position:hover .position-tooltip,
        .position:focus-visible .position-tooltip {
            opacity: 1;
            transform: translateY(0);
        }

        .position-tooltip-line {
            display: flex;
            justify-content: space-between;
            gap: 12px;
            font-size: 12px;
            font-variant-numeric: tabular-nums;
            white-space: nowrap;
        }

        .position-tooltip-label {
            color: var(--muted);
            font-weight: 700;
        }

        .position-tooltip-value {
            color: var(--text);
            font-weight: 800;
        }

        .position-info {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .coin-symbol {
            font-size: 18px;
            font-weight: 800;
            color: var(--text);
        }

        .position-side {
            padding: 7px 12px;
            border-radius: 100px;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: -0.01em;
            border: 1px solid transparent;
        }

        .side-long {
            color: var(--green);
            background: var(--green-pill-tint);
            border-color: var(--green-pill-line);
        }

        .side-short {
            color: var(--red);
            background: var(--red-tint);
            border-color: var(--red-line);
        }

        .position-details {
            text-align: right;
        }

        .entry-price {
            font-size: 13px;
            color: var(--muted);
            margin-bottom: 4px;
            font-variant-numeric: tabular-nums;
        }

        .position-pnl {
            font-size: 15px;
            font-weight: 800;
            font-variant-numeric: tabular-nums;
        }

        .logs {
            background: linear-gradient(180deg, rgba(19, 25, 35, 0.94), rgba(14, 19, 28, 0.94));
            border: 1px solid var(--border);
            border-radius: 28px;
            padding: 26px;
            box-shadow: var(--shadow);
        }

        .log-container {
            max-height: 360px;
            overflow-y: auto;
            font-family: 'IBM Plex Mono', 'SF Mono', 'Monaco', monospace;
            font-size: 12px;
            line-height: 1.72;
            color: #a8b1be;
            background: rgba(7, 10, 15, 0.50);
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-radius: 20px;
            padding: 18px;
        }

        .log-container::-webkit-scrollbar {
            width: 8px;
        }

        .log-container::-webkit-scrollbar-track {
            background: transparent;
            border-radius: 4px;
        }

        .log-container::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.12);
            border-radius: 4px;
        }

        .log-container::-webkit-scrollbar-thumb:hover {
            background: rgba(255, 255, 255, 0.18);
        }

        .log-line {
            padding: 4px 0;
            border-bottom: 1px dashed rgba(255, 255, 255, 0.035);
            word-wrap: break-word;
        }

        .log-line:last-child {
            border-bottom: none;
        }

        .btn {
            padding: 14px 22px;
            border: 1px solid transparent;
            border-radius: 18px;
            font-size: 14px;
            font-weight: 700;
            cursor: pointer;
            transition: transform 0.18s ease, background 0.18s ease, border-color 0.18s ease;
            letter-spacing: -0.01em;
            font-family: 'DM Sans', sans-serif;
            box-shadow: var(--shadow-soft);
        }

        .btn:disabled {
            cursor: wait;
            opacity: 0.78;
            transform: none !important;
        }

        .btn-busy {
            position: relative;
        }

        .btn-busy::after {
            content: '';
            width: 8px;
            height: 8px;
            margin-left: 10px;
            border-radius: 50%;
            display: inline-block;
            vertical-align: middle;
            background: currentColor;
            opacity: 0.7;
            animation: pulse 1s infinite;
        }

        .btn-start {
            background: linear-gradient(180deg, #eff4ef 0%, #d6dfd5 100%);
            border-color: rgba(255, 255, 255, 0.18);
            color: #11161b;
            box-shadow: 0 10px 22px rgba(4, 7, 13, 0.20), inset 0 -3px 0 rgba(17, 22, 27, 0.10);
        }

        .btn-start:hover {
            transform: translateY(-1px);
            background: linear-gradient(180deg, #f6f8f5 0%, #dfe7de 100%);
        }

        .btn-stop {
            background: rgba(255, 255, 255, 0.06);
            border-color: var(--border);
            color: var(--text);
        }

        .btn-stop:hover {
            background: rgba(255, 255, 255, 0.06);
            border-color: rgba(255, 255, 255, 0.14);
            transform: translateY(-1px);
        }

        .empty-state {
            text-align: center;
            padding: 46px 24px;
            color: #6b786f;
            font-size: 14px;
            font-weight: 700;
        }

        @keyframes pulse {
            0%, 100% {
                opacity: 1;
            }
            50% {
                opacity: 0.5;
            }
        }

        .loading {
            animation: pulse 2s infinite;
        }

        .btn-copy {
            background: rgba(155, 175, 255, 0.15);
            border-color: rgba(155, 175, 255, 0.20);
            color: #edf1ff;
        }

        .btn-copy:hover {
            background: rgba(140, 166, 255, 0.14);
            transform: translateY(-1px);
        }

        .btn-compact {
            padding: 10px 14px;
            font-size: 13px;
            border-radius: 16px;
        }

        .modal-backdrop {
            position: fixed;
            inset: 0;
            background: rgba(5, 7, 10, 0.76);
            backdrop-filter: blur(10px);
            z-index: 9999;
            padding: 24px;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow-y: auto;
        }

        .modal-panel {
            width: min(100%, 580px);
            max-width: 580px;
            background: linear-gradient(180deg, rgba(18, 22, 28, 0.98), rgba(11, 15, 20, 0.98));
            border: 1px solid var(--border);
            border-radius: 28px;
            padding: 24px;
            box-shadow: var(--shadow);
            display: flex;
            flex-direction: column;
            max-height: min(calc(100dvh - 48px), 760px);
            overflow: hidden;
            margin: auto;
        }

        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 16px;
            margin-bottom: 16px;
        }

        .modal-title-wrap {
            min-width: 0;
        }

        .modal-title {
            font-family: 'Outfit', sans-serif;
            font-size: 22px;
            font-weight: 700;
            margin-bottom: 8px;
        }

        .modal-copy {
            font-size: 14px;
            color: var(--muted);
            line-height: 1.6;
            margin-bottom: 0;
        }

        .modal-close {
            width: 40px;
            height: 40px;
            border-radius: 14px;
            border: 1px solid var(--border);
            background: rgba(255, 255, 255, 0.05);
            color: var(--text);
            font-size: 22px;
            line-height: 1;
            cursor: pointer;
            flex: 0 0 auto;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            transition: transform 0.18s ease, background 0.18s ease, border-color 0.18s ease;
        }

        .modal-close:hover {
            transform: translateY(-1px);
            background: rgba(255, 255, 255, 0.08);
            border-color: rgba(255, 255, 255, 0.16);
        }

        .modal-body {
            overflow-y: auto;
            padding-right: 4px;
        }

        .modal-body::-webkit-scrollbar {
            width: 8px;
        }

        .modal-body::-webkit-scrollbar-track {
            background: transparent;
        }

        .modal-body::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.12);
            border-radius: 999px;
        }

        .modal-footer {
            display: flex;
            justify-content: flex-end;
            gap: 10px;
            margin-top: 18px;
            padding-top: 16px;
            border-top: 1px solid rgba(255, 255, 255, 0.06);
        }

        .session-stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px;
            margin-top: 18px;
        }

        .form-input {
            width: 100%;
            margin-bottom: 12px;
            padding: 14px 16px;
            border-radius: 18px;
            border: 1px solid var(--border);
            background: rgba(255, 255, 255, 0.04);
            color: var(--text);
            font-size: 14px;
            outline: none;
            transition: border-color 0.18s ease, background 0.18s ease;
        }

        .form-input:focus {
            border-color: rgba(140, 166, 255, 0.24);
            background: rgba(255, 255, 255, 0.05);
        }

        .modal-actions {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            margin-bottom: 14px;
        }

        .followers-list {
            max-height: 300px;
            overflow: auto;
            border-top: 1px solid rgba(255, 255, 255, 0.06);
            padding-top: 12px;
        }

        .follower-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            padding: 12px 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
        }

        .follower-row:last-child {
            border-bottom: none;
        }

        .follower-label {
            font-weight: 800;
            margin-bottom: 4px;
        }

        .follower-meta {
            font-size: 12px;
            color: var(--muted);
        }

        .follower-actions {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }

        @media (max-width: 980px) {
            .dashboard-grid {
                grid-template-columns: 1fr;
            }

            .hero {
                padding: 24px;
            }

            .hero-top {
                flex-direction: column;
                align-items: stretch;
            }

            .controls {
                justify-content: flex-start;
            }
        }

        @media (max-width: 640px) {
            body {
                padding: 18px 16px 32px;
            }

            .hero {
                padding: 22px 18px;
                border-radius: 22px;
            }

            .title {
                font-size: 38px;
            }

            .metrics {
                grid-template-columns: repeat(auto-fit, minmax(145px, 1fr));
            }

            .btn {
                flex: 1 1 100%;
                justify-content: center;
            }

            .modal-backdrop {
                padding: 12px;
                align-items: flex-start;
            }

            .modal-panel {
                width: 100%;
                max-height: calc(100dvh - 24px);
                border-radius: 22px;
                padding: 16px;
            }

            .modal-header {
                gap: 12px;
                margin-bottom: 14px;
            }

            .modal-title {
                font-size: 20px;
            }

            .session-stats-grid {
                grid-template-columns: 1fr;
            }

            .modal-footer {
                justify-content: stretch;
            }

            .modal-footer .btn {
                width: 100%;
            }
        }

    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="hero">
                <div class="hero-top">
                    <div class="hero-copy">
                        <div class="eyebrow">Daily market filter</div>
                        <div class="title-row">
                            <div class="title">
                                <span class="title-text">Trading Bot</span>
                            </div>
                        </div>
                        <div class="hero-meta">
                            <span class="status" id="status">Loading</span>
                            <span class="meta-chip">9 tracked pairs</span>
                            <span class="meta-chip">SPY bias active</span>
                            <span class="last-update" id="last-update">Last update: Never</span>
                            <button class="meta-chip meta-button" type="button" onclick="openSessionStatsModal()">Session stats</button>
                        </div>
                    </div>
                    <div class="controls">
                        <button class="btn btn-stop" id="stop-bot-btn" onclick="stopBot()">Stop bot</button>
                        <button class="btn btn-copy" onclick="openCopyModal()">Copy trade</button>
                        <button class="btn btn-start" id="start-bot-btn" onclick="startBot()">Start bot</button>
                    </div>
                </div>
            </div>
        </div>

        <div class="metrics">
            <div class="metric">
                <div class="metric-label">Balance</div>
                <div class="metric-value" id="balance">0</div>
            </div>
            <div class="metric">
                <div class="metric-label">Active</div>
                <div class="metric-value" id="active-trades">0</div>
            </div>
            <div class="metric">
                <div class="metric-label">Win Rate</div>
                <div class="metric-value" id="win-rate">0%</div>
            </div>
            <div class="metric">
                <div class="metric-label">Total</div>
                <div class="metric-value" id="total-trades">0</div>
            </div>
            <div class="metric">
                <div class="metric-label">Wins</div>
                <div class="metric-value green" id="wins">0</div>
            </div>
            <div class="metric">
                <div class="metric-label">Losses</div>
                <div class="metric-value red" id="losses">0</div>
            </div>
            <div class="metric">
                <div class="metric-label">PNL</div>
                <div class="metric-value" id="pnl">0</div>
            </div>
        </div>

        <div class="dashboard-grid">
            <div class="positions">
                <div class="section-header">SPY bias</div>
                <div id="spy-regime-card">
                    <div class="empty-state loading">Loading session bias...</div>
                </div>
            </div>

            <div class="positions">
                <div class="section-header">Open positions</div>
                <div id="positions-list">
                    <div class="empty-state">No active positions</div>
                </div>
            </div>
        </div>

        <div class="logs">
            <div class="section-header">Recent activity</div>
            <div class="log-container" id="logs">
                <div class="empty-state loading">Initializing...</div>
            </div>
        </div>
    </div>
    <div id="copy-modal" class="modal-backdrop" style="display:none;">
        <div class="modal-panel">
            <div class="modal-header">
                <div class="modal-title-wrap">
                    <h3 class="modal-title">Copy Trade Followers</h3>
                    <div class="modal-copy">
                        Add extra Binance Futures testnet accounts and mirror master entries and exits into them.
                    </div>
                </div>
                <button class="modal-close" type="button" aria-label="Close copy trade followers" onclick="closeCopyModal()">&times;</button>
            </div>
            <div class="modal-body">
                <input id="f-label" class="form-input" placeholder="Label (e.g. Friend A)" />
                <input id="f-api" class="form-input" placeholder="Testnet API Key" />
                <input id="f-secret" class="form-input" placeholder="Testnet Secret Key" type="password" />

                <div class="modal-actions">
                    <button class="btn btn-start btn-compact" onclick="addFollower()">Add Follower</button>
                </div>

                <div id="followers-list" class="followers-list"></div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-stop btn-compact" onclick="closeCopyModal()">Close</button>
            </div>
        </div>
    </div>
    <div id="session-stats-modal" class="modal-backdrop" style="display:none;" onclick="handleSessionStatsBackdrop(event)">
        <div class="modal-panel">
            <div class="modal-header">
                <div class="modal-title-wrap">
                    <h3 class="modal-title">Session Stats</h3>
                    <div class="modal-copy">
                        Session metrics for the current bot runtime. These reset when the bot starts again.
                    </div>
                </div>
                <button class="modal-close" type="button" aria-label="Close session stats" onclick="closeSessionStatsModal()">&times;</button>
            </div>
            <div class="modal-body">
                <div id="session-stats-content" class="session-stats-grid">
                    <div class="detail-card">
                        <div class="detail-key">Loading</div>
                        <div class="detail-value">Fetching session stats...</div>
                    </div>
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-stop btn-compact" onclick="closeSessionStatsModal()">Close</button>
            </div>
        </div>
    </div>


    <script>
        let pendingBotAction = null;
        const IST_TIME_ZONE = 'Asia/Kolkata';
        const LOG_TIMESTAMP_RE = /([0-9]{4}-[0-9]{2}-[0-9]{2}) ([0-9]{2}:[0-9]{2}:[0-9]{2})(,[0-9]{3})?/g;

        function formatNumber(num) {
            if (num >= 1000) {
                return (num / 1000).toFixed(1) + 'k';
            }
            return num.toFixed(2);
        }

        function formatMoneyValue(value) {
            if (value === null || value === undefined || Number.isNaN(value)) {
                return 'N/A';
            }
            const numeric = Number(value);
            return `${numeric >= 0 ? '+' : ''}${numeric.toFixed(2)} USDT`;
        }

        function formatPercentValue(value) {
            if (value === null || value === undefined || Number.isNaN(value)) {
                return 'N/A';
            }
            const numeric = Number(value);
            return `${numeric >= 0 ? '+' : ''}${numeric.toFixed(2)}%`;
        }

        function formatSessionDateTime(value) {
            if (!value) {
                return 'N/A';
            }

            const date = new Date(value);
            if (Number.isNaN(date.getTime())) {
                return value;
            }

            const formatter = new Intl.DateTimeFormat('en-GB', {
                timeZone: IST_TIME_ZONE,
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                hour12: false,
            });

            const parts = Object.fromEntries(
                formatter
                    .formatToParts(date)
                    .filter(part => part.type !== 'literal')
                    .map(part => [part.type, part.value])
            );

            return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
        }

        function convertUtcLogTimestampsToIst(log) {
            return log.replace(LOG_TIMESTAMP_RE, (_, datePart, timePart, millisPart = '') => {
                const isoMillis = millisPart ? millisPart.replace(',', '.') : '';
                const utcDate = new Date(`${datePart}T${timePart}${isoMillis}Z`);

                if (Number.isNaN(utcDate.getTime())) {
                    return `${datePart} ${timePart}${millisPart}`;
                }

                const formatter = new Intl.DateTimeFormat('en-GB', {
                    timeZone: IST_TIME_ZONE,
                    year: 'numeric',
                    month: '2-digit',
                    day: '2-digit',
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit',
                    hour12: false,
                });

                const parts = Object.fromEntries(
                    formatter
                        .formatToParts(utcDate)
                        .filter(part => part.type !== 'literal')
                        .map(part => [part.type, part.value])
                );

                return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}${millisPart}`;
            });
        }

        function formatPriceValue(value) {
            if (value === null || value === undefined || Number.isNaN(value)) {
                return 'N/A';
            }
            return Number(value).toFixed(4);
        }

        function setBotButtonState(currentStatus) {
            const startBtn = document.getElementById('start-bot-btn');
            const stopBtn = document.getElementById('stop-bot-btn');
            const isRunning = currentStatus === 'Running';

            const actionResolved =
                !pendingBotAction ||
                (pendingBotAction === 'start' && isRunning) ||
                (pendingBotAction === 'stop' && !isRunning);

            if (actionResolved) {
                pendingBotAction = null;
            }

            startBtn.classList.remove('btn-busy');
            stopBtn.classList.remove('btn-busy');

            startBtn.textContent = pendingBotAction === 'start' ? 'Starting...' : 'Start bot';
            stopBtn.textContent = pendingBotAction === 'stop' ? 'Stopping...' : 'Stop bot';

            if (pendingBotAction === 'start') {
                startBtn.classList.add('btn-busy');
            }

            if (pendingBotAction === 'stop') {
                stopBtn.classList.add('btn-busy');
            }

            if (pendingBotAction) {
                startBtn.disabled = true;
                stopBtn.disabled = true;
                return;
            }

            startBtn.disabled = isRunning;
            stopBtn.disabled = !isRunning;
        }

        function renderSpyRegime(spyRegime, spyError) {
            const cardEl = document.getElementById('spy-regime-card');

            if (!spyRegime) {
                cardEl.innerHTML = `<div class="empty-state">${spyError ? `SPY regime unavailable: ${spyError}` : 'No daily bias cached yet'}</div>`;
                return;
            }

            const regimeClass = spyRegime.regime === 'LONG_ONLY' ? 'regime-badge regime-long' : 'regime-badge regime-short';
            const regimeLabel = spyRegime.regime === 'LONG_ONLY' ? 'Longs only' : 'Shorts only';
            const missingExternal = (spyRegime.latest_missing_external || []);
            const effectiveUntil = formatSessionDateTime(spyRegime.effective_until_utc || spyRegime.effective_until_ny);
            const pills = missingExternal.length > 0
                ? missingExternal.map(item => `<span class="pill pill-warning">${item}</span>`).join('')
                : '<span class="pill pill-muted">All external features current</span>';

            cardEl.innerHTML = `
                <div class="regime-card">
                    <div class="regime-header">
                        <div>
                            <div class="${regimeClass}">${regimeLabel}</div>
                        </div>
                        <div class="detail-value">${spyRegime.direction || 'Unknown'} • ${(spyRegime.confidence * 100).toFixed(1)}%</div>
                    </div>
                    <div class="regime-copy">
                        This SPY direction is pinned for the current US trading session and updates at the next US market open.
                    </div>
                    <div class="detail-grid">
                        <div class="detail-card">
                            <div class="detail-key">Generated from</div>
                            <div class="detail-value">${spyRegime.as_of_date || 'Unknown'}</div>
                        </div>
                        <div class="detail-card">
                            <div class="detail-key">Active session</div>
                            <div class="detail-value">${spyRegime.predicting_for || 'Unknown'}</div>
                        </div>
                        <div class="detail-card">
                            <div class="detail-key">Data source</div>
                            <div class="detail-value">${spyRegime.market_data_source || 'Unknown'}</div>
                        </div>
                        <div class="detail-card">
                            <div class="detail-key">Effective until</div>
                            <div class="detail-value">${effectiveUntil}</div>
                        </div>
                    </div>
                    <div>
                        <div class="detail-key">Lagging external features</div>
                        <div class="pill-row">${pills}</div>
                    </div>
                    <div class="regime-actions">
                        <a class="external-link-btn" href="https://spy-predictor-ui.onrender.com/" target="_blank" rel="noopener noreferrer">
                            Open SPY predictor
                        </a>
                    </div>
                </div>
            `;
        }

        function renderSessionStats(sessionStats) {
            const contentEl = document.getElementById('session-stats-content');
            if (!sessionStats) {
                contentEl.innerHTML = `
                    <div class="detail-card">
                        <div class="detail-key">Session stats</div>
                        <div class="detail-value">Unavailable</div>
                    </div>
                `;
                return;
            }

            const startBalance = sessionStats.pending_start_balance
                ? 'Waiting for balance...'
                : formatMoneyValue(sessionStats.start_balance);

            contentEl.innerHTML = `
                <div class="detail-card">
                    <div class="detail-key">Session started</div>
                    <div class="detail-value">${formatSessionDateTime(sessionStats.started_at)}</div>
                </div>
                <div class="detail-card">
                    <div class="detail-key">Starting balance</div>
                    <div class="detail-value">${startBalance}</div>
                </div>
                <div class="detail-card">
                    <div class="detail-key">Current balance</div>
                    <div class="detail-value">${formatMoneyValue(sessionStats.current_balance)}</div>
                </div>
                <div class="detail-card">
                    <div class="detail-key">Realized PnL</div>
                    <div class="detail-value">${formatMoneyValue(sessionStats.realized_pnl)}</div>
                </div>
                <div class="detail-card">
                    <div class="detail-key">Unrealized PnL</div>
                    <div class="detail-value">${formatMoneyValue(sessionStats.unrealized_pnl)}</div>
                </div>
                <div class="detail-card">
                    <div class="detail-key">Total PnL</div>
                    <div class="detail-value">${formatMoneyValue(sessionStats.total_pnl)}</div>
                </div>
                <div class="detail-card">
                    <div class="detail-key">ROI</div>
                    <div class="detail-value">${formatPercentValue(sessionStats.roi_pct)}</div>
                </div>
                <div class="detail-card">
                    <div class="detail-key">Closed trades</div>
                    <div class="detail-value">${sessionStats.total_closed}</div>
                </div>
                <div class="detail-card">
                    <div class="detail-key">Wins / Losses</div>
                    <div class="detail-value">${sessionStats.wins} / ${sessionStats.losses}</div>
                </div>
                <div class="detail-card">
                    <div class="detail-key">Manual closes</div>
                    <div class="detail-value">${sessionStats.manual_closes}</div>
                </div>
                <div class="detail-card">
                    <div class="detail-key">Bias-change closes</div>
                    <div class="detail-value">${sessionStats.bias_change_closes || 0}</div>
                </div>
                <div class="detail-card">
                    <div class="detail-key">Active positions</div>
                    <div class="detail-value">${sessionStats.active_positions}</div>
                </div>
            `;
        }

        function openSessionStatsModal() {
            document.getElementById('session-stats-modal').style.display = 'block';
        }

        function closeSessionStatsModal() {
            document.getElementById('session-stats-modal').style.display = 'none';
        }

        function handleSessionStatsBackdrop(event) {
            if (event.target.id === 'session-stats-modal') {
                closeSessionStatsModal();
            }
        }

        async function fetchData() {
            try {
                const response = await fetch('/api/data');
                const data = await response.json();

                // Update status
                const statusEl = document.getElementById('status');
                statusEl.textContent = data.status === 'Running' ? 'Running' : 'Stopped';
                statusEl.className = `status ${data.status === 'Running' ? 'status-running' : 'status-stopped'}`;
                setBotButtonState(data.status);

                // Update metrics
                document.getElementById('balance').textContent = formatNumber(data.balance);
                document.getElementById('active-trades').textContent = data.active_trades.length;
                document.getElementById('win-rate').textContent = Math.round(data.win_rate) + '%';
                document.getElementById('total-trades').textContent = data.total_trades;
                document.getElementById('wins').textContent = data.wins;
                document.getElementById('losses').textContent = data.losses;

                // Update PNL with color
                const pnlEl = document.getElementById('pnl');
                pnlEl.textContent = formatNumber(data.pnl);
                pnlEl.className = `metric-value ${data.pnl >= 0 ? 'green' : 'red'}`;

                renderSpyRegime(data.spy_regime, data.spy_regime_error);
                renderSessionStats(data.session_stats);

                // Update positions
                const positionsEl = document.getElementById('positions-list');
                if (data.active_trades.length > 0) {
                    positionsEl.innerHTML = data.active_trades.map(pos => `
                        <div class="position" tabindex="0" aria-label="TP ${formatPriceValue(pos.tp_target)}, SL ${formatPriceValue(pos.sl_target)}">
                            <div class="position-tooltip">
                                <div class="position-tooltip-line">
                                    <span class="position-tooltip-label">TP</span>
                                    <span class="position-tooltip-value">${formatPriceValue(pos.tp_target)}</span>
                                </div>
                                <div class="position-tooltip-line">
                                    <span class="position-tooltip-label">SL</span>
                                    <span class="position-tooltip-value">${formatPriceValue(pos.sl_target)}</span>
                                </div>
                            </div>
                            <div class="position-info">
                                <span class="coin-symbol">${pos.symbol.replace('USDT', '')}</span>
                                <span class="position-side ${pos.side === 'LONG' ? 'side-long' : 'side-short'}">${pos.side}</span>
                            </div>
                            <div class="position-details">
                                <div class="entry-price">Entry: ${pos.entry_price}</div>
                                <div class="position-pnl ${pos.pnl >= 0 ? 'green' : 'red'}">
                                    ${pos.pnl >= 0 ? '+' : ''}${formatNumber(pos.pnl)} USDT
                                </div>
                            </div>
                        </div>
                    `).join('');
                } else {
                    positionsEl.innerHTML = '<div class="empty-state">No active positions</div>';
                }

                // Update logs
                const logsEl = document.getElementById('logs');
                if (data.logs.length > 0) {
                    // Clean logs from Unicode characters
                    const cleanLogs = data.logs.map(log =>
                        convertUtcLogTimestampsToIst(
                            log.replace(/\\u[\\dA-F]{4}/gi, '').replace(/\\U[\\dA-F]{8}/gi, '')
                        )
                    );
                    logsEl.innerHTML = cleanLogs.slice(-30).map(log =>
                        `<div class="log-line">${log}</div>`
                    ).join('');
                    // Auto-scroll to bottom to show newest logs
                    logsEl.scrollTop = logsEl.scrollHeight;
                }

                // Update last update time
                const now = new Date().toLocaleTimeString();
                document.getElementById('last-update').textContent = `Last update: ${now}`;

            } catch (error) {
                console.error('Error fetching data:', error);
            }
        }

        async function startBot() {
            if (pendingBotAction) {
                return;
            }

            try {
                pendingBotAction = 'start';
                setBotButtonState('Stopped');
                const response = await fetch('/api/start', { method: 'POST' });
                const result = await response.json();
                if (!result.success) {
                    console.error('Failed to start bot:', result.message);
                    pendingBotAction = null;
                    setBotButtonState('Stopped');
                    return;
                }
                await fetchData();
            } catch (error) {
                console.error('Error starting bot:', error);
                pendingBotAction = null;
                setBotButtonState('Stopped');
            }
        }

        async function stopBot() {
            if (pendingBotAction) {
                return;
            }

            try {
                pendingBotAction = 'stop';
                setBotButtonState('Running');
                const response = await fetch('/api/stop', { method: 'POST' });
                const result = await response.json();
                if (!result.success) {
                    console.error('Failed to stop bot:', result.message);
                    pendingBotAction = null;
                    setBotButtonState('Running');
                    return;
                }
                await fetchData();
            } catch (error) {
                console.error('Error stopping bot:', error);
                pendingBotAction = null;
                setBotButtonState('Running');
            }
        }
        function openCopyModal() {
            document.getElementById('copy-modal').style.display = 'block';
            loadFollowers();
        }
        function closeCopyModal() {
            document.getElementById('copy-modal').style.display = 'none';
        }

        async function addFollower() {
            const label = document.getElementById('f-label').value;
            const api_key = document.getElementById('f-api').value;
            const secret_key = document.getElementById('f-secret').value;

            const res = await fetch('/api/followers', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({label, api_key, secret_key})
            });
            const out = await res.json();
            if (!out.success) {
                alert('Add follower failed: ' + out.message);
                return;
            }

            document.getElementById('f-label').value = '';
            document.getElementById('f-api').value = '';
            document.getElementById('f-secret').value = '';
            await loadFollowers();
        }

        async function loadFollowers() {
            const res = await fetch('/api/followers');
            const out = await res.json();
            const box = document.getElementById('followers-list');

            if (!out.success || !out.followers || out.followers.length === 0) {
                box.innerHTML = '<div class="empty-state">No followers added</div>';
                return;
            }

            box.innerHTML = out.followers.map(f => `
                <div class="follower-row">
                    <div>
                        <div class="follower-label">${f.label}</div>
                        <div class="follower-meta">${f.api_key} • ${f.active ? 'ACTIVE' : 'PAUSED'}</div>
                    </div>
                    <div class="follower-actions">
                        <button class="btn btn-copy btn-compact" onclick="toggleFollower('${f.id}')">${f.active ? 'Pause' : 'Resume'}</button>
                        <button class="btn btn-stop btn-compact" onclick="deleteFollower('${f.id}')">Remove</button>
                    </div>
                </div>
            `).join('');
}

        async function toggleFollower(fid) {
            const res = await fetch(`/api/followers/${fid}/toggle`, {method: 'POST'});
            const out = await res.json();
            if (!out.success) alert(out.message || 'Toggle failed');
            await loadFollowers();
        }

        async function deleteFollower(fid) {
            const res = await fetch(`/api/followers/${fid}`, {method: 'DELETE'});
            const out = await res.json();
            if (!out.success) alert(out.message || 'Delete failed');
            await loadFollowers();
        }

        

        // Auto-refresh every 2 seconds
        setInterval(fetchData, 2000);

        // Initial load
        fetchData();
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    # Initialize Binance client
    if init_binance_client():
        append_bot_log_line("[INFO] Binance client initialized successfully")
    else:
        append_bot_log_line("[ERROR] Failed to initialize Binance client")

    start_self_ping()

    # Start the bot automatically when server starts
    start_bot()

    # Start background update thread
    update_thread = threading.Thread(target=update_loop, daemon=True)
    update_thread.start()

    # Get port from environment variable (for Render) or use 5000
    port = int(os.environ.get('PORT', 5000))

    # Run Flask app
    print(f"Starting web server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
