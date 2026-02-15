#!/usr/bin/env python3
"""
Server version of bot launcher (for AWS/Linux servers)
No browser opening, just runs the bot
"""

import os
import sys
import subprocess
from pathlib import Path

def main():
    print("=" * 60)
    print("STARTING BINANCE TRADING BOT (SERVER MODE)")
    print("=" * 60)

    # Change to bot directory
    bot_dir = Path(__file__).parent / 'trading-bot'
    os.chdir(bot_dir)

    print(f"Working directory: {os.getcwd()}")
    print("\nStarting bot with web interface...")
    print("Web interface available at:")
    print("  - http://localhost:5000 (local)")
    print("  - http://[SERVER_IP]:5000 (remote)")
    print("\nPress Ctrl+C to stop\n")
    print("-" * 60)

    try:
        subprocess.run([sys.executable, 'web_server.py'])
    except KeyboardInterrupt:
        print("\n" + "=" * 60)
        print("Bot stopped")
        print("=" * 60)

if __name__ == "__main__":
    main()