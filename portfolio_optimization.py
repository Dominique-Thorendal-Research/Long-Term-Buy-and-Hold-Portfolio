"""
Portfolio optimization and sampling functions.

Includes portfolio optimization (Sharpe ratio maximization),
random portfolio generation, and rebalancing simulation.
"""

from typing import Dict
import pandas as pd
import numpy as np
from scipy.optimize import minimize
from portfolio_utils import TRADING_DAYS, portfolio_return_series, portfolio_metrics_from_returns


def recommend_weights_for_target_return(
    returns: pd.DataFrame,
    target_annual_ret: float,
    min_weight: float = 0.0,
    max_weight: float = 1.0
) -> Dict[str, float]:
    """
    Find portfolio weights that minimize variance while achieving target annual return.
    
    Args:
        returns: DataFrame of asset daily returns
        target_annual_ret: Target annual return as decimal (e.g., 0.08 for 8%)
        min_weight: Minimum weight per asset (default 0.0 = no short sales)
        max_weight: Maximum weight per asset (default 1.0 = no leverage)
    
    Returns:
        Dictionary mapping asset names to optimal weights
    """
    assets = list(returns.columns)
    mean_ann = returns.mean() * TRADING_DAYS
    cov_ann = returns.cov() * TRADING_DAYS
    n = len(assets)

    def obj(w):
        return float(w @ cov_ann.values @ w)

    cons = [
        {'type': 'eq', 'fun': lambda w: float(np.sum(w) - 1.0)},
        {'type': 'ineq', 'fun': lambda w: float(w @ mean_ann.values - target_annual_ret)}
    ]
    bounds = tuple((min_weight, max_weight) for _ in range(n))
    x0 = np.array([1.0 / n] * n)

    res = minimize(obj, x0, method='SLSQP', bounds=bounds, constraints=cons, options={'ftol': 1e-9, 'maxiter': 1000})
    if not res.success:
        print('⚠ Optimization failed:', res.message)
    w_opt = np.clip(res.x, 0, 1)
    w_opt = w_opt / w_opt.sum()
    return {assets[i]: float(w_opt[i]) for i in range(n)}


def recommend_weights_for_max_vol(
    returns: pd.DataFrame,
    max_annual_vol: float,
    min_weight: float = 0.0,
    max_weight: float = 1.0
) -> Dict[str, float]:
    """
    Find portfolio weights that maximize return while keeping volatility below threshold.
    
    Args:
        returns: DataFrame of asset daily returns
        max_annual_vol: Maximum annual volatility as decimal (e.g., 0.13 for 13%)
        min_weight: Minimum weight per asset (default 0.0 = no short sales)
        max_weight: Maximum weight per asset (default 1.0 = no leverage)
    
    Returns:
        Dictionary mapping asset names to optimal weights
    """
    assets = list(returns.columns)
    mean_ann = returns.mean() * TRADING_DAYS
    cov_ann = returns.cov() * TRADING_DAYS
    n = len(assets)

    def obj(w):
        return -float(w @ mean_ann.values)

    def vol_con(w):
        vol = np.sqrt(float(w @ cov_ann.values @ w))
        return max_annual_vol - vol

    cons = [
        {'type': 'eq', 'fun': lambda w: float(np.sum(w) - 1.0)},
        {'type': 'ineq', 'fun': vol_con}
    ]

    bounds = tuple((min_weight, max_weight) for _ in range(n))
    x0 = np.array([1.0 / n] * n)

    res = minimize(obj, x0, method='SLSQP', bounds=bounds, constraints=cons, options={'ftol': 1e-9, 'maxiter': 1000})
    if not res.success:
        print('⚠ Optimization failed:', res.message)
    w_opt = np.clip(res.x, 0, 1)
    w_opt = w_opt / w_opt.sum()
    return {assets[i]: float(w_opt[i]) for i in range(n)}


def weights_over_time_buy_and_hold(returns: pd.DataFrame, init_weights: dict) -> pd.DataFrame:
    """
    Calculate weight evolution over time for a buy-and-hold strategy.
    
    Args:
        returns: DataFrame of asset daily returns
        init_weights: Dictionary of initial portfolio weights
    
    Returns:
        DataFrame of weights over time
    """
    assets = list(returns.columns)
    w0 = np.array([init_weights.get(a, 0.0) for a in assets])
    cum = (1 + returns).cumprod()
    values = cum.multiply(w0, axis=1)
    weights_t = values.div(values.sum(axis=1), axis=0)
    return weights_t


def simulate_rebalancing(
    returns: pd.DataFrame,
    target_weights: dict,
    freq: str = '6M'
) -> tuple:
    """
    Simulate periodic rebalancing and calculate turnovers.
    
    Args:
        returns: DataFrame of asset daily returns
        target_weights: Dictionary of target portfolio weights
        freq: Rebalancing frequency (e.g., 'M', 'Q', '6M', 'A')
    
    Returns:
        Tuple of (weights_df, turnovers_series)
        - weights_df: DataFrame of weights over time
        - turnovers_series: Series of turnover at each rebalance date
    """
    assets = list(returns.columns)
    target = np.array([target_weights.get(a, 0.0) for a in assets])

    weights_df = pd.DataFrame(index=returns.index, columns=assets, dtype=float)
    # Values start with total 1 allocated according to target
    values = target.copy()
    turnovers = {}

    # Group by rebalance periods
    for period, group in returns.groupby(pd.Grouper(freq=freq)):
        if group.empty:
            continue
        for date, row in group.iterrows():
            values = values * (1 + row.values)
            weights_df.loc[date, assets] = values / values.sum()
        end_date = group.index[-1]
        pre_weights = weights_df.loc[end_date].values
        turnover = 0.5 * np.abs(target - pre_weights).sum()
        turnovers[end_date] = turnover
        # rebalance
        values = values.sum() * target

    return weights_df, pd.Series(turnovers)


def generate_random_portfolios(returns: pd.DataFrame, num_portfolios: int = 2000, seed: int = 42) -> pd.DataFrame:
    """
    Generate random portfolio allocations and evaluate their metrics.
    
    Args:
        returns: DataFrame of asset daily returns
        num_portfolios: Number of random portfolios to generate (default 2000)
        seed: Random seed for reproducibility (default 42)
    
    Returns:
        DataFrame with one row per portfolio containing:
        - CAGR, AnnualVol, Sharpe, MaxDrawdown metrics
        - w_<asset> columns for each asset weight
    """
    np.random.seed(seed)
    assets = list(returns.columns)
    n = len(assets)
    samples = np.random.dirichlet(np.ones(n), size=num_portfolios)

    rows = []
    for s in samples:
        w = {asset: float(s[i]) for i, asset in enumerate(assets)}
        port_rets = portfolio_return_series(returns, w)
        mets = portfolio_metrics_from_returns(port_rets)
        row = {
            "CAGR": mets["CAGR"],
            "AnnualVol": mets["AnnualVol"],
            "Sharpe": mets["Sharpe"],
            "MaxDrawdown": mets["MaxDrawdown"]
        }
        # add weights
        for i, asset in enumerate(assets):
            row[f"w_{asset}"] = s[i]
        rows.append(row)

    df = pd.DataFrame(rows)
    df["Return"] = df["CAGR"]
    return df


def optimize_portfolio_sharpe(
    returns: pd.DataFrame,
    min_weight: float = 0.0,
    max_weight: float = 1.0,
    risk_free_rate: float = 0.0
) -> Dict[str, float]:
    """
    Find portfolio weights that maximize Sharpe ratio.
    
    Args:
        returns: DataFrame of asset daily returns
        min_weight: Minimum weight per asset (default 0.0 = no short sales)
        max_weight: Maximum weight per asset (default 1.0 = no leverage)
        risk_free_rate: Risk-free rate for Sharpe calculation (default 0.0)
    
    Returns:
        Dictionary mapping asset names to optimal weights
    """
    assets = list(returns.columns)
    n = len(assets)
    
    # Annualized metrics
    mean_returns = returns.mean() * TRADING_DAYS
    cov_matrix = returns.cov() * TRADING_DAYS
    
    # Objective: negative Sharpe ratio (minimize to maximize Sharpe)
    def neg_sharpe(w):
        port_return = np.dot(w, mean_returns)
        port_vol = np.sqrt(np.dot(w, np.dot(cov_matrix, w)))
        if port_vol == 0:
            return 1e6
        return -(port_return - risk_free_rate) / port_vol
    
    # Constraints
    constraints = [
        {'type': 'eq', 'fun': lambda w: np.sum(w) - 1}  # weights sum to 1
    ]
    
    # Bounds: each weight between min_weight and max_weight
    bounds = tuple([(min_weight, max_weight) for _ in range(n)])
    
    # Initial guess: equal-weight
    x0 = np.array([1.0 / n for _ in range(n)])
    
    # Optimize
    result = minimize(
        neg_sharpe,
        x0,
        method='SLSQP',
        bounds=bounds,
        constraints=constraints,
        options={'ftol': 1e-9, 'maxiter': 1000}
    )
    
    if not result.success:
        print(f"⚠ Optimization warning: {result.message}")
    
    # Convert to dictionary
    opt_weights = {assets[i]: float(result.x[i]) for i in range(n)}
    
    return opt_weights
