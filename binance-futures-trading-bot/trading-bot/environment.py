"""
ENVIRONMENT CONFIGURATION
=========================
Toggle between TESTNET and MAINNET here
"""

import io
import os
import sys


def _get_bool_env(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

# ============================================
# CHANGE THIS TO SWITCH BETWEEN TESTNET AND MAINNET
# ============================================
USE_TESTNET = _get_bool_env("USE_TESTNET", True)  # Set to False for real trading

# Display current mode
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
