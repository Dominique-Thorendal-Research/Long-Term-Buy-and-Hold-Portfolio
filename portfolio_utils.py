"""
Core portfolio analysis utilities.

Functions for fetching prices, computing returns, and calculating portfolio metrics.
"""

from typing import Dict, Optional
import pandas as pd
import numpy as np
import yfinance as yf

TRADING_DAYS = 252


def fetch_prices(tickers: Dict[str, str], start: str, end: Optional[str] = None) -> pd.DataFrame:
    """
    Fetch adjusted close prices for given tickers from Yahoo Finance.
    
    Args:
        tickers: Dictionary mapping asset names to ticker symbols (e.g., {"USA_SP500": "SPY"})
        start: Start date as string (e.g., "2012-10-16")
        end: End date as string or None for most recent data
    
    Returns:
        DataFrame with dates as index and asset names as columns
    """
    yf_tickers = list(tickers.values())
    data = yf.download(yf_tickers, start=start, end=end, progress=False)
    
    if data.empty:
        raise RuntimeError("No data fetched. Check tickers or date range.")
    
    # yfinance returns multi-level columns: (Price Type, Ticker)
    # Extract the 'Close' or 'Adj Close' prices
    if data.columns.nlevels > 1:
        # Multi-level columns from yfinance
        level_0_values = data.columns.get_level_values(0).unique()
        
        # Try to get 'Adj Close' first, fall back to 'Close'
        if 'Adj Close' in level_0_values:
            adj = data.xs('Adj Close', level=0, axis=1)
        elif 'Close' in level_0_values:
            adj = data.xs('Close', level=0, axis=1)
        else:
            raise RuntimeError(f"Could not find 'Adj Close' or 'Close' in columns. Available: {level_0_values.tolist()}")
    elif 'Adj Close' in data.columns:
        adj = data["Adj Close"].copy()
    elif 'Close' in data.columns:
        adj = data["Close"].copy()
    else:
        raise RuntimeError("Could not find price column in downloaded data.")
    
    # Rename columns from ticker symbols to asset names
    mapping = {tickers[k]: k for k in tickers}
    try:
        adj = adj.rename(columns=mapping)
    except:
        pass
    
    adj = adj.sort_index()
    return adj


def compute_returns(prices: pd.DataFrame, method: str = "log") -> pd.DataFrame:
    """
    Convert prices to returns.
    
    Args:
        prices: DataFrame of prices
        method: "log" for log returns (default) or "simple" for percentage returns
    
    Returns:
        DataFrame of returns
    """
    if method == "log":
        return np.log(prices / prices.shift(1)).dropna()
    else:
        return prices.pct_change().dropna()


def apply_annual_fees(returns: pd.DataFrame, annual_fees: Dict[str, float]) -> pd.DataFrame:
    """
    Apply annual fees (expense ratios) to returns.
    
    Args:
        returns: DataFrame of daily returns
        annual_fees: Dictionary mapping asset names to annual fee rates (e.g., 0.003 for 0.3%)
    
    Returns:
        DataFrame of net returns after fees deducted daily
    """
    daily_fees = {k: (1 + v) ** (1 / TRADING_DAYS) - 1 for k, v in annual_fees.items()}
    net = returns.copy()
    for col in net.columns:
        fee = daily_fees.get(col, 0.0)
        net[col] = net[col] - fee
    return net


def apply_isk_simple_tax_on_annual(values: pd.Series, annual_tax_rate: float) -> pd.Series:
    """
    Apply Swedish ISK schablon tax (annual tax on portfolio value at year-end).
    
    Args:
        values: Series of portfolio values (cumulative returns)
        annual_tax_rate: Annual tax rate (e.g., 0.003 for 0.3%)
    
    Returns:
        Series of after-tax values
    """
    vals = values.copy()
    yrs = vals.index.year
    year_ends = vals.groupby(yrs).apply(lambda s: s.index[-1])
    for idx in year_ends:
        tax_base = vals.loc[idx]
        vals.loc[idx:] = vals.loc[idx:] - (annual_tax_rate * tax_base)
    return vals


def portfolio_return_series(returns: pd.DataFrame, weights: Dict[str, float]) -> pd.Series:
    """
    Calculate portfolio returns from asset returns and weights.
    
    Args:
        returns: DataFrame of asset returns
        weights: Dictionary mapping asset names to weights (should sum to 1)
    
    Returns:
        Series of portfolio returns
    """
    cols = list(weights.keys())
    w = np.array([weights[c] for c in cols])
    rets = returns[cols].dot(w)
    return rets


def get_historical_riksbanken_rate(date: pd.Timestamp) -> float:
    """
    Get historical Riksbanken repo rate for a given date.
    Based on actual Riksbanken policy rates 2007-2025.
    
    Returns:
        Annual repo rate as decimal (e.g., 0.035 for 3.5%)
    """
    # Historical Riksbanken repo rate changes
    if date < pd.Timestamp('2009-01-01'):
        return 0.035  # 3.5% (pre-crisis)
    elif date < pd.Timestamp('2010-07-01'):
        return 0.005  # 0.5% (crisis period)
    elif date < pd.Timestamp('2021-01-01'):
        return 0.000  # 0.0% (ultra-low rates)
    elif date < pd.Timestamp('2022-04-01'):
        return 0.000  # 0.0% (still near-zero)
    elif date < pd.Timestamp('2022-09-01'):
        return 0.0075  # 0.75%
    elif date < pd.Timestamp('2022-11-01'):
        return 0.015  # 1.5%
    elif date < pd.Timestamp('2022-12-01'):
        return 0.020  # 2.0%
    elif date < pd.Timestamp('2023-02-01'):
        return 0.020  # 2.0%
    elif date < pd.Timestamp('2023-09-01'):
        return 0.025  # 2.5%
    elif date < pd.Timestamp('2024-01-01'):
        return 0.0275  # 2.75%
    elif date < pd.Timestamp('2024-05-01'):
        return 0.035  # 3.5%
    else:
        return 0.035  # 3.5% (current/normalized)


def add_cash_returns(returns: pd.DataFrame, cash_rate=None, bank_margin: float = 0.0075, 
                     use_riksbanken: bool = True, trading_days: int = TRADING_DAYS) -> pd.DataFrame:
    """
    Add cash return column to a returns DataFrame.
    Can use constant rate OR time-varying Riksbanken-based rates.
    
    Args:
        returns: DataFrame of asset returns
        cash_rate: Fixed annual cash rate (e.g., 0.01 for 1%). 
                   If None and use_riksbanken=True, uses Riksbanken rates.
        bank_margin: Bank margin over repo rate (default 0.75%). Only used with Riksbanken rates.
        use_riksbanken: If True, uses historical Riksbanken rates + margin. If False, uses fixed cash_rate.
        trading_days: Number of trading days per year (default 252)
    
    Returns:
        DataFrame with 'Cash' column added
    """
    out = returns.copy()
    
    if use_riksbanken and cash_rate is None:
        # Time-varying rates based on Riksbanken repo rate + bank margin
        cash_rates = []
        for date in returns.index:
            repo_rate = get_historical_riksbanken_rate(date)
            effective_rate = repo_rate + bank_margin
            daily_rate = (1 + effective_rate) ** (1 / trading_days) - 1
            cash_rates.append(daily_rate)
        out['Cash'] = pd.Series(cash_rates, index=returns.index)
    else:
        # Constant rate (backward compatible)
        if cash_rate is None:
            cash_rate = 0.02  # Default fallback
        daily_cash = (1 + cash_rate) ** (1 / trading_days) - 1
        cash_col = pd.Series(daily_cash, index=returns.index, name='Cash')
        out['Cash'] = cash_col
    
    return out


# Metric calculation functions

def cagr_from_value_series(values: pd.Series) -> float:
    """
    Calculate Compound Annual Growth Rate from cumulative values.
    
    Args:
        values: Series of cumulative portfolio values
    
    Returns:
        CAGR as a decimal (e.g., 0.08 for 8%)
    """
    days = (values.index[-1] - values.index[0]).days
    years = days / 365.25
    return (values.iloc[-1] / values.iloc[0]) ** (1 / years) - 1


def max_drawdown(values: pd.Series) -> float:
    """
    Calculate maximum drawdown from cumulative values.
    
    Args:
        values: Series of cumulative portfolio values
    
    Returns:
        Maximum drawdown as a decimal (e.g., -0.25 for 25% drawdown)
    """
    cum = values / values.iloc[0]
    roll_max = cum.cummax()
    dd = (cum / roll_max) - 1
    return dd.min()


def annualized_vol(returns: pd.Series) -> float:
    """
    Calculate annualized volatility from daily returns.
    
    Args:
        returns: Series of daily returns
    
    Returns:
        Annualized volatility as a decimal (e.g., 0.15 for 15%)
    """
    return returns.std() * np.sqrt(TRADING_DAYS)


def sharpe_ratio(returns: pd.Series, risk_free: float = 0.0) -> float:
    """
    Calculate Sharpe ratio from returns.
    
    Args:
        returns: Series of daily returns
        risk_free: Risk-free rate as annualized decimal (default 0.0)
    
    Returns:
        Sharpe ratio (excess return per unit of volatility)
    """
    ann_ret = returns.mean() * TRADING_DAYS
    ann_vol = annualized_vol(returns)
    return (ann_ret - risk_free) / ann_vol if ann_vol != 0 else np.nan


def portfolio_metrics_from_returns(portfolio_rets: pd.Series, start_value: float = 1.0, risk_free: float = 0.0) -> Dict[str, float]:
    """
    Calculate key portfolio metrics from returns.
    
    Args:
        portfolio_rets: Series of portfolio daily returns
        start_value: Starting portfolio value (default 1.0)
        risk_free: Risk-free rate as annualized decimal (default 0.0)
    
    Returns:
        Dictionary with keys: "CAGR", "AnnualVol", "Sharpe", "MaxDrawdown"
    """
    values = (1 + portfolio_rets).cumprod() * start_value
    return {
        "CAGR": cagr_from_value_series(values),
        "AnnualVol": annualized_vol(portfolio_rets),
        "Sharpe": sharpe_ratio(portfolio_rets, risk_free),
        "MaxDrawdown": max_drawdown(values)
    }
