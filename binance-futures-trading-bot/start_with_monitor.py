#!/usr/bin/env python3
"""
Start the trading bot with web monitoring interface
This script starts the web server which automatically starts the bot
"""

import os
import sys
import time
import subprocess

def main():
    print("="*60)
    print("STARTING TRADING BOT WITH WEB MONITOR")
    print("="*60)

    # Change to trading-bot directory
    bot_dir = os.path.join(os.path.dirname(__file__), 'trading-bot')
    os.chdir(bot_dir)

    print(f"Working directory: {os.getcwd()}")
    print("Starting web server on http://localhost:5000")
    print("-"*60)

    try:
        # Start the web server (which will start the bot)
        subprocess.run([sys.executable, 'web_server.py'])
    except KeyboardInterrupt:
        print("\n" + "="*60)
        print("Shutting down trading bot and web monitor")
        print("="*60)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()