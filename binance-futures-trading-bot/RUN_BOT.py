#!/usr/bin/env python3
"""
================================
BINANCE TRADING BOT LAUNCHER
================================
Single command to run the bot with web monitoring
"""

import os
import sys
import subprocess
import time
import webbrowser
import socket
from pathlib import Path

from runtime_config import TESTNET_BASE_URL, TESTNET_FUTURES_URL, get_binance_credentials

def check_port(port):
    """Check if a port is available"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('127.0.0.1', port))
    sock.close()
    return result != 0

def print_banner():
    """Print startup banner"""
    print("\n" + "=" * 70)
    print(" " * 20 + "BINANCE TRADING BOT")
    print(" " * 15 + "with Web Monitoring Interface")
    print("=" * 70)

def check_requirements():
    """Check if all requirements are installed"""
    print("\n📋 Checking requirements...")

    try:
        import binance
        print("  ✅ python-binance installed")
    except ImportError:
        print("  ❌ python-binance not found")
        print("     Installing requirements...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])

    try:
        import flask
        print("  ✅ Flask installed")
    except ImportError:
        print("  ❌ Flask not found")
        print("     Installing Flask...")
        subprocess.run([sys.executable, "-m", "pip", "install", "flask", "flask-cors"])

def check_api_keys():
    """Verify API keys are configured"""
    print("\n🔑 Checking API configuration...")

    # Add trading-bot to path
    bot_dir = Path(__file__).parent / 'trading-bot'
    sys.path.insert(0, str(bot_dir))

    from environment import USE_TESTNET

    if USE_TESTNET:
        try:
            testnet_api_key, testnet_secret_key = get_binance_credentials(USE_TESTNET)
            if testnet_api_key and testnet_secret_key and 'YOUR_' not in testnet_api_key:
                print(f"  ✅ Testnet API keys configured")
                print(f"     Key: {testnet_api_key[:10]}...")
                return True
            else:
                print("  ❌ Testnet API keys not configured!")
                print("     Set BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_SECRET_KEY")
                return False
        except Exception as exc:
            print(f"  ❌ Testnet credentials not available: {exc}")
            return False
    else:
        print("  ⚠️  WARNING: Running in MAINNET mode!")
        return True

def test_binance_connection():
    """Quick connection test"""
    print("\n🌐 Testing Binance connection...")

    bot_dir = Path(__file__).parent / 'trading-bot'
    sys.path.insert(0, str(bot_dir))

    try:
        from binance.client import Client
        from environment import USE_TESTNET
        api_key, secret_key = get_binance_credentials(USE_TESTNET)

        client = Client(api_key, secret_key, testnet=True)
        client.API_URL = TESTNET_BASE_URL
        client.FUTURES_URL = TESTNET_FUTURES_URL

        # Quick ping test
        client.ping()
        print("  ✅ Connected to Binance Testnet")

        # Get balance
        account = client.futures_account()
        balance = float(account['totalWalletBalance'])
        print(f"  💰 Balance: {balance:.2f} USDT")

        if balance == 0:
            print("\n  ⚠️  WARNING: Your testnet balance is 0!")
            print("     Visit https://testnet.binancefuture.com")
            print("     Login and use the Faucet to get test USDT")
            print()

        return True
    except Exception as e:
        print(f"  ❌ Connection failed: {str(e)[:100]}")
        return False

def start_bot():
    """Start the bot with web interface"""
    print("\n🚀 Starting Trading Bot...")

    # Change to bot directory
    bot_dir = Path(__file__).parent / 'trading-bot'
    os.chdir(bot_dir)

    # Check if port is available
    if not check_port(5000):
        print("  ⚠️  Port 5000 is already in use!")
        print("     Another instance might be running")
        response = input("  Continue anyway? (y/n): ")
        if response.lower() != 'y':
            return False

    print(f"  📂 Working directory: {os.getcwd()}")
    print("  🌐 Web interface will be at: http://localhost:5000")
    print()

    # Start the web server
    try:
        if os.name == 'nt':  # Windows
            # Open browser after a delay
            print("  Opening browser in 5 seconds...")
            subprocess.Popen(['timeout', '/t', '5', '/nobreak', '>nul', '&&', 'start', 'http://localhost:5000'], shell=True)
        else:  # Linux/Mac
            subprocess.Popen(['sh', '-c', 'sleep 5 && xdg-open http://localhost:5000 || open http://localhost:5000'], shell=False)

        # Run the web server
        subprocess.run([sys.executable, 'web_server.py'])
    except KeyboardInterrupt:
        print("\n\n🛑 Bot stopped by user")
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return False

    return True

def main():
    """Main launcher function"""
    print_banner()

    # Run checks
    check_requirements()

    if not check_api_keys():
        print("\n❌ Please configure your API keys first!")
        print("\nSet BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_SECRET_KEY")
        print("or keep a local trading-bot/apikey_testnet.py for local runs")
        sys.exit(1)

    if not test_binance_connection():
        print("\n❌ Cannot connect to Binance!")
        print("Please check your API keys and internet connection")
        sys.exit(1)

    # Show instructions
    print("\n" + "=" * 70)
    print("📌 INSTRUCTIONS:")
    print("  1. Bot will start automatically")
    print("  2. Web interface opens at http://localhost:5000")
    print("  3. Monitor your trades in real-time")
    print("  4. Press Ctrl+C to stop the bot")
    print("=" * 70)

    # Start the bot
    if not start_bot():
        print("\n❌ Failed to start bot")
        sys.exit(1)

    print("\n✅ Bot shutdown complete")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        sys.exit(1)
