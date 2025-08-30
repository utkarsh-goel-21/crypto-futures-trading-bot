"""
ENVIRONMENT CONFIGURATION
=========================
Toggle between TESTNET and MAINNET here
"""

# ============================================
# CHANGE THIS TO SWITCH BETWEEN TESTNET AND MAINNET
# ============================================
USE_TESTNET = True  # Set to False for real trading

# Display current mode
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

if USE_TESTNET:
    print("="*60)
    print("TESTNET MODE ACTIVE - USING FAKE MONEY")
    print("URL: https://testnet.binancefuture.com")
    print("Get test funds from Faucet menu")
    print("="*60)
else:
    print("="*60)
    print("WARNING: MAINNET MODE ACTIVE - USING REAL MONEY")
    print("URL: https://www.binance.com")
    print("Real funds will be used!")
    print("="*60)