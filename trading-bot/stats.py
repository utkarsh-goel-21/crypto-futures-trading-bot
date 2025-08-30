"""
STATISTICS TRACKER MODULE - UTC VERSION
======================================
Tracks bot performance without revealing sensitive data
Uses UTC timestamps consistently with the rest of the bot
"""

import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from config import DATABASE_FILE

logger = logging.getLogger(__name__)

class StatsTracker:
    """Track and calculate bot statistics for public display"""
    
    def __init__(self, position_tracker=None):
        self.position_tracker = position_tracker
    
    def update_position_tracker(self, position_tracker):
        """Update reference to position tracker"""
        self.position_tracker = position_tracker
    
    def get_stats(self, days=30):
        """Get statistics without sensitive financial data"""
        try:
            conn = sqlite3.connect(DATABASE_FILE)
            cursor = conn.cursor()
            
            # Ensure table exists (updated schema without DEFAULT)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME,
                    coin TEXT,
                    side TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    pnl_pct REAL,
                    pnl_value REAL,
                    exit_type TEXT
                )
            ''')
            
            # Get trades from last N days in UTC
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            cursor.execute('''
                SELECT * FROM trades 
                WHERE timestamp > ? 
                ORDER BY timestamp DESC
            ''', (cutoff.strftime('%Y-%m-%d %H:%M:%S'),))
            
            trades = cursor.fetchall()
            conn.close()
            
            if not trades:
                return {
                    'total_trades': 0,
                    'wins': 0,
                    'losses': 0,
                    'win_rate': 0,
                    'today_trades': 0,
                    'today_wins': 0,
                    'today_losses': 0,
                    'today_win_rate': 0,
                    'best_streak': 0,
                    'current_streak': 0
                }
            
            # Calculate statistics
            total_trades = len(trades)
            wins = [t for t in trades if t[6] > 0]  # pnl_value > 0
            losses = [t for t in trades if t[6] <= 0]
            
            win_count = len(wins)
            loss_count = len(losses)
            win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0
            
            # Today's stats in UTC
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            today_trades = []
            
            for trade in trades:
                try:
                    # Parse UTC timestamp from database
                    trade_time = datetime.strptime(trade[1], '%Y-%m-%d %H:%M:%S')
                    # Add UTC timezone info
                    trade_time = trade_time.replace(tzinfo=timezone.utc)
                    if trade_time > today_start:
                        today_trades.append(trade)
                except Exception as e:
                    logger.warning(f"Could not parse timestamp: {trade[1]} - {e}")
            
            today_wins = len([t for t in today_trades if t[6] > 0])
            today_losses = len([t for t in today_trades if t[6] <= 0])
            today_win_rate = (today_wins / len(today_trades) * 100) if today_trades else 0
            
            # Calculate streaks
            best_streak = 0
            current_streak = 0
            temp_streak = 0
            
            for trade in reversed(trades):  # Go from oldest to newest
                if trade[6] > 0:  # Win
                    temp_streak += 1
                    best_streak = max(best_streak, temp_streak)
                else:  # Loss
                    temp_streak = 0
            
            # Current streak (from most recent trades)
            for trade in trades:  # From newest to oldest
                if trade[6] > 0:  # Win
                    current_streak += 1
                else:
                    break
            
            return {
                'total_trades': total_trades,
                'wins': win_count,
                'losses': loss_count,
                'win_rate': win_rate,
                'today_trades': len(today_trades),
                'today_wins': today_wins,
                'today_losses': today_losses,
                'today_win_rate': today_win_rate,
                'best_streak': best_streak,
                'current_streak': current_streak
            }
            
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def format_daily_discord_summary(self):
        """Format daily summary for Discord - UTC VERSION"""
        stats = self.get_stats(30)
        
        if not stats:
            return None
        
        # Use UTC time consistently
        date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")
        time_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
        
        # Count open positions
        open_positions = 0
        open_coins = []
        if self.position_tracker:
            positions = self.position_tracker.positions
            open_positions = len(positions)
            open_coins = [coin.replace('USDC', '') for coin in positions.keys()]
        
        # Determine performance level for CLOSED trades
        if stats['today_trades'] == 0 and open_positions == 0:
            performance = "⏸️ MONITORING"
            emoji = "📊"
        elif stats['today_trades'] == 0 and open_positions > 0:
            performance = "🔄 POSITIONS OPEN"
            emoji = "🟡"
        elif stats['today_win_rate'] >= 70:
            performance = "🔥 EXCELLENT"
            emoji = "🟢"
        elif stats['today_win_rate'] >= 60:
            performance = "✅ PROFITABLE"
            emoji = "🟢"
        elif stats['today_win_rate'] >= 50:
            performance = "📈 POSITIVE"
            emoji = "🟡"
        else:
            performance = "📉 CHALLENGING"
            emoji = "🔴"
        
        # Build summary
        summary = f"""📊 **DAILY PERFORMANCE - {date_str}**
⏰ **Report Time: {time_str}**
═══════════════════════════════

**Today's Completed Trades (UTC):**
• Closed Positions: {stats['today_trades']}
• Wins: {stats['today_wins']} | Losses: {stats['today_losses']}
• Win Rate: {stats['today_win_rate']:.1f}% {f"(of closed)" if stats['today_trades'] > 0 else ""}

**Currently Open:**
• Active Positions: {open_positions} {f"({', '.join(open_coins)})" if open_coins else ""}
• Status: {performance}

**30-Day Performance:**
• Total Closed Trades: {stats['total_trades']}
• Overall Win Rate: {stats['win_rate']:.1f}%
• Best Win Streak: {stats['best_streak']} trades
• Current Streak: {stats['current_streak']} {"wins 🔥" if stats['current_streak'] > 0 else ""}

{emoji} **Bot Status: {"ACTIVE - " + str(open_positions) + " POSITIONS RUNNING" if open_positions > 0 else "ACTIVE & MONITORING"}**

_Note: All times in UTC (Exchange Time)_
_Win rate calculated on closed positions only_"""
        
        # Debug logging
        logger.info(f"Stats Debug - Total: {stats['total_trades']}, Today: {stats['today_trades']}, Open: {open_positions}")
        
        return summary

# Global instance
stats_tracker = StatsTracker()