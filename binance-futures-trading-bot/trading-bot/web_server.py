"""
Web server for trading bot monitoring
Provides API endpoints and serves frontend
"""

from flask import Flask, jsonify, render_template_string, send_from_directory
from flask_cors import CORS
import json
import sqlite3
import os
import threading
import time
from datetime import datetime, timedelta
from collections import deque
import subprocess
import sys

from flask import Flask, jsonify, render_template_string, send_from_directory, request
from copy_trader import (
    list_followers, add_follower, toggle_follower, delete_follower
)


# Import bot configuration
from environment import USE_TESTNET

if USE_TESTNET:
    from apikey_testnet import testnet_api_key as api_key, testnet_secret_key as secret_key
else:
    from apikey import api_key, secret_key

from binance.client import Client
from binance.exceptions import BinanceAPIException

app = Flask(__name__)
CORS(app)

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
    'last_update': None
}

# Binance client
client = None
bot_process = None

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
        # Check if database exists
        db_file = 'trades.db'
        if os.path.exists(db_file):
            conn = sqlite3.connect(db_file)
            cursor = conn.cursor()

            # Get total trades
            cursor.execute("SELECT COUNT(*) FROM trades WHERE exit_time IS NOT NULL")
            total = cursor.fetchone()
            bot_data['total_trades'] = total[0] if total else 0

            # Get wins
            cursor.execute("SELECT COUNT(*) FROM trades WHERE exit_time IS NOT NULL AND profit > 0")
            wins = cursor.fetchone()
            bot_data['wins'] = wins[0] if wins else 0

            # Get losses
            cursor.execute("SELECT COUNT(*) FROM trades WHERE exit_time IS NOT NULL AND profit <= 0")
            losses = cursor.fetchone()
            bot_data['losses'] = losses[0] if losses else 0

            # Calculate win rate
            if bot_data['total_trades'] > 0:
                bot_data['win_rate'] = (bot_data['wins'] / bot_data['total_trades']) * 100

            conn.close()
    except Exception as e:
        bot_data['logs'].append(f"[ERROR] Failed to update trade stats: {str(e)}")

def monitor_bot_logs():
    """Monitor bot log file for updates"""
    global bot_data
    log_file = 'trading_log.txt'

    try:
        if os.path.exists(log_file):
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                # Read last 100 lines
                lines = f.readlines()
                for line in lines[-100:]:
                    if line.strip():
                        bot_data['logs'].append(line.strip())
    except Exception as e:
        bot_data['logs'].append(f"[ERROR] Failed to read log file: {str(e)}")

def update_loop():
    """Background thread to update data"""
    while True:
        try:
            update_balance()
            update_positions()
            update_trade_stats()
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
            # Start the bot as a subprocess
            bot_process = subprocess.Popen(
                [sys.executable, "main.py"],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )

            # Start a thread to read bot output
            def read_output():
                for line in bot_process.stdout:
                    bot_data['logs'].append(line.strip())

            output_thread = threading.Thread(target=read_output, daemon=True)
            output_thread.start()

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
    <link href="https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700;800;900&display=swap" rel="stylesheet">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Nunito', -apple-system, BlinkMacSystemFont, sans-serif;
            background: #0f0f0f;
            min-height: 100vh;
            color: #ffffff;
            padding: 24px;
            letter-spacing: -0.02em;
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
        }

        .header {
            margin-bottom: 48px;
        }

        .title {
            font-size: 48px;
            font-weight: 800;
            margin-bottom: 8px;
        }

        .title-text {
            background: linear-gradient(135deg, #58CC02 0%, #89E219 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            display: inline;
        }

        .subtitle {
            font-size: 16px;
            color: #777;
            font-weight: 600;
        }

        .status {
            display: inline-block;
            padding: 8px 20px;
            border-radius: 100px;
            font-size: 12px;
            font-weight: 900;
            margin-left: 16px;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            border: 2px solid;
        }

        .status-running {
            background: rgba(88, 204, 2, 0.15);
            color: #58CC02;
            border-color: #58CC02;
            box-shadow: 0 0 20px rgba(88, 204, 2, 0.3);
        }

        .status-stopped {
            background: rgba(255, 75, 75, 0.15);
            color: #FF4B4B !important;
            border-color: #FF4B4B;
            -webkit-text-fill-color: #FF4B4B !important;
        }

        .metrics {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 16px;
            margin-bottom: 32px;
        }

        .metric {
            background: #1c1c1c;
            border: 1px solid #2a2a2a;
            border-radius: 16px;
            padding: 20px;
            transition: all 0.2s ease;
        }

        .metric:hover {
            background: #222;
            border-color: #333;
        }

        .metric-label {
            font-size: 12px;
            color: #777;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 8px;
            font-weight: 700;
        }

        .metric-value {
            font-size: 32px;
            font-weight: 800;
            line-height: 1;
        }

        .metric-value.green {
            color: #58CC02;
        }

        .metric-value.red {
            color: #FF4B4B;
        }

        .positions {
            background: #1c1c1c;
            border: 1px solid #2a2a2a;
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 24px;
        }

        .section-header {
            font-size: 20px;
            font-weight: 800;
            margin-bottom: 20px;
            color: #fff;
        }

        .position {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 16px 0;
            border-bottom: 1px solid #2a2a2a;
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
            color: #fff;
        }

        .position-side {
            padding: 4px 12px;
            border-radius: 100px;
            font-size: 11px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }

        .side-long {
            background: rgba(88, 204, 2, 0.2);
            color: #58CC02;
        }

        .side-short {
            background: rgba(255, 75, 75, 0.2);
            color: #FF4B4B;
        }

        .position-details {
            text-align: right;
        }

        .entry-price {
            font-size: 14px;
            color: #777;
            margin-bottom: 4px;
        }

        .position-pnl {
            font-size: 16px;
            font-weight: 800;
        }

        .logs {
            background: #1c1c1c;
            border: 1px solid #2a2a2a;
            border-radius: 16px;
            padding: 24px;
        }

        .log-container {
            max-height: 280px;
            overflow-y: auto;
            font-family: 'SF Mono', 'Monaco', 'Inconsolata', monospace;
            font-size: 13px;
            line-height: 1.8;
            color: #999;
        }

        .log-container::-webkit-scrollbar {
            width: 8px;
        }

        .log-container::-webkit-scrollbar-track {
            background: #1c1c1c;
            border-radius: 4px;
        }

        .log-container::-webkit-scrollbar-thumb {
            background: #333;
            border-radius: 4px;
        }

        .log-container::-webkit-scrollbar-thumb:hover {
            background: #444;
        }

        .log-line {
            padding: 2px 0;
            word-wrap: break-word;
        }

        .controls {
            position: fixed;
            bottom: 32px;
            right: 32px;
            display: flex;
            gap: 12px;
        }

        .btn {
            padding: 14px 32px;
            border: none;
            border-radius: 100px;
            font-size: 16px;
            font-weight: 800;
            cursor: pointer;
            transition: all 0.2s ease;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            font-family: 'Nunito', sans-serif;
        }

        .btn-start {
            background: #58CC02;
            color: #0f0f0f;
        }

        .btn-start:hover {
            background: #89E219;
            transform: scale(1.05);
        }

        .btn-stop {
            background: #2a2a2a;
            color: #fff;
        }

        .btn-stop:hover {
            background: #FF4B4B;
            transform: scale(1.05);
        }

        .empty-state {
            text-align: center;
            padding: 40px;
            color: #555;
            font-size: 14px;
            font-weight: 600;
        }

        .last-update {
            position: fixed;
            bottom: 32px;
            left: 32px;
            font-size: 12px;
            color: #555;
            font-weight: 600;
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
            background: #2d7ff9;
            color: #fff;
        }
        .btn-copy:hover {
            background: #4e95ff;
            transform: scale(1.05);
        }

    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="title">
                <span class="title-text">Trading Bot</span>
                <span class="status" id="status">Loading</span>
            </div>
            <div class="subtitle">Binance Futures • Testnet Mode</div>
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

        <div class="positions">
            <div class="section-header">Active Positions</div>
            <div id="positions-list">
                <div class="empty-state">No active positions</div>
            </div>
        </div>

        <div class="logs">
            <div class="section-header">Terminal</div>
            <div class="log-container" id="logs">
                <div class="empty-state loading">Initializing...</div>
            </div>
        </div>
    </div>

    <div class="controls">
        <button class="btn btn-stop" onclick="stopBot()">Stop</button>
        <button class="btn btn-copy" onclick="openCopyModal()">Copy Trade Bot</button>
        <button class="btn btn-start" onclick="startBot()">Start</button>
        
    </div>

    <div class="last-update" id="last-update">
        Last update: Never
    </div>
    <div id="copy-modal" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,0.6); z-index:9999;">
        <div style="max-width:560px; margin:80px auto; background:#1c1c1c; border:1px solid #2a2a2a; border-radius:16px; padding:20px;">
            <h3 style="margin-bottom:14px;">Copy Trade Followers (Testnet)</h3>

            <input id="f-label" placeholder="Label (e.g. Friend A)" style="width:100%; margin-bottom:10px; padding:10px; border-radius:10px; border:1px solid #333; background:#111; color:#fff;" />
            <input id="f-api" placeholder="Testnet API Key" style="width:100%; margin-bottom:10px; padding:10px; border-radius:10px; border:1px solid #333; background:#111; color:#fff;" />
            <input id="f-secret" placeholder="Testnet Secret Key" type="password" style="width:100%; margin-bottom:10px; padding:10px; border-radius:10px; border:1px solid #333; background:#111; color:#fff;" />

            <div style="display:flex; gap:8px; margin-bottom:12px;">
                <button class="btn btn-start" style="padding:10px 16px; font-size:13px;" onclick="addFollower()">Add Follower</button>
                <button class="btn btn-stop" style="padding:10px 16px; font-size:13px;" onclick="closeCopyModal()">Close</button>
            </div>

            <div id="followers-list" style="max-height:280px; overflow:auto; border-top:1px solid #2a2a2a; padding-top:12px;"></div>
        </div>
</div>


    <script>
        function formatNumber(num) {
            if (num >= 1000) {
                return (num / 1000).toFixed(1) + 'k';
            }
            return num.toFixed(2);
        }

        async function fetchData() {
            try {
                const response = await fetch('/api/data');
                const data = await response.json();

                // Update status
                const statusEl = document.getElementById('status');
                statusEl.textContent = data.status;
                statusEl.className = `status ${data.status === 'Running' ? 'status-running' : 'status-stopped'}`;

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
                        log.replace(/\\u[\dA-F]{4}/gi, '').replace(/\\U[\dA-F]{8}/gi, '')
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
            try {
                const response = await fetch('/api/start', { method: 'POST' });
                const result = await response.json();
                if (!result.success) {
                    console.error('Failed to start bot:', result.message);
                }
            } catch (error) {
                console.error('Error starting bot:', error);
            }
        }

        async function stopBot() {
            try {
                const response = await fetch('/api/stop', { method: 'POST' });
                const result = await response.json();
                if (!result.success) {
                    console.error('Failed to stop bot:', result.message);
                }
            } catch (error) {
                console.error('Error stopping bot:', error);
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
                <div style="display:flex; justify-content:space-between; align-items:center; padding:8px 0; border-bottom:1px solid #2a2a2a;">
                    <div>
                        <div style="font-weight:700;">${f.label}</div>
                        <div style="font-size:12px; color:#888;">${f.api_key} • ${f.active ? 'ACTIVE' : 'PAUSED'}</div>
                    </div>
                    <div style="display:flex; gap:8px;">
                        <button class="btn btn-copy" style="padding:8px 12px; font-size:11px;" onclick="toggleFollower('${f.id}')">${f.active ? 'Pause' : 'Resume'}</button>
                        <button class="btn btn-stop" style="padding:8px 12px; font-size:11px;" onclick="deleteFollower('${f.id}')">Remove</button>
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