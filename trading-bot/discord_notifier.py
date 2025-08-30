"""
DISCORD NOTIFICATION MODULE
==========================
Sends signals to Discord channel
"""

import requests
import logging
from config import DISCORD_ENABLED, DISCORD_WEBHOOK_URL

logger = logging.getLogger(__name__)

class DiscordNotifier:
    """Send notifications to Discord"""
    
    def __init__(self):
        self.enabled = DISCORD_ENABLED
        self.webhook_url = DISCORD_WEBHOOK_URL
        
        if self.enabled:
            self.send_message("🤖 **Trading Bot Started!**")
    
    def send_message(self, content, embed=None):
        """Send message to Discord"""
        if not self.enabled:
            return
        
        try:
            data = {"content": content}
            if embed:
                data["embeds"] = [embed]
            
            response = requests.post(
                self.webhook_url,
                json=data,
                timeout=5
            )
            
            if not response.ok:
                logger.error(f"Discord send failed: {response.text}")
                
        except Exception as e:
            logger.error(f"Discord error: {e}")
    
    def send_trade_alert(self, coin, side, price, tp_price=None, sl_price=None):
        """Send trade entry alert with embed including TP/SL"""
        # Convert BUY/SELL to LONG/SHORT for clarity
        position_side = "LONG" if side == "BUY" else "SHORT"
        
        # Set color based on position type
        color = 0x00ff00 if side == "BUY" else 0xff0000  # Green for LONG, Red for SHORT
        
        # Build fields
        fields = [
            {"name": "Coin", "value": coin.replace("USDC", ""), "inline": True},
            {"name": "Side", "value": position_side, "inline": True},
            {"name": "Entry", "value": f"${price:.2f}", "inline": True}
        ]
        
        # Add TP and SL if provided
        if tp_price and sl_price:
            fields.extend([
                {"name": "Take Profit", "value": f"${tp_price:.2f}", "inline": True},
                {"name": "Stop Loss", "value": f"${sl_price:.2f}", "inline": True},
                {"name": "Risk/Reward", "value": f"1:{((tp_price-price)/(price-sl_price) if side=='BUY' else (price-tp_price)/(sl_price-price)):.1f}", "inline": True}
            ])
        
        embed = {
            "title": "🚨 NEW TRADING SIGNAL",
            "color": color,
            "fields": fields,
            "footer": {"text": "AI Trading Bot • Manage Risk"}
        }
        
        self.send_message("", embed)
    
    def send_exit_alert(self, coin, exit_type, pnl_pct, pnl_value):
        """Send trade exit alert with embed - NO DOLLAR VALUES"""
        color = 0x00ff00 if pnl_value > 0 else 0xff0000
        emoji = "✅" if pnl_value > 0 else "❌"
        
        # Don't show dollar values, only percentage
        embed = {
            "title": f"{emoji} POSITION CLOSED",
            "color": color,
            "fields": [
                {"name": "Coin", "value": coin.replace("USDC", ""), "inline": True},
                {"name": "Exit Type", "value": exit_type, "inline": True},
                {"name": "Result", "value": f"{pnl_pct:.2f}%", "inline": True}  # Only percentage
            ],
            "footer": {"text": "AI Trading Bot"}
        }
        
        self.send_message("", embed)

# Global instance
discord = DiscordNotifier()