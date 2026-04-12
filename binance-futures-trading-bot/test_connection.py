#!/usr/bin/env python3
"""
Test Binance Testnet Connection
Verifies API keys and connection before running the bot
"""

import os
import sys

# Add trading-bot to path
bot_dir = os.path.join(os.path.dirname(__file__), 'trading-bot')
sys.path.insert(0, bot_dir)

from binance.client import Client
from binance.exceptions import BinanceAPIException
from environment import USE_TESTNET
from runtime_config import TESTNET_BASE_URL, TESTNET_FUTURES_URL, get_binance_credentials

def test_connection():
    print("=" * 60)
    print("TESTING BINANCE TESTNET CONNECTION")
    print("=" * 60)

    try:
        # Initialize client
        print("Initializing Binance client...")
        api_key, secret_key = get_binance_credentials(USE_TESTNET)

        if USE_TESTNET:
            client = Client(api_key, secret_key, testnet=True)
            client.API_URL = TESTNET_BASE_URL
            client.FUTURES_URL = TESTNET_FUTURES_URL
            print("Using TESTNET")
        else:
            print("ERROR: Not in testnet mode!")
            return False

        # Test connection
        print("\nTesting API connection...")
        server_time = client.get_server_time()
        print(f"✅ Server time: {server_time['serverTime']}")

        # Get account info
        print("\nGetting account info...")
        account = client.futures_account()

        print(f"✅ Account balance: {account['totalWalletBalance']} USDT")
        print(f"✅ Available balance: {account['availableBalance']} USDT")

        # Check if balance is zero
        if float(account['totalWalletBalance']) == 0:
            print("\n⚠️  WARNING: Balance is 0")
            print("   Go to https://testnet.binancefuture.com")
            print("   Login and use the Faucet to get test USDT")

        # Get active positions
        print("\nActive positions:")
        positions = [p for p in account['positions'] if float(p['positionAmt']) != 0]
        if positions:
            for pos in positions:
                print(f"  - {pos['symbol']}: {pos['positionAmt']}")
        else:
            print("  No active positions")

        print("\n" + "=" * 60)
        print("✅ CONNECTION TEST SUCCESSFUL!")
        print("Bot is ready to run on testnet")
        print("=" * 60)
        return True

    except BinanceAPIException as e:
        print(f"\n❌ API Error: {e}")
        print("Check your API keys and permissions")
        return False
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return False

if __name__ == "__main__":
    test_connection()
