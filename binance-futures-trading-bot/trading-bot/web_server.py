"""
Web server for trading bot monitoring
Provides API endpoints and serves frontend
"""

import os
import sqlite3
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
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

from config import DATABASE_FILE, LOG_FILE
from environment import USE_TESTNET
from spy_integration import SpyRegimeFilter

if USE_TESTNET:
    from apikey_testnet import testnet_api_key as api_key, testnet_secret_key as secret_key
else:
    from apikey import api_key, secret_key

from binance.client import Client
from binance.exceptions import BinanceAPIException

app = Flask(__name__)
CORS(app)

BOT_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BOT_DIR / DATABASE_FILE
LOG_PATH = BOT_DIR / LOG_FILE
ENVIRONMENT_LABEL = "TESTNET" if USE_TESTNET else "MAINNET"

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

# Binance client
client = None
bot_process = None
spy_regime_filter = SpyRegimeFilter(APP_ROOT)
log_file_offset = 0

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
        return True
    except Exception as e:
        bot_data['logs'].append(f"[ERROR] Failed to initialize Binance client: {str(e)}")
        return False

def update_balance():
    """Update account balance"""
    global bot_data
    try:
        if client:
            account = client.futures_account()
            bot_data['balance'] = float(account['totalWalletBalance'])
            bot_data['pnl'] = float(account.get('totalUnrealizedProfit', 0))
    except Exception as e:
        bot_data['logs'].append(f"[ERROR] Failed to update balance: {str(e)}")

def update_positions():
    """Update active positions"""
    global bot_data
    try:
        if client:
            positions = client.futures_account()['positions']
            active_positions = []
            for pos in positions:
                if float(pos['positionAmt']) != 0:
                    active_positions.append({
                        'symbol': pos['symbol'],
                        'amount': float(pos['positionAmt']),
                        'entry_price': float(pos['entryPrice']),
                        'pnl': float(pos['unrealizedProfit']),
                        'side': 'LONG' if float(pos['positionAmt']) > 0 else 'SHORT'
                    })
            bot_data['active_trades'] = active_positions
    except Exception as e:
        bot_data['logs'].append(f"[ERROR] Failed to update positions: {str(e)}")

def update_trade_stats():
    """Update trade statistics from database"""
    global bot_data
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
        bot_data['logs'].append(f"[ERROR] Failed to update trade stats: {str(e)}")

def monitor_bot_logs():
    """Monitor bot log file for updates"""
    global bot_data, log_file_offset

    try:
        if not LOG_PATH.exists():
            return

        file_size = LOG_PATH.stat().st_size
        if file_size < log_file_offset:
            log_file_offset = 0

        with open(LOG_PATH, 'r', encoding='utf-8', errors='ignore') as f:
            f.seek(log_file_offset)
            for line in f:
                if line.strip():
                    bot_data['logs'].append(line.strip())
            log_file_offset = f.tell()
    except Exception as e:
        bot_data['logs'].append(f"[ERROR] Failed to read log file: {str(e)}")

def update_spy_regime():
    """Load the cached SPY regime used by the live bot."""
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
            update_balance()
            update_positions()
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
            bot_process = subprocess.Popen(
                [sys.executable, "main.py"],
                cwd=BOT_DIR,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                text=True,
            )

            bot_data['logs'].append("[INFO] Trading bot started")
            return True
    except Exception as e:
        bot_data['logs'].append(f"[ERROR] Failed to start bot: {str(e)}")
        return False

@app.route('/')
def index():
    """Serve the main page"""
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/data')
def get_data():
    """API endpoint for bot data"""
    # Convert deque to list for JSON serialization
    data = bot_data.copy()
    data['logs'] = list(bot_data['logs'])
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
            bot_data['logs'].append("[INFO] Trading bot stopped")
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
            --red: #ff8b8b;
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
            background: rgba(143, 207, 140, 0.12);
            border-color: rgba(143, 207, 140, 0.22);
        }

        .regime-short {
            color: #FF4B4B;
            background: rgba(255, 75, 75, 0.14);
            border-color: rgba(255, 75, 75, 0.24);
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
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 16px 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
        }

        .position:last-child {
            border-bottom: none;
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
        }

        .side-long {
            background: rgba(143, 207, 140, 0.14);
            color: var(--green);
        }

        .side-short {
            background: rgba(255, 75, 75, 0.2);
            color: #FF4B4B;
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
        }

        .modal-panel {
            max-width: 580px;
            margin: 64px auto;
            background: linear-gradient(180deg, rgba(18, 22, 28, 0.98), rgba(11, 15, 20, 0.98));
            border: 1px solid var(--border);
            border-radius: 28px;
            padding: 24px;
            box-shadow: var(--shadow);
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
            margin-bottom: 18px;
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
                padding: 16px;
            }

            .modal-panel {
                margin: 24px auto;
                padding: 18px;
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
                    <div class="empty-state loading">Loading daily bias...</div>
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
            <h3 class="modal-title">Copy Trade Followers</h3>
            <div class="modal-copy">
                Add extra Binance Futures testnet accounts and mirror master entries and exits into them.
            </div>

            <input id="f-label" class="form-input" placeholder="Label (e.g. Friend A)" />
            <input id="f-api" class="form-input" placeholder="Testnet API Key" />
            <input id="f-secret" class="form-input" placeholder="Testnet Secret Key" type="password" />

            <div class="modal-actions">
                <button class="btn btn-start btn-compact" onclick="addFollower()">Add Follower</button>
                <button class="btn btn-stop btn-compact" onclick="closeCopyModal()">Close</button>
            </div>

            <div id="followers-list" class="followers-list"></div>
        </div>
    </div>


    <script>
        let pendingBotAction = null;

        function formatNumber(num) {
            if (num >= 1000) {
                return (num / 1000).toFixed(1) + 'k';
            }
            return num.toFixed(2);
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
                        Every new crypto entry must agree with this SPY direction until the next daily refresh.
                    </div>
                    <div class="detail-grid">
                        <div class="detail-card">
                            <div class="detail-key">As of</div>
                            <div class="detail-value">${spyRegime.as_of_date || 'Unknown'}</div>
                        </div>
                        <div class="detail-card">
                            <div class="detail-key">Predicting for</div>
                            <div class="detail-value">${spyRegime.predicting_for || 'Unknown'}</div>
                        </div>
                        <div class="detail-card">
                            <div class="detail-key">Data source</div>
                            <div class="detail-value">${spyRegime.market_data_source || 'Unknown'}</div>
                        </div>
                        <div class="detail-card">
                            <div class="detail-key">Cache status</div>
                            <div class="detail-value">${spyRegime.cache_status || 'Unknown'}</div>
                        </div>
                    </div>
                    <div>
                        <div class="detail-key">Lagging external features</div>
                        <div class="pill-row">${pills}</div>
                    </div>
                </div>
            `;
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

                // Update positions
                const positionsEl = document.getElementById('positions-list');
                if (data.active_trades.length > 0) {
                    positionsEl.innerHTML = data.active_trades.map(pos => `
                        <div class="position">
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
                        log.replace(/\\u[\\dA-F]{4}/gi, '').replace(/\\U[\\dA-F]{8}/gi, '')
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
        bot_data['logs'].append("[INFO] Binance client initialized successfully")
    else:
        bot_data['logs'].append("[ERROR] Failed to initialize Binance client")

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
