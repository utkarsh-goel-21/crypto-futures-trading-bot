"""
BINANCE API CREDENTIALS
=======================
Add your API keys here
"""

from environment import USE_TESTNET

if USE_TESTNET:
    # TESTNET credentials (from testnet.binancefuture.com)
    BINANCE_API_KEY = 'YOUR_TESTNET_API_KEY'
    BINANCE_API_SECRET = 'YOUR_TESTNET_API_SECRET'
else:
    # MAINNET credentials (from binance.com)
    BINANCE_API_KEY = 'YOUR_MAINNET_API_KEY'
    BINANCE_API_SECRET = 'YOUR_MAINNET_API_SECRET'