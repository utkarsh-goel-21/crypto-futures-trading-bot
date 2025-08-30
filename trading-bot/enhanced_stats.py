"""
ENHANCED STATS TRACKING SYSTEM
==============================
Tracks detailed statistics per coin and overall
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple
import logging

logger = logging.getLogger(__name__)

class EnhancedStatsTracker:
    """Track detailed statistics for each coin and overall performance"""
    
    def __init__(self, database_file='bot_trades.db'):
        self.database_file = database_file
        self.current_session_stats = {
            'overall': {'wins': 0, 'losses': 0, 'total': 0, 'pnl': 0.0},
            'by_coin': {}
        }
        self.init_database()
        
    def init_database(self):
        """Initialize or upgrade database schema"""
        try:
            conn = sqlite3.connect(self.database_file)
            cursor = conn.cursor()
            
            # Enhanced trades table with more details
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
                    exit_type TEXT,
                    position_size REAL,
                    leverage INTEGER,
                    session_id TEXT
                )
            ''')
            
            # Stats summary table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS stats_summary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME,
                    coin TEXT,
                    total_trades INTEGER,
                    wins INTEGER,
                    losses INTEGER,
                    win_rate REAL,
                    total_pnl REAL,
                    avg_win REAL,
                    avg_loss REAL,
                    best_trade REAL,
                    worst_trade REAL,
                    period TEXT
                )
            ''')
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Database initialization error: {e}")
    
    def record_trade(self, trade_data: Dict):
        """Record a completed trade with enhanced details"""
        try:
            conn = sqlite3.connect(self.database_file)
            cursor = conn.cursor()
            
            # Save to database
            cursor.execute('''
                INSERT INTO trades (
                    timestamp, coin, side, entry_price, exit_price, 
                    pnl_pct, pnl_value, exit_type, position_size, leverage, session_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
                trade_data['coin'],
                trade_data['side'],
                trade_data['entry_price'],
                trade_data['exit_price'],
                trade_data['pnl_pct'],
                trade_data['pnl_value'],
                trade_data['exit_type'],
                trade_data.get('position_size', 1000),
                trade_data.get('leverage', 10),
                trade_data.get('session_id', 'default')
            ))
            
            conn.commit()
            conn.close()
            
            # Update session stats
            self.update_session_stats(trade_data)
            
        except Exception as e:
            logger.error(f"Error recording trade: {e}")
    
    def update_session_stats(self, trade_data: Dict):
        """Update current session statistics"""
        coin = trade_data['coin']
        
        # Initialize coin stats if needed
        if coin not in self.current_session_stats['by_coin']:
            self.current_session_stats['by_coin'][coin] = {
                'wins': 0, 'losses': 0, 'total': 0, 'pnl': 0.0
            }
        
        # Update coin stats
        coin_stats = self.current_session_stats['by_coin'][coin]
        coin_stats['total'] += 1
        coin_stats['pnl'] += trade_data['pnl_value']
        
        if trade_data['pnl_pct'] > 0:
            coin_stats['wins'] += 1
        else:
            coin_stats['losses'] += 1
        
        # Update overall stats
        self.current_session_stats['overall']['total'] += 1
        self.current_session_stats['overall']['pnl'] += trade_data['pnl_value']
        
        if trade_data['pnl_pct'] > 0:
            self.current_session_stats['overall']['wins'] += 1
        else:
            self.current_session_stats['overall']['losses'] += 1
    
    def get_coin_stats(self, coin: str, hours: int = 0) -> Dict:
        """Get statistics for a specific coin"""
        try:
            conn = sqlite3.connect(self.database_file)
            cursor = conn.cursor()
            
            # Build time filter
            if hours > 0:
                time_filter = f"AND timestamp > datetime('now', '-{hours} hours')"
            else:
                time_filter = ""
            
            # Get stats
            query = f'''
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl_pct <= 0 THEN 1 ELSE 0 END) as losses,
                    SUM(pnl_value) as total_pnl,
                    AVG(CASE WHEN pnl_pct > 0 THEN pnl_pct ELSE NULL END) as avg_win,
                    AVG(CASE WHEN pnl_pct <= 0 THEN pnl_pct ELSE NULL END) as avg_loss,
                    MAX(pnl_pct) as best_trade,
                    MIN(pnl_pct) as worst_trade
                FROM trades
                WHERE coin = ? {time_filter}
            '''
            
            cursor.execute(query, (coin,))
            result = cursor.fetchone()
            conn.close()
            
            if result and result[0] > 0:
                total = result[0]
                wins = result[1] or 0
                losses = result[2] or 0
                
                return {
                    'coin': coin,
                    'total_trades': total,
                    'wins': wins,
                    'losses': losses,
                    'win_rate': (wins / total * 100) if total > 0 else 0,
                    'total_pnl': result[3] or 0,
                    'avg_win': result[4] or 0,
                    'avg_loss': result[5] or 0,
                    'best_trade': result[6] or 0,
                    'worst_trade': result[7] or 0
                }
            else:
                return {
                    'coin': coin,
                    'total_trades': 0,
                    'wins': 0,
                    'losses': 0,
                    'win_rate': 0,
                    'total_pnl': 0,
                    'avg_win': 0,
                    'avg_loss': 0,
                    'best_trade': 0,
                    'worst_trade': 0
                }
                
        except Exception as e:
            logger.error(f"Error getting coin stats: {e}")
            return {}
    
    def get_overall_stats(self, hours: int = 0) -> Dict:
        """Get overall statistics across all coins"""
        try:
            conn = sqlite3.connect(self.database_file)
            cursor = conn.cursor()
            
            # Build time filter
            if hours > 0:
                time_filter = f"WHERE timestamp > datetime('now', '-{hours} hours')"
            else:
                time_filter = ""
            
            # Get overall stats
            query = f'''
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl_pct <= 0 THEN 1 ELSE 0 END) as losses,
                    SUM(pnl_value) as total_pnl,
                    AVG(CASE WHEN pnl_pct > 0 THEN pnl_pct ELSE NULL END) as avg_win,
                    AVG(CASE WHEN pnl_pct <= 0 THEN pnl_pct ELSE NULL END) as avg_loss,
                    COUNT(DISTINCT coin) as active_coins
                FROM trades
                {time_filter}
            '''
            
            cursor.execute(query)
            result = cursor.fetchone()
            conn.close()
            
            if result and result[0] > 0:
                total = result[0]
                wins = result[1] or 0
                losses = result[2] or 0
                
                return {
                    'total_trades': total,
                    'wins': wins,
                    'losses': losses,
                    'win_rate': (wins / total * 100) if total > 0 else 0,
                    'total_pnl': result[3] or 0,
                    'avg_win': result[4] or 0,
                    'avg_loss': result[5] or 0,
                    'active_coins': result[6] or 0
                }
            else:
                return {
                    'total_trades': 0,
                    'wins': 0,
                    'losses': 0,
                    'win_rate': 0,
                    'total_pnl': 0,
                    'avg_win': 0,
                    'avg_loss': 0,
                    'active_coins': 0
                }
                
        except Exception as e:
            logger.error(f"Error getting overall stats: {e}")
            return {}
    
    def get_all_coins_stats(self, hours: int = 0) -> List[Dict]:
        """Get stats for all coins that have trades"""
        try:
            conn = sqlite3.connect(self.database_file)
            cursor = conn.cursor()
            
            # Get list of coins
            cursor.execute("SELECT DISTINCT coin FROM trades")
            coins = [row[0] for row in cursor.fetchall()]
            conn.close()
            
            # Get stats for each coin
            stats = []
            for coin in coins:
                coin_stats = self.get_coin_stats(coin, hours)
                if coin_stats['total_trades'] > 0:
                    stats.append(coin_stats)
            
            # Sort by win rate
            stats.sort(key=lambda x: x['win_rate'], reverse=True)
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting all coins stats: {e}")
            return []
    
    def format_trade_close_message(self, trade_data: Dict) -> str:
        """Format a detailed message after trade closes"""
        coin = trade_data['coin']
        exit_type = trade_data['exit_type']
        pnl_pct = trade_data['pnl_pct']
        pnl_value = trade_data['pnl_value']
        
        # Get current stats
        coin_stats = self.get_coin_stats(coin)
        coin_stats_24h = self.get_coin_stats(coin, 24)
        overall_stats = self.get_overall_stats()
        overall_stats_24h = self.get_overall_stats(24)
        
        # Build message
        msg = f"{'='*40}\n"
        msg += f"📊 TRADE CLOSED - {coin}\n"
        msg += f"{'='*40}\n\n"
        
        # Trade details
        msg += f"Exit Type: {exit_type} {'✅' if pnl_pct > 0 else '❌'}\n"
        msg += f"P&L: {pnl_pct:.2f}% (${pnl_value:.2f})\n"
        msg += f"Side: {trade_data['side']}\n"
        msg += f"Entry: ${trade_data['entry_price']:.4f}\n"
        msg += f"Exit: ${trade_data['exit_price']:.4f}\n\n"
        
        # Coin statistics
        msg += f"📈 {coin} STATISTICS:\n"
        msg += f"{'─'*35}\n"
        msg += f"All Time: {coin_stats['wins']}/{coin_stats['total_trades']} "
        msg += f"({coin_stats['win_rate']:.1f}% WR)\n"
        msg += f"24 Hours: {coin_stats_24h['wins']}/{coin_stats_24h['total_trades']} "
        msg += f"({coin_stats_24h['win_rate']:.1f}% WR)\n"
        msg += f"Total P&L: ${coin_stats['total_pnl']:.2f}\n"
        msg += f"Avg Win: {coin_stats['avg_win']:.2f}%\n"
        msg += f"Avg Loss: {coin_stats['avg_loss']:.2f}%\n\n"
        
        # Overall statistics
        msg += f"🌍 OVERALL STATISTICS:\n"
        msg += f"{'─'*35}\n"
        msg += f"All Time: {overall_stats['wins']}/{overall_stats['total_trades']} "
        msg += f"({overall_stats['win_rate']:.1f}% WR)\n"
        msg += f"24 Hours: {overall_stats_24h['wins']}/{overall_stats_24h['total_trades']} "
        msg += f"({overall_stats_24h['win_rate']:.1f}% WR)\n"
        msg += f"Total P&L: ${overall_stats['total_pnl']:.2f}\n"
        msg += f"Active Coins: {overall_stats['active_coins']}\n\n"
        
        # All coins summary
        all_coins = self.get_all_coins_stats()
        if len(all_coins) > 1:
            msg += f"🏆 ALL COINS WIN RATES:\n"
            msg += f"{'─'*35}\n"
            for stats in all_coins[:5]:  # Top 5 coins
                msg += f"{stats['coin']}: {stats['win_rate']:.1f}% "
                msg += f"({stats['wins']}/{stats['total_trades']})\n"
        
        msg += f"\n{'='*40}"
        
        return msg
    
    def format_daily_summary(self) -> str:
        """Format daily summary statistics"""
        overall_24h = self.get_overall_stats(24)
        all_coins_24h = self.get_all_coins_stats(24)
        
        msg = f"{'='*40}\n"
        msg += f"📊 DAILY SUMMARY REPORT\n"
        msg += f"{'='*40}\n\n"
        
        msg += f"📈 24-HOUR PERFORMANCE:\n"
        msg += f"{'─'*35}\n"
        msg += f"Total Trades: {overall_24h['total_trades']}\n"
        msg += f"Wins/Losses: {overall_24h['wins']}/{overall_24h['losses']}\n"
        msg += f"Win Rate: {overall_24h['win_rate']:.1f}%\n"
        msg += f"Total P&L: ${overall_24h['total_pnl']:.2f}\n"
        msg += f"Avg Win: {overall_24h['avg_win']:.2f}%\n"
        msg += f"Avg Loss: {overall_24h['avg_loss']:.2f}%\n\n"
        
        if all_coins_24h:
            msg += f"🏆 COIN PERFORMANCE (24H):\n"
            msg += f"{'─'*35}\n"
            for stats in all_coins_24h:
                msg += f"{stats['coin']}: {stats['win_rate']:.1f}% WR "
                msg += f"({stats['wins']}/{stats['total_trades']}) "
                msg += f"P&L: ${stats['total_pnl']:.2f}\n"
        
        msg += f"\n{'='*40}"
        
        return msg

# Create global instance
enhanced_stats = EnhancedStatsTracker()