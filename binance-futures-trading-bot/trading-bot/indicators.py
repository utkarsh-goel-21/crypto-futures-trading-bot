"""
INDICATOR CALCULATION MODULE
============================
Calculates all indicators based on coin-specific parameters
Uses YOUR EXACT strategy - no modifications
"""

import pandas as pd
import numpy as np
import ta
from datetime import datetime, timedelta,timezone
import logging

logger = logging.getLogger(__name__)

class IndicatorCalculator:
    """Calculate indicators for a specific coin"""
    
    def __init__(self, coin, params):
        self.coin = coin
        self.params = params['parameters']  # Extract parameters section
        self.last_calculation = None
        
    def fetch_historical_data(self, client, timeframe, limit=200):
        """Fetch historical candles from Binance"""
        try:
            # Convert timeframe to Binance format
            interval_map = {
                '1m': client.KLINE_INTERVAL_1MINUTE,
                '5m': client.KLINE_INTERVAL_5MINUTE,
                '15m': client.KLINE_INTERVAL_15MINUTE,
                '30m': client.KLINE_INTERVAL_30MINUTE,
                '1h': client.KLINE_INTERVAL_1HOUR,
                '2h': client.KLINE_INTERVAL_2HOUR,
                '4h': client.KLINE_INTERVAL_4HOUR,
            }
            
            interval = interval_map.get(timeframe, timeframe)
            
            # Fetch klines
            klines = client.futures_klines(
                symbol=self.coin,
                interval=interval,
                limit=limit
            )
            
            # Convert to DataFrame
            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_volume', 'trades', 'taker_buy_vol',
                'taker_buy_quote_vol', 'ignore'
            ])
            
            # Convert types
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = df[col].astype(float)
            
            df.set_index('timestamp', inplace=True)
            
            return df[['open', 'high', 'low', 'close', 'volume']]
            
        except Exception as e:
            logger.error(f"Error fetching data for {self.coin} {timeframe}: {e}")
            return None
    
    def calculate_all_indicators(self, df):
        """Calculate all indicators based on parameters - YOUR EXACT STRATEGY"""
        
        # Get exact parameters as integers where needed
        rsi_period = int(round(self.params['rsi_period']))
        trend_fast_ema = int(round(self.params['trend_fast_ema']))
        trend_slow_ema = int(round(self.params['trend_slow_ema']))
        entry_ema_period = int(round(self.params['entry_ema_period']))
        macd_fast = int(round(self.params['macd_fast']))
        macd_slow = int(round(self.params['macd_slow']))
        macd_signal = int(round(self.params['macd_signal']))
        volume_ma_period = int(round(self.params['volume_ma_period']))
        bollinger_period = int(round(self.params['bollinger_period']))
        bollinger_squeeze_length = int(round(self.params['bollinger_squeeze_length']))
        stochastic_k = int(round(self.params['stochastic_k']))
        stochastic_d = int(round(self.params['stochastic_d']))
        atr_period = int(round(self.params['atr_period']))
        adx_period = int(round(self.params['adx_period']))
        sr_lookback = int(round(self.params['sr_lookback']))
        momentum_period = int(round(self.params['momentum_period']))
        structure_lookback = int(round(self.params['structure_lookback']))
        
        # RSI - if weight > 0.01
        if self.params.get('rsi_weight', 0) > 0.01:
            df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=rsi_period).rsi()
        
        # EMAs for trend - if weight > 0.01
        if self.params.get('trend_ema_weight', 0) > 0.01:
            df['ema_fast'] = df['close'].ewm(span=trend_fast_ema, adjust=False).mean()
            df['ema_slow'] = df['close'].ewm(span=trend_slow_ema, adjust=False).mean()
            df['trend'] = np.where(df['ema_fast'] > df['ema_slow'], 'BULLISH', 'BEARISH')
        
        # Entry EMA - if weight > 0.01
        if self.params.get('price_ema_weight', 0) > 0.01:
            df['entry_ema'] = df['close'].ewm(span=entry_ema_period, adjust=False).mean()
        
        # MACD - if weight > 0.01
        if self.params.get('macd_weight', 0) > 0.01:
            macd = ta.trend.MACD(df['close'], window_slow=macd_slow, window_fast=macd_fast, window_sign=macd_signal)
            df['macd_histogram'] = macd.macd_diff()
            df['macd_histogram_prev'] = df['macd_histogram'].shift(1)
            df['macd_flip_bullish'] = (df['macd_histogram_prev'] < 0) & (df['macd_histogram'] > 0)
            df['macd_flip_bearish'] = (df['macd_histogram_prev'] > 0) & (df['macd_histogram'] < 0)
        
        # Volume - if weight > 0.01
        if self.params.get('volume_spike_weight', 0) > 0.01:
            df['vol_ma'] = df['volume'].rolling(window=volume_ma_period).mean()
            df['vol_spike'] = df['volume'] > (df['vol_ma'] * self.params['volume_spike_multiplier'])
        
        # Bollinger Bands - if weight > 0.01
        if self.params.get('bollinger_weight', 0) > 0.01:
            bb = ta.volatility.BollingerBands(
                close=df['close'], 
                window=bollinger_period, 
                window_dev=self.params['bollinger_std']
            )
            df['bb_upper'] = bb.bollinger_hband()
            df['bb_lower'] = bb.bollinger_lband()
            df['bb_width'] = bb.bollinger_wband()
            
            if self.params.get('bollinger_squeeze_enabled', 0) > 0.5:
                df['bb_squeeze'] = df['bb_width'] == df['bb_width'].rolling(window=bollinger_squeeze_length).min()
        
        # Stochastic - if weight > 0.01
        if self.params.get('stochastic_weight', 0) > 0.01:
            stoch = ta.momentum.StochasticOscillator(
                high=df['high'], low=df['low'], close=df['close'],
                window=stochastic_k, smooth_window=stochastic_d
            )
            df['stoch_k'] = stoch.stoch()
        
        # ATR - if weight > 0.01
        if self.params.get('atr_weight', 0) > 0.01:
            df['atr'] = ta.volatility.AverageTrueRange(
                high=df['high'], low=df['low'], close=df['close'], window=atr_period
            ).average_true_range()
        
        # ADX - if weight > 0.01
        if self.params.get('adx_weight', 0) > 0.01:
            adx = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=adx_period)
            df['adx'] = adx.adx()
        
        # Support/Resistance - if weight > 0.01
        if self.params.get('sr_weight', 0) > 0.01:
            df['resistance'] = df['high'].rolling(window=sr_lookback).max()
            df['support'] = df['low'].rolling(window=sr_lookback).min()
            df['near_resistance'] = abs(df['close'] - df['resistance']) / df['close'] < self.params['sr_touch_distance']
            df['near_support'] = abs(df['close'] - df['support']) / df['close'] < self.params['sr_touch_distance']
        
        # Momentum - if weight > 0.01
        if self.params.get('momentum_weight', 0) > 0.01:
            df['momentum'] = df['close'].pct_change(periods=momentum_period)
        
        # Market Structure - if weight > 0.01
        if self.params.get('market_structure_weight', 0) > 0.01:
            df['highest_high'] = df['high'].rolling(window=structure_lookback).max()
            df['lowest_low'] = df['low'].rolling(window=structure_lookback).min()
            df['highest_high_prev'] = df['highest_high'].shift(structure_lookback)
            df['lowest_low_prev'] = df['lowest_low'].shift(structure_lookback)
            df['market_structure'] = np.where(
                (df['highest_high'] > df['highest_high_prev']) & (df['lowest_low'] > df['lowest_low_prev']), 
                'BULLISH',
                np.where(
                    (df['highest_high'] < df['highest_high_prev']) & (df['lowest_low'] < df['lowest_low_prev']), 
                    'BEARISH', 
                    'NEUTRAL'
                )
            )
        
        return df
    
    def calculate_weighted_signal(self, row, trend_data=None):
        """Calculate weighted signal from all filters - YOUR EXACT FORMULA"""
        weighted_sum = 0.0
        filter_signals = {}
        
        # RSI
        if self.params.get('rsi_weight', 0) > 0.01 and 'rsi' in row:
            rsi = row['rsi']
            oversold = self.params['rsi_oversold']
            overbought = self.params['rsi_overbought']
            
            if rsi < oversold:
                signal = 1.0  # Strong LONG
            elif rsi < oversold + 10:
                signal = 0.5  # Weak LONG
            elif rsi > overbought:
                signal = -1.0  # Strong SHORT
            elif rsi > overbought - 10:
                signal = -0.5  # Weak SHORT
            else:
                signal = 0.0  # Neutral
            
            weight = self.params['rsi_weight']
            weighted_sum += weight * signal
            filter_signals['rsi'] = {'weight': weight, 'signal': signal}
        
        # Trend EMA
        if self.params.get('trend_ema_weight', 0) > 0.01 and 'trend' in row:
            trend = row['trend']
            signal = 1.0 if trend == 'BULLISH' else -1.0 if trend == 'BEARISH' else 0.0
            weight = self.params['trend_ema_weight']
            weighted_sum += weight * signal
            filter_signals['trend_ema'] = {'weight': weight, 'signal': signal}
        
        # Price EMA
        if self.params.get('price_ema_weight', 0) > 0.01 and 'entry_ema' in row:
            price = row['close']
            ema = row['entry_ema']
            distance = (price - ema) / ema
            
            if distance < -0.002:
                signal = 0.8
            elif distance < 0:
                signal = 0.3
            elif distance > 0.002:
                signal = -0.8
            elif distance > 0:
                signal = -0.3
            else:
                signal = 0.0
            
            weight = self.params['price_ema_weight']
            weighted_sum += weight * signal
            filter_signals['price_ema'] = {'weight': weight, 'signal': signal}
        
        # MACD
        if self.params.get('macd_weight', 0) > 0.01:
            if self.params.get('macd_flip_only', 0) > 0.5:
                if 'macd_flip_bullish' in row and row['macd_flip_bullish']:
                    signal = 1.0
                elif 'macd_flip_bearish' in row and row['macd_flip_bearish']:
                    signal = -1.0
                else:
                    signal = 0.0
            else:
                if 'macd_histogram' in row:
                    macd_hist = row['macd_histogram']
                    threshold = self.params.get('macd_histogram_threshold', 0)
                    if macd_hist > threshold:
                        signal = min(macd_hist / 0.002, 1.0)
                    elif macd_hist < -threshold:
                        signal = max(macd_hist / 0.002, -1.0)
                    else:
                        signal = 0.0
                else:
                    signal = 0.0
            
            weight = self.params['macd_weight']
            weighted_sum += weight * signal
            filter_signals['macd'] = {'weight': weight, 'signal': signal}
        
        # Volume Spike
        if self.params.get('volume_spike_weight', 0) > 0.01 and 'vol_spike' in row:
            if row['vol_spike']:
                price_change = (row['close'] - row['open']) / row['open']
                signal = 0.5 if price_change > 0 else -0.5
            else:
                signal = 0.0
            
            weight = self.params['volume_spike_weight']
            weighted_sum += weight * signal
            filter_signals['volume_spike'] = {'weight': weight, 'signal': signal}
        
        # Bollinger Bands
        if self.params.get('bollinger_weight', 0) > 0.01:
            price = row['close']
            if 'bb_lower' in row and 'bb_upper' in row:
                bb_lower = row['bb_lower']
                bb_upper = row['bb_upper']
                
                if self.params.get('bollinger_squeeze_enabled', 0) > 0.5:
                    squeeze = row.get('bb_squeeze', False)
                    if squeeze:
                        if price <= bb_lower:
                            signal = 1.0
                        elif price >= bb_upper:
                            signal = -1.0
                        else:
                            signal = 0.0
                    else:
                        signal = 0.0
                else:
                    if price <= bb_lower:
                        signal = 0.8
                    elif price >= bb_upper:
                        signal = -0.8
                    else:
                        signal = 0.0
            else:
                signal = 0.0
            
            weight = self.params['bollinger_weight']
            weighted_sum += weight * signal
            filter_signals['bollinger'] = {'weight': weight, 'signal': signal}
        
        # Stochastic
        if self.params.get('stochastic_weight', 0) > 0.01 and 'stoch_k' in row:
            stoch_k = row['stoch_k']
            oversold = self.params['stochastic_oversold']
            overbought = self.params['stochastic_overbought']
            
            if stoch_k < oversold:
                signal = 1.0
            elif stoch_k > overbought:
                signal = -1.0
            else:
                signal = 0.0
            
            weight = self.params['stochastic_weight']
            weighted_sum += weight * signal
            filter_signals['stochastic'] = {'weight': weight, 'signal': signal}
        
        # ATR (volatility filter)
        if self.params.get('atr_weight', 0) > 0.01 and 'atr' in row:
            atr = row['atr']
            min_atr = self.params['atr_min_threshold']
            signal = 0.2 if atr > min_atr else -0.2
            
            weight = self.params['atr_weight']
            weighted_sum += weight * signal
            filter_signals['atr'] = {'weight': weight, 'signal': signal}
        
        # ADX (trend strength)
        if self.params.get('adx_weight', 0) > 0.01 and 'adx' in row:
            adx = row['adx']
            threshold = self.params['adx_threshold']
            if adx > threshold:
                trend = row.get('trend', 'NEUTRAL')
                signal = 0.5 if trend == 'BULLISH' else -0.5 if trend == 'BEARISH' else 0
            else:
                signal = 0.0
            
            weight = self.params['adx_weight']
            weighted_sum += weight * signal
            filter_signals['adx'] = {'weight': weight, 'signal': signal}
        
        # Support/Resistance
        if self.params.get('sr_weight', 0) > 0.01:
            near_support = row.get('near_support', False)
            near_resistance = row.get('near_resistance', False)
            
            if near_support:
                signal = 0.7
            elif near_resistance:
                signal = -0.7
            else:
                signal = 0.0
            
            weight = self.params['sr_weight']
            weighted_sum += weight * signal
            filter_signals['sr'] = {'weight': weight, 'signal': signal}
        
        # Momentum
        if self.params.get('momentum_weight', 0) > 0.01 and 'momentum' in row:
            momentum = row['momentum']
            threshold = self.params['momentum_threshold']
            
            if momentum > threshold:
                signal = min(momentum / (threshold * 2), 1.0)
            elif momentum < -threshold:
                signal = max(momentum / (threshold * 2), -1.0)
            else:
                signal = 0.0
            
            weight = self.params['momentum_weight']
            weighted_sum += weight * signal
            filter_signals['momentum'] = {'weight': weight, 'signal': signal}
        
        # Market Structure
        if self.params.get('market_structure_weight', 0) > 0.01 and 'market_structure' in row:
            structure = row['market_structure']
            signal = 0.8 if structure == 'BULLISH' else -0.8 if structure == 'BEARISH' else 0.0
            
            weight = self.params['market_structure_weight']
            weighted_sum += weight * signal
            filter_signals['market_structure'] = {'weight': weight, 'signal': signal}
        
        # Time Filter
        if self.params.get('time_filter_weight', 0) > 0.01:
            hour = datetime.now(timezone.utc).hour  # Now it will work!
            start_hour = int(self.params['trade_start_hour'])
            end_hour = int(self.params['trade_end_hour'])
            
            if start_hour <= end_hour:
                in_window = start_hour <= hour <= end_hour
            else:  # Overnight trading
                in_window = hour >= start_hour or hour <= end_hour
            
            signal = 0.1 if in_window else -0.5
            
            weight = self.params['time_filter_weight']
            weighted_sum += weight * signal
            filter_signals['time_filter'] = {'weight': weight, 'signal': signal}
        
        # MTF Confirmation (if trend data provided)
        if self.params.get('mtf_confirmation_weight', 0) > 0.01 and trend_data:
            # Check if trends align
            entry_trend = row.get('trend', 'NEUTRAL')
            higher_trend = trend_data.get('trend', 'NEUTRAL')
            
            if entry_trend == higher_trend and entry_trend != 'NEUTRAL':
                signal = 0.6 if entry_trend == 'BULLISH' else -0.6
            else:
                signal = 0.0
            
            weight = self.params['mtf_confirmation_weight']
            weighted_sum += weight * signal
            filter_signals['mtf_confirmation'] = {'weight': weight, 'signal': signal}
        
        return weighted_sum, filter_signals
    
    def get_entry_signal(self, entry_df, trend_df=None):
        """Determine if should enter position - YOUR EXACT THRESHOLDS"""
        try:
            # Get latest complete candle
            last_row = entry_df.iloc[-1]
            
            # Get trend data if available
            trend_data = None
            if trend_df is not None and len(trend_df) > 0:
                trend_data = trend_df.iloc[-1].to_dict()
            
            # Calculate weighted signal
            weighted_sum, filter_signals = self.calculate_weighted_signal(last_row, trend_data)
            
            # Check against threshold (BTC: 1.998, SOL: 1.984)
            entry_threshold = self.params['entry_threshold']
            
            if weighted_sum > entry_threshold:
                return 'LONG', weighted_sum, filter_signals
            elif weighted_sum < -entry_threshold:
                return 'SHORT', weighted_sum, filter_signals
            else:
                return None, weighted_sum, filter_signals
                
        except Exception as e:
            logger.error(f"Error calculating entry signal: {e}")
            return None, 0, {}
    
    def get_entry_signal_delayed(self, entry_df, trend_df=None):
        """
        OPTIMIZATION-ALIGNED VERSION: Uses PREVIOUS candle data for signals
        This matches the optimization code's logic with 1-candle delay
        """
        try:
            # Need at least 2 candles
            if len(entry_df) < 2:
                return None, 0, {}
            
            # Get PREVIOUS candle (not the latest) for signal calculation
            prev_row = entry_df.iloc[-2]  # -2 is previous, -1 is current
            
            # Get trend data from previous candle if available
            trend_data = None
            if trend_df is not None and len(trend_df) > 1:
                trend_data = trend_df.iloc[-2].to_dict()
            
            # Calculate weighted signal using PREVIOUS candle
            weighted_sum, filter_signals = self.calculate_weighted_signal(prev_row, trend_data)
            
            # Check against threshold
            entry_threshold = self.params['entry_threshold']
            
            if weighted_sum > entry_threshold:
                return 'LONG', weighted_sum, filter_signals
            elif weighted_sum < -entry_threshold:
                return 'SHORT', weighted_sum, filter_signals
            else:
                return None, weighted_sum, filter_signals
                
        except Exception as e:
            logger.error(f"Error calculating delayed entry signal: {e}")
            return None, 0, {}
    
    def should_exit_position(self, position_data, current_price):
        """Check if should exit position - YOUR EXACT TP/SL"""
        entry_price = position_data['entry_price']
        side = position_data['side']
        
        if side == 'LONG':
            # Calculate profit
            profit_pct = (current_price - entry_price) / entry_price
            
            # Check TP (BTC: 4.86%, SOL: 4.94%)
            if profit_pct >= self.params['tp_percent']:
                return 'TP'
            
            # Check SL (BTC: 1.40%, SOL: 1.64%)
            if profit_pct <= -self.params['sl_percent']:
                return 'SL'
            
            # Check trailing if enabled (BTC: disabled, SOL: slightly enabled)
            if self.params.get('use_trailing', 0) > 0.5:
                if profit_pct >= (self.params['tp_percent'] * self.params['trailing_activation']):
                    # Trailing activated
                    highest = position_data.get('highest_price', current_price)
                    if current_price > highest:
                        position_data['highest_price'] = current_price
                    
                    trail_from_high = (highest - current_price) / highest
                    if trail_from_high >= self.params['trailing_distance']:
                        return 'TRAIL'
        
        else:  # SHORT
            # Calculate profit
            profit_pct = (entry_price - current_price) / entry_price
            
            # Check TP
            if profit_pct >= self.params['tp_percent']:
                return 'TP'
            
            # Check SL
            if profit_pct <= -self.params['sl_percent']:
                return 'SL'
            
            # Check trailing if enabled
            if self.params.get('use_trailing', 0) > 0.5:
                if profit_pct >= (self.params['tp_percent'] * self.params['trailing_activation']):
                    # Trailing activated
                    lowest = position_data.get('lowest_price', current_price)
                    if current_price < lowest:
                        position_data['lowest_price'] = current_price
                    
                    trail_from_low = (current_price - lowest) / lowest
                    if trail_from_low >= self.params['trailing_distance']:
                        return 'TRAIL'
        
        return None