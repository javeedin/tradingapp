"""High-beta stock screener for detecting 2%+ upward movement potential."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class StockScreener:
    """Scans stocks for high beta with 2%+ upward movement potential."""

    # Risk parameters
    MIN_PRICE = 50
    MAX_DAILY_LOSS_RISK = 0.02  # 2%
    MIN_VOLUME_AVG = 500_000
    MIN_BETA = 1.1
    MAX_BETA = 3.5

    # Signal weights (total = 100)
    SIGNAL_WEIGHTS = {
        'volume_spike': 25,
        'breakout': 25,
        'rsi': 20,
        'macd': 15,
        'beta': 15,
    }

    def __init__(self, breeze_client: Any, data_dir: str | None = None):
        self.client = breeze_client
        if data_dir is None:
            data_dir = str(Path.cwd() / 'data' / 'screener')
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def calculate_rsi(self, closes: list[float], period: int = 14) -> float:
        """Calculate RSI (Relative Strength Index)."""
        if len(closes) < period + 1:
            return 50

        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = sum(d for d in deltas if d > 0) / period
        losses = abs(sum(d for d in deltas if d < 0) / period)

        if losses == 0:
            return 100 if gains > 0 else 0

        rs = gains / losses
        return 100 - (100 / (1 + rs))

    def calculate_macd(self, closes: list[float]) -> tuple[float, float]:
        """Calculate MACD (simplified)."""
        if len(closes) < 26:
            return 0, 0

        ema12 = sum(closes[-12:]) / 12
        ema26 = sum(closes[-26:]) / 26
        macd = ema12 - ema26
        signal = sum([ema12 - ema26 for _ in range(9)]) / 9 if len(closes) >= 35 else 0

        return macd, signal

    def calculate_atr(self, highs: list[float], lows: list[float], period: int = 14) -> float:
        """Calculate ATR (Average True Range)."""
        if len(highs) < period:
            return 0

        tr = [highs[i] - lows[i] for i in range(-period, 0)]
        return sum(tr) / len(tr)

    def calculate_beta(self, stock_returns: list[float], market_returns: list[float]) -> float:
        """Calculate beta (simplified)."""
        if len(stock_returns) < 2 or len(market_returns) < 2:
            return 1.0

        if len(stock_returns) > len(market_returns):
            stock_returns = stock_returns[-len(market_returns):]
        elif len(market_returns) > len(stock_returns):
            market_returns = market_returns[-len(stock_returns):]

        covariance = sum(s * m for s, m in zip(stock_returns, market_returns)) / len(stock_returns)
        market_variance = sum(m * m for m in market_returns) / len(market_returns)

        if market_variance == 0:
            return 1.0

        return covariance / market_variance

    def score_stock(self, data: dict[str, Any]) -> dict[str, Any]:
        """Calculate signal score for a stock (0-100)."""
        score = 0
        signals = {}

        # Volume spike (25 pts)
        vol_spike_ratio = data.get('volume_ratio', 1.0)
        volume_score = min(25, vol_spike_ratio * 12.5)
        signals['volume_spike'] = int(volume_score)
        score += volume_score

        # Breakout (25 pts)
        is_breakout = data.get('is_breakout', False)
        breakout_score = 25 if is_breakout else 0
        signals['breakout'] = breakout_score
        score += breakout_score

        # RSI momentum (20 pts)
        rsi = data.get('rsi', 50)
        rsi_score = 0
        if 50 <= rsi <= 70:
            rsi_score = 20
        elif 40 <= rsi < 50:
            rsi_score = 10
        signals['rsi'] = rsi_score
        score += rsi_score

        # MACD (15 pts)
        macd_positive = data.get('macd_positive', False)
        macd_score = 15 if macd_positive else 0
        signals['macd'] = macd_score
        score += macd_score

        # Beta (15 pts)
        beta = data.get('beta', 1.0)
        beta_score = 0
        if 1.1 <= beta <= 2.0:
            beta_score = 15
        elif 0.9 <= beta < 1.1 or 2.0 < beta <= 3.5:
            beta_score = 8
        signals['beta'] = beta_score
        score += beta_score

        return {
            'score': int(score),
            'signals': signals,
        }

    def screen_stocks(self, symbols: list[str] = None) -> dict[str, Any]:
        """Screen stocks for candidates."""
        if symbols is None:
            symbols = ['NIFTY', 'INFY', 'TCS', 'RELIANCE', 'HDFC', 'ICICIBANK', 'SBIN']

        candidates = []
        scan_timestamp = datetime.now().isoformat()

        for symbol in symbols:
            try:
                quote = self.client.get_quote(symbol)
                if not quote or quote.get('ltp', 0) < self.MIN_PRICE:
                    continue

                ltp = quote.get('ltp', 0)
                volume = quote.get('volume', 0)
                volume_avg = quote.get('volume_avg_20', 1)

                if volume < self.MIN_VOLUME_AVG:
                    continue

                # Calculate metrics
                volume_ratio = volume / (volume_avg + 1)
                rsi = self.calculate_rsi([ltp])
                macd, signal = self.calculate_macd([ltp])
                beta = quote.get('beta', 1.0)

                is_breakout = quote.get('close', 0) > quote.get('high_52', ltp)
                macd_positive = macd > signal

                candidate_data = {
                    'symbol': symbol,
                    'current_price': ltp,
                    'volume_current': volume,
                    'volume_avg': volume_avg,
                    'volume_ratio': volume_ratio,
                    'is_breakout': is_breakout,
                    'rsi': rsi,
                    'macd_positive': macd_positive,
                    'beta': beta,
                }

                scoring = self.score_stock(candidate_data)

                if scoring['score'] >= 50:  # Only show candidates with score >= 50
                    candidate = {
                        'symbol': symbol,
                        'current_price': round(ltp, 2),
                        'signal_score': scoring['score'],
                        'signals': scoring['signals'],
                        'entry_price': round(ltp, 2),
                        'stop_loss': round(ltp * (1 - self.MAX_DAILY_LOSS_RISK), 2),
                        'target_1': round(ltp * 1.02, 2),
                        'target_2': round(ltp * 1.035, 2),
                        'target_3': round(ltp * 1.05, 2),
                        'volume_current': int(volume),
                        'volume_avg': int(volume_avg),
                        'rsi': round(rsi, 2),
                        'beta': round(beta, 2),
                        'atr': 0,  # Will calculate from full data
                    }
                    candidates.append(candidate)

            except Exception as e:
                logger.warning(f"Error screening {symbol}: {e}")

        # Sort by score descending
        candidates.sort(key=lambda x: x['signal_score'], reverse=True)

        result = {
            'scan_timestamp': scan_timestamp,
            'scan_id': f"scan_{datetime.now().strftime('%Y%m%d_%H%M')}",
            'candidates': candidates[:20],  # Top 20
            'total_candidates': len(candidates),
        }

        # Save to JSON
        self._save_scan(result)

        return result

    def _save_scan(self, result: dict[str, Any]) -> None:
        """Save scan results to JSON file."""
        filename = self.data_dir / f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, 'w') as f:
            json.dump(result, f, indent=2)

    def get_latest_scan(self) -> dict[str, Any] | None:
        """Get the latest scan results."""
        files = sorted(self.data_dir.glob('scan_*.json'), reverse=True)
        if not files:
            return None

        try:
            with open(files[0], 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading scan file: {e}")
            return None
