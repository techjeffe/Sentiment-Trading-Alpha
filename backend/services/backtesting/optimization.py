"""
Rolling Window Optimization Module
Implements walk-forward optimization for strategy parameter tuning
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime


@dataclass
class OptimizationResult:
    """Results from a single optimization run."""
    window_start: str
    window_end: str
    test_start: str
    test_end: str
    threshold: float
    total_return: float
    annualized_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    total_trades: int


class RollingWindowOptimizer:
    """
    Walk-forward optimizer for rolling window backtesting.
    
    Implements the walk-forward analysis technique where:
    1. Train on a lookback window
    2. Test on forward period
    3. Shift window forward and repeat
    
    This approach helps avoid overfitting by continuously re-optimizing
    parameters as new data becomes available.
    """
    
    def __init__(
        self,
        lookback_days: int = 14,
        test_period_days: int = 7,
        step_days: int = 1,
        leverage: float = 3.0
    ):
        """
        Initialize rolling window optimizer.
        
        Args:
            lookback_days: Size of training window (default: 14 days)
            test_period_days: Size of forward testing period (default: 7 days)
            step_days: Number of days to shift window each iteration (default: 1)
            leverage: Position leverage for backtesting (default: 3x)
        """
        self.lookback_days = lookback_days
        self.test_period_days = test_period_days
        self.step_days = step_days
        self.leverage = leverage
        
    def optimize(
        self,
        prices: pd.Series,
        signal_thresholds: Optional[List[float]] = None,
        stop_loss_pct: float = 2.0,
        take_profit_pct: float = 3.0
    ) -> Dict[str, Any]:
        """
        Run walk-forward optimization on price data.
        
        Args:
            prices: OHLCV Close prices (pd.Series)
            signal_thresholds: Threshold values to test for signal generation
            stop_loss_pct: Stop loss percentage
            take_profit_pct: Take profit percentage
            
        Returns:
            Dictionary containing:
            - results: List of optimization results per window
            - best_parameters: Optimal threshold and metrics
            - summary: Aggregate statistics
        """
        if signal_thresholds is None:
            signal_thresholds = [-0.5, -0.3, -0.1, 0.1, 0.3]
        
        results = []
        all_returns = []
        all_sharpe = []
        all_drawdowns = []
        
        # Walk-forward loop
        for i in range(0, len(prices) - self.lookback_days - self.test_period_days, self.step_days):
            # Define window boundaries
            train_end = i + self.lookback_days
            test_start = train_end
            test_end = min(train_end + self.test_period_days, len(prices))
            
            # Extract training data
            train_prices = prices.iloc[:train_end]
            
            # Optimize parameters on training data
            opt_result = self._optimize_on_training_data(
                train_prices, signal_thresholds, stop_loss_pct, take_profit_pct
            )
            
            best_threshold = opt_result['best_threshold']
            
            # Generate signals for test period using optimized threshold
            test_signals = self._generate_test_signals(
                prices.iloc[test_start:test_end], best_threshold
            )
            
            if len(test_signals) < 1:
                continue
            
            # Run backtest on test period
            result = self._run_backtest(
                prices.iloc[test_start:test_end],
                test_signals,
                stop_loss_pct,
                take_profit_pct
            )
            
            results.append(OptimizationResult(
                window_start=str(prices.index[i]),
                window_end=str(prices.index[train_end - 1]),
                test_start=str(prices.index[test_start]),
                test_end=str(prices.index[min(test_end - 1, len(prices) - 1)]),
                threshold=best_threshold,
                total_return=result['total_return'],
                annualized_return=result['annualized_return'],
                sharpe_ratio=result['sharpe_ratio'],
                max_drawdown=result['max_drawdown'],
                win_rate=result['win_rate'],
                total_trades=result['total_trades']
            ))
            
            # Collect metrics for summary
            all_returns.append(result['total_return'])
            all_sharpe.append(result['sharpe_ratio'])
            all_drawdowns.append(result['max_drawdown'])
        
        regime_validation = self.evaluate_regime_mix(prices)
        if not results:
            return {
                'results': [],
                'best_parameters': None,
                'summary': {},
                'regime_validation': regime_validation,
            }
        
        # Find best parameters (highest Sharpe ratio)
        best_idx = np.argmax([r.sharpe_ratio for r in results])
        best_result = results[best_idx]
        
        return {
            'results': [vars(r) for r in results],
            'best_parameters': {
                'threshold': best_result.threshold,
                'total_return': best_result.total_return,
                'annualized_return': best_result.annualized_return,
                'sharpe_ratio': best_result.sharpe_ratio,
                'max_drawdown': best_result.max_drawdown,
                'win_rate': best_result.win_rate,
                'total_trades': best_result.total_trades
            },
            'summary': {
                'num_windows': len(results),
                'avg_total_return': float(np.mean(all_returns)),
                'avg_sharpe_ratio': float(np.mean(all_sharpe)),
                'avg_max_drawdown': float(np.mean(all_drawdowns)),
                'std_sharpe_ratio': float(np.std(all_sharpe)),
                'best_threshold': best_result.threshold,
                'total_data_points': len(prices)
            },
            'regime_validation': regime_validation,
        }

    def evaluate_regime_mix(self, prices: pd.Series) -> Dict[str, Any]:
        """Classify recent history into coarse regimes and verify minimum mix coverage."""
        if prices is None or len(prices) < 30:
            return {"ok": False, "reason": "insufficient_prices", "counts": {}}
        returns = prices.pct_change().dropna()
        if returns.empty:
            return {"ok": False, "reason": "no_returns", "counts": {}}
        roll = returns.rolling(20).mean().dropna()
        vol = returns.rolling(20).std().dropna()
        common_idx = roll.index.intersection(vol.index)
        if len(common_idx) == 0:
            return {"ok": False, "reason": "insufficient_window", "counts": {}}
        trend_up = 0
        trend_down_high_vol = 0
        chop = 0
        for idx in common_idx:
            mu = float(roll.loc[idx])
            sigma = float(vol.loc[idx])
            if mu > 0.001:
                trend_up += 1
            elif mu < -0.001 and sigma > 0.015:
                trend_down_high_vol += 1
            elif abs(mu) <= 0.0007:
                chop += 1
        counts = {
            "trending_up": trend_up,
            "trending_down_high_vol": trend_down_high_vol,
            "range_chop": chop,
        }
        min_required = 5
        ok = all(v >= min_required for v in counts.values())
        return {
            "ok": ok,
            "min_required": min_required,
            "counts": counts,
        }
    
    def _optimize_on_training_data(
        self,
        prices: pd.Series,
        thresholds: List[float],
        stop_loss_pct: float,
        take_profit_pct: float
    ) -> Dict[str, Any]:
        """
        Find best threshold on training data.
        
        Args:
            prices: Training period prices
            thresholds: Threshold values to test
            stop_loss_pct: Stop loss percentage
            take_profit_pct: Take profit percentage
            
        Returns:
            Dictionary with best threshold and metrics
        """
        best_threshold = thresholds[0]
        best_sharpe = -float('inf')
        
        for threshold in thresholds:
            signals = self._generate_training_signals(prices, threshold)
            
            if len(signals) < 3:
                continue
            
            # Simple backtest on training data
            result = self._run_backtest(
                prices, signals, stop_loss_pct, take_profit_pct
            )
            
            if result['sharpe_ratio'] > best_sharpe:
                best_sharpe = result['sharpe_ratio']
                best_threshold = threshold
        
        return {
            'best_threshold': best_threshold,
            'best_sharpe': best_sharpe
        }
    
    def _generate_training_signals(
        self,
        prices: pd.Series,
        threshold: float
    ) -> pd.Series:
        """Generate signals based on price momentum threshold."""
        returns = prices.pct_change()
        signals = (returns > threshold).astype(int)
        return pd.Series(signals, index=prices.index)
    
    def _generate_test_signals(
        self,
        prices: pd.Series,
        threshold: float
    ) -> pd.Series:
        """Generate test signals using optimized threshold."""
        returns = prices.pct_change()
        signals = (returns > threshold).astype(int)
        return pd.Series(signals, index=prices.index)
    
    def _run_backtest(
        self,
        prices: pd.Series,
        signals: pd.Series,
        stop_loss_pct: float,
        take_profit_pct: float
    ) -> Dict[str, Any]:
        """
        Run simple backtest on price data with signals.
        
        Args:
            prices: Price series
            signals: Binary signal series (1=long, 0=no trade)
            stop_loss_pct: Stop loss percentage
            take_profit_pct: Take profit percentage
            
        Returns:
            Dictionary with backtest metrics
        """
        if len(prices) < 2:
            return {
                'total_return': 0.0,
                'annualized_return': 0.0,
                'sharpe_ratio': 0.0,
                'max_drawdown': 0.0,
                'win_rate': 0.0,
                'total_trades': 0
            }
        
        # Generate entry/exit points
        entries = []
        exits = []
        trade_returns = []
        
        for i in range(1, len(signals)):
            if signals.iloc[i] == 1 and signals.iloc[i-1] == 0:
                # Entry signal
                entries.append(i)
            
            if signals.iloc[i] == 0 and signals.iloc[i-1] == 1:
                # Exit signal
                exits.append(i)
        
        # Calculate returns for each trade
        for entry_idx, exit_idx in zip(entries[:len(exits)], exits):
            entry_price = prices.iloc[entry_idx]
            exit_price = prices.iloc[exit_idx]
            
            # Apply leverage
            leveraged_return = (exit_price / entry_price - 1) * self.leverage
            
            trade_returns.append(leveraged_return)
        
        if not trade_returns:
            return {
                'total_return': 0.0,
                'annualized_return': 0.0,
                'sharpe_ratio': 0.0,
                'max_drawdown': 0.0,
                'win_rate': 0.0,
                'total_trades': 0
            }
        
        # Calculate metrics
        total_return = sum(trade_returns) * 100
        
        days = len(prices)
        # Annualized return: handle cases where total_return is very negative
        if days > 0:
            base = 1 + total_return/100
            if base > 0:
                annualized_return = (base ** (252/days) - 1) * 100
            else:
                # If capital is lost (base <= 0), return is undefined; use negative return
                annualized_return = -100.0 if total_return < 0 else 0.0
        else:
            annualized_return = 0.0
        
        # Sharpe ratio
        risk_free_rate = 2.0 / 252  # Daily risk-free rate
        excess_returns = np.array(trade_returns) - risk_free_rate
        std_returns = np.std(excess_returns)
        sharpe_ratio = (excess_returns.mean() / std_returns * np.sqrt(252)) if std_returns > 0 else 0.0
        
        # Max drawdown
        cumulative = np.cumprod(1 + np.array(trade_returns) / 100)
        running_max = np.maximum.accumulate(cumulative)
        drawdown = (cumulative - running_max) / running_max
        max_drawdown = drawdown.min() * 100
        
        # Win rate
        wins = [r for r in trade_returns if r > 0]
        losses = [r for r in trade_returns if r < 0]
        win_rate = (len(wins) / len(trade_returns) * 100) if trade_returns else 0.0
        
        return {
            'total_return': total_return,
            'annualized_return': annualized_return,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown': max_drawdown,
            'win_rate': win_rate,
            'total_trades': len(trade_returns)
        }


def max_day_trades_in_rolling_window(
    round_trips: List[Tuple[datetime, datetime]],
    window_days: int = 5,
) -> int:
    """
    PDT stress check: maximum number of day trades (round trips opened and
    closed on the same calendar day) inside any rolling `window_days`-day
    window. A margin account is flagged pattern-day-trader at 3+ day trades in
    5 rolling trading days, so the de-risked execution rules must never push
    this above 3.

    Pure helper used by the verification tests; takes (open_dt, close_dt) UTC
    pairs and counts only pairs whose open and close fall on the same calendar
    day.
    """
    daily: Dict[str, int] = {}
    for open_dt, close_dt in round_trips:
        if open_dt is None or close_dt is None:
            continue
        try:
            same_day = open_dt.date() == close_dt.date()
        except AttributeError:
            same_day = open_dt.date() == close_dt.date()  # pandas Timestamp has .date()
        if same_day:
            key = str(open_dt.date())
            daily[key] = daily.get(key, 0) + 1

    days = sorted(daily)
    if not days:
        return 0
    max_count = 0
    for i, day in enumerate(days):
        window_end = pd.Timestamp(day) + pd.Timedelta(days=window_days)
        count = 0
        for j in range(i, len(days)):
            if pd.Timestamp(days[j]) < window_end:
                count += daily[days[j]]
            else:
                break
        max_count = max(max_count, count)
    return max_count
