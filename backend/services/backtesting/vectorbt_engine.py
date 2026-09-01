"""
VectorBT Backtesting Engine
Implements rolling window backtesting with walk-forward optimization
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import vectorbt as vbt


@dataclass
class BacktestResult:
    """Results from a VectorBT backtest."""
    symbol: str
    total_return: float
    annualized_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_win_pct: float
    avg_loss_pct: float
    profit_factor: float
    lookback_days: int
    walk_forward_steps: int
    entry_prices: List[float] = field(default_factory=list)
    exit_prices: List[float] = field(default_factory=list)
    trade_dates: List[datetime] = field(default_factory=list)


class VectorBTBacktester:
    """
    Backtesting engine using VectorBT for vectorized backtesting.
    
    Features:
    - Rolling window optimization (14-day lookback)
    - Walk-forward analysis
    - 3x leverage support
    - Stop-loss and take-profit management
    """
    
    # Default parameters
    DEFAULT_LEVERAGE = 3.0
    DEFAULT_STOP_LOSS_PCT = 2.0
    DEFAULT_TAKE_PROFIT_PCT = 3.0
    DEFAULT_LOOKBACK_DAYS = 14
    DEFAULT_MIN_TRADES = 5
    
    def __init__(
        self,
        leverage: float = DEFAULT_LEVERAGE,
        stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT,
        take_profit_pct: float = DEFAULT_TAKE_PROFIT_PCT,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS
    ):
        """
        Initialize backtester.
        
        Args:
            leverage: Position leverage (default 3x)
            stop_loss_pct: Stop loss percentage
            take_profit_pct: Take profit percentage
            lookback_days: Rolling window size for optimization
        """
        self.leverage = leverage
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.lookback_days = lookback_days
        
    def backtest(
        self,
        prices: pd.Series,
        signals: pd.Series,
        entry_prices: Optional[pd.Series] = None,
        exit_prices: Optional[pd.Series] = None,
        regime_filter: bool = False,
    ) -> BacktestResult:
        """
        Run backtest on price data with signals.
        
        Args:
            prices: OHLCV Close prices (pd.Series)
            signals: Binary signal series (1=long, 0=hold, -1=short)
            entry_prices: Optional entry prices for each trade
            exit_prices: Optional exit prices for each trade
            
        Returns:
            BacktestResult with performance metrics
        """
        if len(prices) < self.lookback_days + 1:
            raise ValueError(f"Insufficient data: need at least {self.lookback_days + 1} days")

        if regime_filter:
            from services.regime import classify_regime_from_prices
            _mask = pd.Series(
                [
                    classify_regime_from_prices(prices.iloc[max(0, i - 30) : i]) != "choppy"
                    for i in range(len(prices))
                ],
                index=prices.index,
            )
            signals = signals.mask(~_mask, 0)

        
        # Generate entry/exit signals from price changes
        if entry_prices is None or exit_prices is None:
            entry_prices, exit_prices = self._generate_signals(prices, signals)
        
        # Calculate returns with leverage
        leveraged_returns = (entry_prices / exit_prices - 1) * self.leverage
        
        # Create DataFrame for VectorBT
        df = pd.DataFrame({
            'open': prices[:-1],
            'high': prices[:-1] * 1.01,  # Approximate high
            'low': prices[:-1] * 0.99,   # Approximate low
            'close': entry_prices,
            'volume': np.random.randint(1000000, 10000000, len(entry_prices))
        })
        
        # Run VectorBT backtest
        bt = vbt.Positions.from_dataframe(
            df,
            entry=entry_prices.index,
            exit=exit_prices.index,
            leverage=self.leverage
        )
        
        # Calculate metrics
        returns = bt.returns()
        total_return = (returns.iloc[-1] * 100) if len(returns) > 0 else 0.0
        
        # Annualized return
        days = len(returns)
        annualized_return = ((1 + total_return/100) ** (252/days) - 1) * 100 if days > 0 else 0.0
        
        # Sharpe ratio (assuming 2% risk-free rate)
        risk_free_rate = 2.0
        excess_returns = returns - risk_free_rate/252
        std_returns = returns.std()
        sharpe_ratio = (excess_returns.mean() / std_returns * np.sqrt(252)) if std_returns > 0 else 0.0
        
        # Max drawdown
        cumulative = (1 + returns/100).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max * 100
        max_drawdown = drawdown.min() if len(drawdown) > 0 else 0.0
        
        # Trade statistics
        trade_returns = bt.returns()
        winning_trades = (trade_returns > 0).sum()
        losing_trades = (trade_returns < 0).sum()
        total_trades = len(trade_returns)
        
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
        
        # Average win/loss
        wins = trade_returns[trade_returns > 0]
        losses = trade_returns[trade_returns < 0]
        
        avg_win_pct = wins.mean() * 100 if len(wins) > 0 else 0.0
        avg_loss_pct = losses.mean() * 100 if len(losses) > 0 else 0.0
        
        # Profit factor
        gross_profit = abs(wins.sum()) if len(wins) > 0 else 0.0
        gross_loss = abs(losses.sum()) if len(losses) > 0 else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        # Extract trade data
        entry_prices_list = entry_prices.values.tolist()
        exit_prices_list = exit_prices.values.tolist()
        trade_dates = [pd.Timestamp(idx) for idx in bt.trades.index]
        
        return BacktestResult(
            symbol="",  # Would be set by caller
            total_return=total_return,
            annualized_return=annualized_return,
            sharpe_ratio=sharpe_ratio,
            max_drawdown=max_drawdown,
            win_rate=win_rate,
            total_trades=total_trades,
            winning_trades=int(winning_trades),
            losing_trades=int(losing_trades),
            avg_win_pct=avg_win_pct,
            avg_loss_pct=avg_loss_pct,
            profit_factor=profit_factor,
            lookback_days=self.lookback_days,
            walk_forward_steps=0,  # Would be set by optimizer
            entry_prices=entry_prices_list[:20],  # Limit for display
            exit_prices=exit_prices_list[:20],
            trade_dates=trade_dates[:20]
        )
    
    def _generate_signals(
        self,
        prices: pd.Series,
        signals: pd.Series
    ) -> Tuple[pd.Series, pd.Series]:
        """
        Generate entry/exit prices from price data and signals.
        
        Args:
            prices: Close prices
            signals: Binary signal series
            
        Returns:
            Tuple of (entry_prices, exit_prices) DataFrames
        """
        # Simple strategy: enter on signal=1, exit next day
        entry_mask = signals == 1
        exit_mask = signals.shift(1) == 1
        
        entry_prices = prices.copy()
        exit_prices = prices.shift(-1).copy()
        
        return entry_prices, exit_prices
    
    def optimize_parameters(
        self,
        prices: pd.Series,
        signal_thresholds: List[float]
    ) -> Dict[str, Any]:
        """
        Optimize strategy parameters using walk-forward analysis.
        
        Args:
            prices: OHLCV Close prices
            signal_thresholds: Threshold values to test
            
        Returns:
            Dictionary with optimized parameters and performance metrics
        """
        best_params = {
            'threshold': signal_thresholds[0],
            'total_return': -float('inf'),
            'sharpe_ratio': -float('inf')
        }
        
        results = []
        
        for threshold in signal_thresholds:
            # Generate signals based on threshold
            signals = self._generate_signals_from_threshold(prices, threshold)
            
            if len(signals) < self.DEFAULT_MIN_TRADES:
                continue
            
            # Run backtest
            result = self.backtest(prices, signals)
            
            results.append({
                'threshold': threshold,
                'total_return': result.total_return,
                'sharpe_ratio': result.sharpe_ratio,
                'max_drawdown': result.max_drawdown,
                'win_rate': result.win_rate
            })
            
            # Track best parameters
            if result.sharpe_ratio > best_params['sharpe_ratio']:
                best_params = {
                    'threshold': threshold,
                    'total_return': result.total_return,
                    'sharpe_ratio': result.sharpe_ratio,
                    'max_drawdown': result.max_drawdown,
                    'win_rate': result.win_rate
                }
        
        return {
            'best_parameters': best_params,
            'all_results': results
        }
