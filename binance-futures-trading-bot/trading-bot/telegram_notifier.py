"""
TELEGRAM NOTIFICATION MODULE
===========================
Sends important alerts to your phone
"""

import requests
import logging
from config import TELEGRAM_ENABLED, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

class TelegramNotifier:
    """Send notifications to Telegram"""
    
    def __init__(self):
        self.enabled = TELEGRAM_ENABLED
        self.token = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        
        if self.enabled:
            self.send_message("🤖 Trading Bot Started on AWS!")
    
    def send_message(self, message):
        """Send message to Telegram"""
        if not self.enabled:
            return
        
        try:
            url = f"{self.base_url}/sendMessage"
            data = {
                'chat_id': self.chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            response = requests.post(url, data=data, timeout=5)
            if not response.ok:
                logger.error(f"Telegram send failed: {response.text}")
        except Exception as e:
            logger.error(f"Telegram error: {e}")
    
    def send_trade_alert(self, coin, side, price, reason="Signal"):
        """Send trade entry alert"""
        message = f"📈 <b>NEW POSITION</b>\n"
        message += f"Coin: {coin}\n"
        message += f"Side: {side}\n"
        message += f"Price: ${price:.2f}\n"
        message += f"Reason: {reason}"
        self.send_message(message)
    
    def send_exit_alert(self, coin, exit_type, pnl_pct, pnl_value):
        """Send trade exit alert"""
        emoji = "✅" if pnl_value > 0 else "❌"
        message = f"{emoji} <b>POSITION CLOSED</b>\n"
        message += f"Coin: {coin}\n"
        message += f"Exit: {exit_type}\n"
        message += f"PnL: {pnl_pct:.2f}% (${pnl_value:.2f})"
        self.send_message(message)
    
    def send_error_alert(self, error_msg):
        """Send error alert"""
        message = f"⚠️ <b>ERROR DETECTED</b>\n{error_msg}"
        self.send_message(message)
    
    def send_daily_summary(self, total_pnl, win_rate, num_trades):
        """Send daily summary"""
        message = f"📊 <b>DAILY SUMMARY</b>\n"
        message += f"Total PnL: ${total_pnl:.2f}\n"
        message += f"Win Rate: {win_rate:.1f}%\n"
        message += f"Trades: {num_trades}"
        self.send_message(message)

# Global notifier instance
notifier = TelegramNotifier()