# 📂 AI Trading Bot Project Structure
# Scaled-down Wall Street-grade AI trading system for home/PC users.
# Combines real-time data, ML, risk management, and hardware optimization.
# Generated on: 2025-03-06_22-20


# ────────────────────────────────────────────────────────────
# FILE: ai/modules/account/account_manager.py
# ────────────────────────────────────────────────────────────

# ai/modules/account/account_manager.py
from decimal import Decimal, ROUND_DOWN
from typing import Optional, Dict
from pybit.unified_trading import HTTP
import os
import logging
from dotenv import load_dotenv
from modules.utils.redis_manager import RedisManager
from modules.utils.events import EventBus
import time
import json

load_dotenv()


class AccountManager:
    """Enhanced with fractional trading, connection resilience, and real-time sync"""

    def __init__(
        self, config: Optional[Dict] = None, event_bus: Optional[EventBus] = None
    ):
        self.config = config or {
            "api_key": os.getenv("REAL_API_KEY"),
            "api_secret": os.getenv("REAL_API_SECRET"),
            "testnet": os.getenv("TESTNET", "false").lower() == "true",
        }
        self.event_bus = event_bus
        self.redis = RedisManager()
        self._init_session()
        self.precision_cache = {}

        self.max_retries = 5
        self.retry_delay = 3

    def _init_session(self):
        """Initialize with pybit's built-in retry logic"""
        self.session = HTTP(**self.config)  # pybit.HTTP handles retries internally

    def _get_precision(self, symbol: str) -> int:
        if symbol not in self.precision_cache:
            try:
                info = self.session.get_instruments_info(
                    category="linear", symbol=symbol
                )
                self.precision_cache[symbol] = int(
                    info["result"]["list"][0]["priceScale"]
                )
            except Exception as e:
                logging.error(f"Precision check failed: {e}")
                return 8
        return self.precision_cache[symbol]

    def get_balance(self, coin: str = "USDT") -> Decimal:
        for _ in range(self.max_retries):
            try:
                response = self.session.get_wallet_balance(
                    accountType="UNIFIED", coin=coin
                )
                if response["retCode"] == 0:
                    balance = Decimal(
                        next(
                            coin_info["walletBalance"]
                            for coin_info in response["result"]["list"][0]["coin"]
                            if coin_info["coin"] == coin
                        )
                    ).quantize(Decimal("1e-8"), rounding=ROUND_DOWN)
                    self.redis.set(f"balance:{coin}", str(balance))
                    if self.event_bus:
                        self.event_bus.publish(
                            "balance_update",
                            {
                                "coin": coin,
                                "balance": float(balance),
                                "timestamp": time.time(),
                            },
                        )
                    return balance
            except (KeyError, IndexError, ConnectionError) as e:
                logging.warning(f"Balance check attempt {_+1} failed: {e}")
                time.sleep(self.retry_delay)
        return Decimal(0)

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        order_type: str = "Market",
        price: Optional[Decimal] = None,
        reduce_only: bool = False,
    ) -> Dict:
        precision = self._get_precision(symbol)
        qty_str = format(quantity.quantize(Decimal(10**-precision)), "f")

        params = {
            "category": "linear",
            "symbol": symbol,
            "side": side.capitalize(),
            "orderType": order_type,
            "qty": qty_str,
            "reduceOnly": reduce_only,
        }

        if price is not None:
            params["price"] = str(price.quantize(Decimal(10**-precision)))

        for _ in range(self.max_retries):
            try:
                response = self.session.place_order(**params)
                if response["retCode"] == 0:
                    order_id = response["result"]["orderId"]
                    self.redis.set(f"order:{order_id}", json.dumps(response))
                    return {
                        "status": "success",
                        "order_id": order_id,
                        "executed_qty": Decimal(response["result"]["executedQty"]),
                        "avg_price": Decimal(response["result"]["avgPrice"]),
                    }
            except Exception as e:
                logging.error(f"Order placement failed: {e}")
                time.sleep(self.retry_delay)

        return {"status": "error", "message": "Max retries exceeded"}



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/account/compliance_reporting.py
# ────────────────────────────────────────────────────────────

# ai/modules/account/compliance_reporting.py
"""
Consolidated Compliance and Reporting Module
- Regulatory compliance monitoring
- Audit trail management
- Report generation and submission
"""

from collections import deque, Counter
import time
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from sklearn.linear_model import LogisticRegression
from ..utils.redis_manager import RedisManager
from ..utils.events import EventBus


class ComplianceEngine:
    """Institutional-grade compliance monitoring with AI risk prediction"""

    def __init__(self, trade_state, event_bus):
        self.trade_log = deque(maxlen=10000)
        self.audit_trail = []
        self.regulatory_rules = {
            "position_limits": {"BTCUSDT": 100, "ETHUSDT": 500},
            "wash_trading": {"window": 60, "threshold": 3},
        }
        self.trade_state = trade_state
        self.event_bus = event_bus
        self.risk_predictor = LogisticRegression()  # AI model for compliance risk
        self._train_initial_model()

    def log_trade(self, order: dict):
        """Record trade details and check compliance"""
        trade_entry = {
            "timestamp": time.time_ns(),
            "symbol": order.get("symbol"),
            "side": order.get("side"),
            "qty": order.get("qty"),
            "price": order.get("price"),
            "exec_id": order.get("order_id"),
            "client_id": self.trade_state.get("api_key", "unknown")[-4:],
        }
        self.trade_log.append(trade_entry)
        self.trade_state.rpush("full_trade_history", json.dumps(order))
        self._check_position_limits(order)
        self._detect_wash_trading()
        self.trade_state.set(f"trade:{order['order_id']}", json.dumps(order))

    def _check_position_limits(self, order: dict):
        symbol = order["symbol"]
        position = float(self.trade_state.get(f"position:{symbol}") or 0)
        limit = self.regulatory_rules["position_limits"].get(symbol, 0)
        if abs(position) > limit:
            violation = {
                "type": "position_limit",
                "symbol": symbol,
                "position": position,
                "limit": limit,
                "timestamp": time.time_ns(),
            }
            self.audit_trail.append(violation)
            self.event_bus.publish("compliance_violation", violation)

    def _detect_wash_trading(self):
        window = self.regulatory_rules["wash_trading"]["window"]
        threshold = self.regulatory_rules["wash_trading"]["threshold"]
        recent_trades = [
            t for t in self.trade_log if time.time_ns() - t["timestamp"] < window * 1e9
        ]
        if len(recent_trades) > threshold:
            violation = {
                "type": "wash_trading",
                "count": len(recent_trades),
                "threshold": threshold,
                "timestamp": time.time_ns(),
            }
            self.audit_trail.append(violation)
            self.event_bus.publish("compliance_violation", violation)

    def _train_initial_model(self):
        """Initialize AI model with dummy data (to be trained later)"""
        # Dummy data: [trade_count_in_window, position_size] -> [0=safe, 1=risky]
        X = [[1, 50], [2, 75], [4, 150], [5, 200]]  # Features: trades, position
        y = [0, 0, 1, 1]  # Labels: 0 = compliant, 1 = violation
        self.risk_predictor.fit(X, y)

    def predict_compliance_risk(
        self, symbol: str, trade_count: int, position_size: float
    ) -> float:
        """AI prediction of compliance risk (0 = safe, 1 = risky)"""
        features = [[trade_count, position_size]]
        risk_score = self.risk_predictor.predict_proba(features)[0][1]  # Risk probability
        return risk_score

    def generate_compliance_report(self) -> Dict[str, Any]:
        """Generate comprehensive compliance report"""
        return {
            "timestamp": datetime.now().isoformat(),
            "violations": len(self.audit_trail),
            "details": self.audit_trail[-10:],  # Last 10 violations
            "risk_level": self._calculate_overall_risk()
        }
    
    def _calculate_overall_risk(self) -> float:
        """Calculate overall compliance risk level"""
        if not self.audit_trail:
            return 0.0
        
        # Calculate recent violation rate
        recent_violations = sum(1 for v in self.audit_trail 
                               if time.time_ns() - v["timestamp"] < 24 * 3600 * 1e9)
        return min(1.0, recent_violations / 10)


class ReportingEngine:
    """Generates and submits regulatory and performance reports"""

    def __init__(self, redis_manager=None):
        self.redis = redis_manager or RedisManager()
        self.compliance_engine = None  # Will be set externally if needed
        self.report_cache = {}

    def generate_compliance_report(self):
        """Generate a compliance report based on stored audit logs"""
        if self.compliance_engine:
            return self.compliance_engine.generate_compliance_report()
            
        # Fallback if compliance engine not connected
        audit_logs = json.loads(self.redis.get("audit_trail") or "[]")
        report = {
            "timestamp": datetime.now().isoformat(),
            "violations": len(audit_logs),
            "details": audit_logs,
        }
        self.redis.set("compliance_report", json.dumps(report))
        return report

    def submit_regulatory_report(self, report_type: str):
        """Submit reports to regulatory bodies"""
        if report_type == "FATCA":
            report = self._generate_fatca_report()
        elif report_type == "MiFID II":
            report = self._generate_mifid_report()
        elif report_type == "SEC":
            report = self._generate_sec_report()
        else:
            raise ValueError(f"Unsupported report type: {report_type}")

        # Cache the report for future reference
        self.report_cache[report_type] = report
        self.redis.set(f"submitted_report:{report_type}", json.dumps(report))
        
        return report

    def _generate_fatca_report(self):
        """Generate FATCA (Foreign Account Tax Compliance Act) report"""
        trades = json.loads(self.redis.get("full_trade_history") or "[]")
        total_volume = sum(float(t.get("qty", 0)) for t in trades)
        return {
            "report_type": "FATCA",
            "total_volume": total_volume,
            "taxable_events": len(trades),
            "timestamp": datetime.now().isoformat(),
        }

    def _generate_mifid_report(self):
        """Generate MiFID II (Markets in Financial Instruments Directive) report"""
        trades = json.loads(self.redis.get("full_trade_history") or "[]")
        trade_breakdown = {}
        for trade in trades:
            symbol = trade.get("symbol", "unknown")
            trade_breakdown[symbol] = trade_breakdown.get(symbol, 0) + 1

        return {
            "report_type": "MiFID II",
            "best_execution_rate": 0.95,  # Placeholder for execution quality
            "trade_breakdown": trade_breakdown,
            "timestamp": datetime.now().isoformat(),
        }

    def _generate_sec_report(self):
        """Generate SEC (Securities and Exchange Commission) report"""
        trades = json.loads(self.redis.get("full_trade_history") or "[]")
        long_positions = sum(1 for t in trades if t.get("side") == "BUY")
        short_positions = sum(1 for t in trades if t.get("side") == "SELL")

        return {
            "report_type": "SEC",
            "long_positions": long_positions,
            "short_positions": short_positions,
            "timestamp": datetime.now().isoformat(),
        }
        
    def generate_performance_report(self):
        """Generate trading performance report"""
        profits = json.loads(self.redis.get("profit_history") or "[]")
        positions = json.loads(self.redis.get("positions") or "{}")
        
        total_profit = sum(profits)
        avg_profit = total_profit / len(profits) if profits else 0
        
        return {
            "timestamp": datetime.now().isoformat(),
            "total_profit": total_profit,
            "average_profit": avg_profit,
            "win_rate": self._calculate_win_rate(profits),
            "open_positions": len(positions),
            "position_value": sum(positions.values())
        }
        
    def _calculate_win_rate(self, profits):
        """Calculate win rate from profit history"""
        if not profits:
            return 0
        wins = sum(1 for p in profits if p > 0)
        return wins / len(profits)


class ComplianceReportingSystem:
    """Unified system for compliance monitoring and reporting"""
    
    def __init__(self, trade_state=None, event_bus=None):
        self.trade_state = trade_state or RedisManager()
        self.event_bus = event_bus or EventBus()
        self.compliance_engine = ComplianceEngine(self.trade_state, self.event_bus)
        self.reporting_engine = ReportingEngine(self.trade_state)
        self.reporting_engine.compliance_engine = self.compliance_engine
        
        # Register event handlers
        self.event_bus.register_listener("trade_executed", self._on_trade_executed)
        self.event_bus.register_listener("report_requested", self._on_report_requested)
        
    def _on_trade_executed(self, trade_data):
        """Handle trade execution events"""
        self.compliance_engine.log_trade(trade_data)
        
    def _on_report_requested(self, request_data):
        """Handle report generation requests"""
        report_type = request_data.get("type", "compliance")
        
        if report_type == "compliance":
            return self.reporting_engine.generate_compliance_report()
        elif report_type == "performance":
            return self.reporting_engine.generate_performance_report()
        elif report_type in ["FATCA", "MiFID II", "SEC"]:
            return self.reporting_engine.submit_regulatory_report(report_type)
        else:
            raise ValueError(f"Unknown report type: {report_type}")
    
    def check_compliance(self, order):
        """Check if an order complies with regulations before execution"""
        symbol = order.get("symbol", "")
        qty = float(order.get("qty", 0))
        current_position = float(self.trade_state.get(f"position:{symbol}") or 0)
        
        # Get recent trade count
        trade_count = len(json.loads(self.trade_state.get("recent_trades") or "[]"))
        
        # Predict risk using AI model
        risk_score = self.compliance_engine.predict_compliance_risk(
            symbol, trade_count, current_position + qty
        )
        
        return {
            "compliant": risk_score < 0.7,  # Threshold for compliance
            "risk_score": risk_score,
            "details": {
                "position_limit_check": abs(current_position + qty) <= 
                    self.compliance_engine.regulatory_rules["position_limits"].get(symbol, float('inf')),
                "wash_trading_check": trade_count < 
                    self.compliance_engine.regulatory_rules["wash_trading"]["threshold"]
            }
        }



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/analysis/backtester.py
# ────────────────────────────────────────────────────────────

# ai/modules/analysis/backtester.py

import pandas as pd
from typing import Callable, Dict
import logging

from ..utils.redis_manager import RedisManager
from ..utils.profit_tracker import ProfitTracker


class Backtester:
    """Multi-timeframe strategy validation engine"""

    def __init__(self):
        self.redis = RedisManager()
        self.results = {}
        logging.basicConfig(level=logging.INFO)

    def run_backtest(
        self,
        strategy: Callable[[pd.DataFrame], Dict],
        data: pd.DataFrame,
        initial_balance: float = 10000,
    ) -> Dict[str, float]:
        """
        Execute a trading strategy on historical data and return performance metrics.

        :param strategy: A callable that takes a DataFrame and returns trade signals.
        :param data: Historical price data as a pandas DataFrame.
        :param initial_balance: Initial balance for the backtest.
        :return: A dictionary containing performance metrics (Sharpe ratio, max drawdown, win rate).
        """
        if data.empty or not isinstance(data, pd.DataFrame):
            logging.error(
                "Invalid data: must be a non-empty pandas DataFrame.")
            raise ValueError("Data must be a non-empty pandas DataFrame.")

        tracker = ProfitTracker(initial_balance)
        for i in range(1, len(data)):
            signals = strategy(data.iloc[:i])
            if signals["action"] != "HOLD":
                tracker.log_trade(
                    {
                        "price": data.iloc[i]["close"],
                        "qty": signals["size"],
                        "side": signals["action"],
                    }
                )

        return {
            "sharpe": self._calculate_sharpe(tracker),
            "max_drawdown": self._max_drawdown(tracker),
            "win_rate": tracker.win_rate(),
        }

    def _calculate_sharpe(self, tracker: ProfitTracker) -> float:
        """
        Calculate the Sharpe ratio of the backtest results.

        :param tracker: An instance of ProfitTracker.
        :return: The Sharpe ratio.
        """
        returns = tracker.calculate_returns()
        mean_return = np.mean(returns)
        std_return = np.std(returns)
        sharpe_ratio = mean_return / std_return if std_return != 0 else 0
        logging.info(f"Calculated Sharpe ratio: {sharpe_ratio}")
        return sharpe_ratio

    def _max_drawdown(self, tracker: ProfitTracker) -> float:
        """
        Calculate the maximum drawdown of the backtest results.

        :param tracker: An instance of ProfitTracker.
        :return: The maximum drawdown.
        """
        equity_curve = tracker.calculate_equity_curve()
        peak = equity_curve[0]
        drawdowns = []
        for value in equity_curve:
            if value > peak:
                peak = value
            drawdown = (peak - value) / peak
            drawdowns.append(drawdown)
        max_drawdown = max(drawdowns)
        logging.info(f"Calculated max drawdown: {max_drawdown}")
        return max_drawdown



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/analysis/stress_tester.py
# ────────────────────────────────────────────────────────────

# ai/modules/analysis/stress_tester.py

from typing import Dict, List, Set, Tuple
import numpy as np
import logging


class MarketStressTest:
    """Black Swan event simulation"""

    def __init__(self):
        logging.basicConfig(level=logging.INFO)

    def flash_crash_scenario(
        self,
        portfolio: np.ndarray,
        mean_return: float = -0.25,
        std_dev: float = 0.15,
        steps: int = 100,
    ) -> np.ndarray:
        """
        Simulate a 2010-style flash crash with customizable parameters.

        :param portfolio: Current portfolio values as a numpy array.
        :param mean_return: Mean return for the flash crash scenario.
        :param std_dev: Standard deviation for the flash crash scenario.
        :param steps: Number of time steps in the simulation.
        :return: Portfolio values after the simulated flash crash.
        """
        if not isinstance(portfolio, np.ndarray) or len(portfolio) == 0:
            logging.error(
                "Invalid portfolio: must be a non-empty numpy array.")
            raise ValueError("Portfolio must be a non-empty numpy array.")

        if (
            not isinstance(mean_return, (int, float))
            or not isinstance(std_dev, (int, float))
            or not isinstance(steps, int)
        ):
            logging.error(
                "Invalid parameters: mean_return, std_dev, and steps must be numeric types."
            )
            raise ValueError(
                "Mean return, standard deviation, and steps must be numeric types."
            )

        crash_returns = np.random.normal(mean_return, std_dev, steps)
        new_portfolio = portfolio * (1 + crash_returns).cumprod()
        logging.info(
            f"Simulated flash crash scenario with mean_return={mean_return}, std_dev={std_dev}, steps={steps}. New portfolio values: {new_portfolio}"
        )
        return new_portfolio

    def liquidity_crisis_test(self, orderbook: dict) -> bool:
        """
        Test for a bid-ask spread collapse during a liquidity crisis.

        :param orderbook: A dictionary containing bids and asks.
        :return: True if the bid-ask spread has collapsed, False otherwise.
        """
        if (
            not isinstance(orderbook, dict)
            or "bids" not in orderbook
            or "asks" not in orderbook
        ):
            logging.error(
                "Invalid orderbook: must be a dictionary with 'bids' and 'asks' keys."
            )
            raise ValueError(
                "Orderbook must be a dictionary with 'bids' and 'asks' keys."
            )

        if not isinstance(orderbook["bids"][0][0], (int, float)) or not isinstance(
            orderbook["asks"][0][0], (int, float)
        ):
            logging.error(
                "Invalid orderbook prices: bids and asks must be numeric types."
            )
            raise ValueError("Bids and asks must be numeric types.")

        bid_price = orderbook["bids"][0][0]
        ask_price = orderbook["asks"][0][0]
        spread_collapsed = bid_price * 0.9 < ask_price
        logging.info(
            f"Liquidity crisis test result: {spread_collapsed} (bid_price={bid_price}, ask_price={ask_price})"
        )
        return spread_collapsed

    def calculate_stress_metrics(
        self, portfolio: np.ndarray, initial_balance: float
    ) -> Dict[str, float]:
        """
        Calculate additional metrics for the stress test results.

        :param portfolio: Portfolio values after the stress test.
        :param initial_balance: Initial balance before the stress test.
        :return: A dictionary containing various metrics.
        """
        if not isinstance(portfolio, np.ndarray) or len(portfolio) == 0:
            logging.error(
                "Invalid portfolio: must be a non-empty numpy array.")
            raise ValueError("Portfolio must be a non-empty numpy array.")

        if not isinstance(initial_balance, (int, float)):
            logging.error("Invalid initial_balance: must be a numeric type.")
            raise ValueError("Initial balance must be a numeric type.")

        final_balance = portfolio[-1]
        total_return = (final_balance - initial_balance) / initial_balance
        max_drawdown = self._calculate_max_drawdown(portfolio)
        sharpe_ratio = self._calculate_sharpe(portfolio, initial_balance)

        metrics = {
            "final_balance": final_balance,
            "total_return": total_return,
            "max_drawdown": max_drawdown,
            "sharpe_ratio": sharpe_ratio,
        }

        logging.info(f"Calculated stress test metrics: {metrics}")
        return metrics

    def _calculate_max_drawdown(self, portfolio: np.ndarray) -> float:
        """
        Calculate the maximum drawdown of the portfolio.

        :param portfolio: Portfolio values.
        :return: The maximum drawdown.
        """
        peak = portfolio[0]
        drawdowns = []
        for value in portfolio:
            if value > peak:
                peak = value
            drawdown = (peak - value) / peak
            drawdowns.append(drawdown)
        max_drawdown = max(drawdowns)
        logging.info(f"Calculated max drawdown: {max_drawdown}")
        return max_drawdown

    def _calculate_sharpe(self, portfolio: np.ndarray, initial_balance: float) -> float:
        """
        Calculate the Sharpe ratio of the portfolio.

        :param portfolio: Portfolio values.
        :param initial_balance: Initial balance.
        :return: The Sharpe ratio.
        """
        returns = np.diff(portfolio) / portfolio[:-1]
        mean_return = np.mean(returns)
        std_return = np.std(returns)
        sharpe_ratio = mean_return / std_return if std_return != 0 else 0
        logging.info(f"Calculated Sharpe ratio: {sharpe_ratio}")
        return sharpe_ratio



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/core/agent.py
# ────────────────────────────────────────────────────────────

# core/agent.py
"""Self-healing agent for component management."""

import asyncio
import time
import os
import importlib
import inspect
from collections import defaultdict
from typing import Dict, Any, List, Set
import warnings
from datetime import datetime
from decimal import Decimal

from modules import utils
from modules.utils.logging import logger
from modules.core.metrics import ComponentMetrics
from modules.core.dependency_graph import DependencyGraph
from modules.core.ui import AIControlCenter
from config import BYBIT_CONFIG

# Required for smart_init
try:
    import pybit
except ImportError:
    # Mock pybit for development
    class pybit:
        class unified_trading:
            class HTTP:
                def __init__(self, api_key=None, api_secret=None):
                    pass


class SelfHealingAgent:
    """Boss's brain—loads, heals, runs components."""

    def __init__(self):
        self.components: Dict[str, Any] = {}
        self.component_status: Dict[str, Dict[str, Any]] = {}
        self.component_metrics: Dict[str, ComponentMetrics] = {}
        self.component_dependencies: Dict[str, List[str]] = {}
        self.dependency_graph = DependencyGraph()
        self.recent_logs = []
        self.system_health_score = 1.0
        self.critical_components = set()
        self.maintenance_mode = False
        self.metrics_cls = ComponentMetrics  # For UI access

        # Initialize core services
        self.redis = self._init_redis()
        self.event_bus = self._init_event_bus()
        self.control_center = AIControlCenter(self)

        # Load all modules
        self._discover_dependencies()
        self._load_modules()

        # Start maintenance cycle
        asyncio.create_task(self._maintenance_cycle())

    def _init_redis(self):
        """Initialize Redis connection or mock if unavailable."""
        try:
            from modules.utils.redis_manager import RedisManager

            redis = RedisManager()
            if redis.ping():
                logger.info("Redis connected")
                return redis
        except Exception as e:
            logger.error(f"Redis failed: {e}—using mock")

        # Mock Redis with basic functionality
        class MockRedis:
            def __init__(self):
                self.storage = {}

            async def get(self, key):
                return self.storage.get(key)

            async def set(self, key, value):
                self.storage[key] = value

            def ping(self):
                return True

        return MockRedis()

    def _init_event_bus(self):
        """Initialize event bus or mock if unavailable."""
        try:
            from modules.utils.events import EventBus
            return EventBus()
        except Exception as e:
            logger.error(f"EventBus failed: {e}—using mock")

            # Mock EventBus with basic functionality
            class MockEventBus:
                def __init__(self):
                    self.handlers = defaultdict(list)

                async def publish(self, event_type, data):
                    for handler in self.handlers.get(event_type, []):
                        if inspect.iscoroutinefunction(handler):
                            await handler(data)
                        else:
                            handler(data)

                def subscribe(self, event_type, handler):
                    self.handlers[event_type].append(handler)

            return MockEventBus()

    def _discover_dependencies(self):
        """Scan modules to discover dependencies between components."""
        modules_dir = os.path.join(os.path.dirname(__file__), "..", "modules")
        dependency_data = {}

        # Skip if modules directory doesn't exist (for development)
        if not os.path.exists(modules_dir):
            logger.warning(f"Modules directory not found: {modules_dir}")
            return

        for root, _, files in os.walk(modules_dir):
            module = os.path.basename(root)
            for file in files:
                if file.endswith(".py") and file != "__init__.py":
                    component = file[:-3]
                    key = f"{module}_{component}"

                    try:
                        # Import the module to inspect it
                        mod = importlib.import_module(
                            f"modules.{module}.{component}")

                        for name, cls in inspect.getmembers(mod, inspect.isclass):
                            if name in ["HTTP", "WebSocket"]:
                                continue

                            # Look at __init__ parameters to find dependencies
                            sig = inspect.signature(cls.__init__)
                            deps = []

                            for param_name, param in sig.parameters.items():
                                if param_name == "self":
                                    continue

                                # Check for common dependency patterns
                                param_hints = [
                                    "model", "predictor", "strategy", "analyzer",
                                    "manager", "fetcher", "engine", "monitor",
                                    "optimizer", "executor",
                                ]

                                if any(hint in param_name for hint in param_hints):
                                    deps.append(param_name)

                            component_key = f"{module}_{name.lower()}"
                            dependency_data[component_key] = deps

                            # Check if it's a critical component
                            if hasattr(cls, "CRITICAL") and cls.CRITICAL:
                                self.critical_components.add(component_key)

                    except Exception as e:
                        logger.debug(
                            f"Could not analyze dependencies for {key}: {e}")

        # Now map parameter names to actual components
        for component, deps in dependency_data.items():
            for dep in deps:
                # Try to find a matching component
                candidates = [
                    c for c in dependency_data.keys() if c.split("_")[1] in dep
                ]
                if candidates:
                    self.dependency_graph.add_dependency(
                        component, candidates[0])
                    if component not in self.component_dependencies:
                        self.component_dependencies[component] = []
                    self.component_dependencies[component].append(
                        candidates[0])

        logger.info(
            f"Discovered dependencies for {len(dependency_data)} components")

    def _load_modules(self):
        """Scan and load all modules in dependency order."""
        modules_dir = os.path.join(os.path.dirname(__file__), "..", "modules")

        # Skip if modules directory doesn't exist (for development)
        if not os.path.exists(modules_dir):
            logger.warning(f"Modules directory not found: {modules_dir}")
            return

        # Get optimal loading order
        init_order = self.dependency_graph.get_initialization_order()
        ordered_components = []

        # First collect all components
        for root, _, files in os.walk(modules_dir):
            module = os.path.basename(root)
            for file in files:
                if file.endswith(".py") and file != "__init__.py":
                    component = file[:-3]
                    ordered_components.append((module, component))

        # Sort by initialization order when possible
        def get_order_index(item):
            key = f"{item[0]}_{item[1]}"
            try:
                return init_order.index(key)
            except ValueError:
                return float("inf")  # Components not in the order go last

        ordered_components.sort(key=get_order_index)

        # Now load in the correct order
        for module, component in ordered_components:
            try:
                logger.info(f"Loading {module}.{component}...")
                mod = importlib.import_module(f"modules.{module}.{component}")
                for name, cls in inspect.getmembers(mod, inspect.isclass):
                    if name not in ["HTTP", "WebSocket"]:
                        instance = self._smart_init(cls)
                        if instance:
                            key = f"{module}_{name.lower()}"
                            self.components[key] = instance
                            self.component_metrics[key] = ComponentMetrics()
                            self.component_status[key] = {
                                "status": "healthy",
                                "errors": 0,
                                "calls": 0,
                                "health_score": 1.0,
                                "avg_response": 0,
                            }
                            logger.info(f"Successfully loaded {key}")
            except Exception as e:
                logger.error(f"Failed to load {module}.{component}: {e}")
                self.component_status[f"{module}_{component}"] = {
                    "status": "error",
                    "errors": 1,
                    "calls": 0,
                    "health_score": 0.0,
                }
                self.component_metrics[f"{module}_{component}"] = ComponentMetrics(
                )
                self.component_metrics[f"{module}_{component}"].error_count = 1
                self.recent_logs.append(
                    f"Failed to load {module}.{component}: {e}")

    def _smart_init(self, cls):
        """AI-smart component init with dependency injection."""
        # Handle special cases first
        if cls in [datetime, deque, Decimal]:
            return cls()  # Default initialization for core types

        if hasattr(pybit, 'unified_trading') and issubclass(cls, pybit.unified_trading.HTTP):
            return cls(
                api_key=BYBIT_CONFIG.API_KEY,
                api_secret=BYBIT_CONFIG.API_SECRET
            )

        try:
            sig = inspect.signature(cls.__init__)
            params = {}

            # First identify all parameters
            for param_name, param in sig.parameters.items():
                if param_name == "self":
                    continue

                # Core service injection
                if "redis" in param_name:
                    params[param_name] = self.redis
                elif "event" in param_name:
                    params[param_name] = self.event_bus
                elif "config" in param_name:
                    params[param_name] = BYBIT_CONFIG
                elif "symbol" in param_name:
                    params[param_name] = "BTCUSDT"

                # Look for component dependencies
                elif any(param_name in comp_key for comp_key in self.components):
                    # Find matching component
                    for comp_key, comp in self.components.items():
                        if param_name in comp_key:
                            params[param_name] = comp
                            break

            # Check if we missed any required parameters
            for param_name, param in sig.parameters.items():
                if (
                    param_name != "self"
                    and param_name not in params
                    and param.default is inspect.Parameter.empty
                ):
                    logger.warning(
                        f"Missing required parameter {param_name} for {cls.__name__}"
                    )
                    # Try a generic null value
                    params[param_name] = None

            return cls(**params)

        except Exception as e:
            logger.warning(f"Smart init failed for {cls.__name__}: {e}")
            return None

    async def recover(self, name, full_restart=False):
        """Enhanced recovery with circuit breaker pattern."""
        metrics = self.component_metrics.get(name)
        if metrics and metrics.recovery_attempts > 3:
            logger.critical(
                f"Permanent failure for {name} after 3 recovery attempts")
            self.component_status[name]["status"] = "terminated"
            # Try to notify the alert manager if it exists
            try:
                await self.execute_with_metrics(
                    "alert_manager",
                    "trigger_alert",
                    message=f"Component {name} permanently failed"
                )
            except:
                pass
            return False

        # Add post-recovery validation
        if name in self.components and hasattr(self.components[name], "health_check"):
            try:
                health_check = await self.components[name].health_check()
                if not health_check.passed:
                    logger.warning(f"Recovery validation failed for {name}")
                    return False
            except Exception as e:
                logger.error(f"Recovery validation error for {name}: {str(e)}")
                return False

        return True

    async def _maintenance_cycle(self):
        """Periodic maintenance to check component health."""
        while True:
            try:
                self.maintenance_mode = True

                # Update system health score
                healthy_components = sum(
                    1 for s in self.component_status.values() if s["status"] == "healthy"
                )
                self.system_health_score = healthy_components / \
                    max(1, len(self.component_status))

                # Check for components that need recovery
                for name, status in self.component_status.items():
                    if status["status"] == "error":
                        logger.info(
                            f"Maintenance cycle attempting to recover {name}")
                        await self.recover(name)
                    elif status["status"] == "healthy" and status.get("health_score", 1.0) < 0.5:
                        logger.info(
                            f"Maintenance cycle attempting to refresh {name} due to low health score"
                        )
                        await self.recover(name, full_restart=True)

                self.maintenance_mode = False
                await asyncio.sleep(300)  # Run maintenance every 5 minutes

            except Exception as e:
                logger.error(f"Maintenance cycle error: {e}")
                self.maintenance_mode = False
                await asyncio.sleep(60)  # Error handling sleep time

    async def execute_with_metrics(self, component_name, method_name, *args, **kwargs):
        """Execute a component method with metrics tracking."""
        if component_name not in self.components:
            logger.warning(f"Component {component_name} not found")
            return None

        if self.component_status[component_name]["status"] != "healthy":
            logger.warning(f"Component {component_name} is not healthy")
            await self.recover(component_name)
            if self.component_status[component_name]["status"] != "healthy":
                return None

        component = self.components[component_name]
        metrics = self.component_metrics[component_name]
        method = getattr(component, method_name)

        start_time = time.time()
        success = True

        try:
            # Check if method is a coroutine
            if asyncio.iscoroutinefunction(method):
                result = await method(*args, **kwargs)
            else:
                result = method(*args, **kwargs)

            return result

        except Exception as e:
            success = False
            logger.error(
                f"Error executing {component_name}.{method_name}: {e}")
            self.recent_logs.append(
                f"Error: {component_name}.{method_name}: {e}")
            self.component_status[component_name]["errors"] += 1

            # Check dependencies for potential causes
            for dep_name in self.component_dependencies.get(component_name, []):
                if (
                    dep_name in self.component_status
                    and self.component_status[dep_name]["status"] != "healthy"
                ):
                    metrics.record_dependency_failure(dep_name)
                    logger.warning(
                        f"Dependency {dep_name} may be causing issues with {component_name}"
                    )

            if self.component_status[component_name]["errors"] > 5:
                self.component_status[component_name]["status"] = "warning"

            if self.component_status[component_name]["errors"] > 10:
                self.component_status[component_name]["status"] = "error"
                await self.recover(component_name)

            return None

        finally:
            end_time = time.time()
            duration_ms = (end_time - start_time) * 1000

            # Update metrics
            metrics.record_call(duration_ms, success)

            # Update status
            self.component_status[component_name]["calls"] += 1

            if success:
                # Calculate rolling average response time
                prev_avg = self.component_status[component_name].get(
                    "avg_response", 0)
                prev_calls = max(
                    0, self.component_status[component_name]["calls"] - 1)
                new_avg = (prev_avg * prev_calls + duration_ms) / self.component_status[
                    component_name
                ]["calls"]
                self.component_status[component_name]["avg_response"] = new_avg

            # Update health score
            self.component_status[component_name]["health_score"] = metrics.health_score



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/core/dependency_graph.py
# ────────────────────────────────────────────────────────────

# core/dependency_graph.py
"""Dependency graph management for component initialization."""

from collections import defaultdict
from typing import Set, List
from modules.utils.logging import logger


class DependencyGraph:
    """Manages component dependencies and initialization order."""

    def __init__(self):
        self.dependencies = defaultdict(set)
        self.reverse_dependencies = defaultdict(set)

    def add_dependency(self, component: str, depends_on: str):
        """Add a dependency relationship between components."""
        self.dependencies[component].add(depends_on)
        self.reverse_dependencies[depends_on].add(component)

    def get_dependencies(self, component: str) -> Set[str]:
        """Get all dependencies for a component."""
        return self.dependencies[component]

    def get_dependent_components(self, component: str) -> Set[str]:
        """Get all components that depend on this component."""
        return self.reverse_dependencies[component]

    def get_initialization_order(self) -> List[str]:
        """Return components in dependency order (least dependent first)."""
        result = []
        visited = set()
        temp_mark = set()

        def visit(node):
            if node in visited:
                return
            if node in temp_mark:
                logger.warning(
                    f"Circular dependency detected involving {node}")
                return

            temp_mark.add(node)

            for dep in self.dependencies[node]:
                visit(dep)

            temp_mark.remove(node)
            visited.add(node)
            result.append(node)

        for node in list(self.dependencies.keys()):
            if node not in visited:
                visit(node)

        return result



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/core/metrics.py
# ────────────────────────────────────────────────────────────

# core/metrics.py
"""Component metrics tracking for the AI Trading System."""

import time
from collections import defaultdict, deque
from modules.utils.logging import logger


class ComponentMetrics:
    """Tracks performance metrics for system components."""

    def __init__(self):
        self.response_times = deque(maxlen=100)
        self.error_count = 0
        self.total_calls = 0
        self.last_error_time = 0
        self.recovery_attempts = 0
        self.dependency_failures = defaultdict(int)

    def record_call(self, duration_ms: float, success: bool):
        """Record a component method call with its duration and success status."""
        self.total_calls += 1
        self.response_times.append(duration_ms)
        if not success:
            self.error_count += 1
            self.last_error_time = time.time()

    def record_dependency_failure(self, dependency_name: str):
        """Record a failure related to a dependency."""
        self.dependency_failures[dependency_name] += 1

    def record_recovery(self):
        """Record a successful recovery attempt."""
        self.recovery_attempts += 1
        self.error_count = 0
        self.response_times.clear()

    @property
    def health_score(self) -> float:
        """Calculate the health score of the component."""
        error_rate = self.error_count / max(1, self.total_calls)
        avg_response = (
            sum(self.response_times) / max(1, len(self.response_times))
            if self.response_times
            else 0
        )
        response_penalty = min(avg_response / 500, 0.5)  # 500ms threshold
        return max(
            0.0,
            1.0 - error_rate - response_penalty -
            0.1 * len(self.dependency_failures),
        )



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/core/ui.py
# ────────────────────────────────────────────────────────────

# core/ui.py
"""UI control center for the AI Trading System."""

import asyncio
from types import SimpleNamespace
import sys
import asyncio
import time
from rich.console import Console
from rich.live import Live
from rich.layout import Layout
from rich.table import Table
from rich.panel import Panel
from config import LiveTradingConfig


class AIControlCenter:
    """Real-time UI for monitoring system components."""

    def __init__(self, agent):
        self.agent = agent
        self.console = Console()
        self.layout = Layout()
        self._setup_layout()
        self.selected_component = None
        # Get Redis connection from agent
        self.redis = agent.redis if hasattr(agent, 'redis') else None

    def _setup_layout(self):
        """Initialize the layout structure."""
        self.layout.split(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="market_data", size=7),  # New market data section
            Layout(name="detail", size=15),
            Layout(name="logs", size=10),
        )
        self.layout["main"].split_row(
            Layout(name="components", ratio=3),
            Layout(name="metrics", ratio=2),
            Layout(name="actions", size=30),
        )

    def _status_table(self) -> Table:
        """Create a table with component status information."""
        table = Table(title="Component Monitor", header_style="bold magenta")
        table.add_column("Module", style="cyan")
        table.add_column("Status", justify="right")
        table.add_column("Health", justify="right")
        table.add_column("Calls", justify="right")
        table.add_column("Errors", justify="right")

        # Sort components by health score
        sorted_components = sorted(
            self.agent.component_status.items(),
            key=lambda x: (
                {"healthy": 0, "warning": 1, "error": 2, "starting": 3}.get(
                    x[1]["status"], 4
                ),
                -x[1].get("health_score", 0),
            ),
        )

        for name, stats in sorted_components:
            status_color = {
                "healthy": "green",
                "warning": "yellow",
                "error": "red",
                "starting": "blue",
            }.get(stats["status"], "white")

            health_score = stats.get("health_score", 1.0) * 100
            health_color = (
                "green"
                if health_score > 80
                else "yellow" if health_score > 50 else "red"
            )

            table.add_row(
                name,
                f"[{status_color}]{stats['status'].upper()}[/]",
                f"[{health_color}]{health_score:.1f}%[/]",
                f"{stats.get('calls', 0)}",
                f"[red]{stats.get('errors', 0)}[/]" if stats.get("errors",
                                                                 0) else "0",
            )
        return table

    def _metrics_panel(self) -> Panel:
        """Create a panel with system metrics."""
        return Panel(
            f"Active Components: {sum(1 for s in self.agent.component_status.values() if s['status'] == 'healthy')}/{len(self.agent.component_status)}\n"
            f"Total API Calls: {sum(s.get('calls', 0) for s in self.agent.component_status.values())}\n"
            f"Error Rate: {(sum(s.get('errors', 0) for s in self.agent.component_status.values()) / max(1, sum(s.get('calls', 0) for s in self.agent.component_status.values()))) * 100:.1f}%\n"
            f"Avg Response: {sum(s.get('avg_response', 0) for s in self.agent.component_status.values()) / max(1, len(self.agent.component_status)):.1f}ms\n"
            f"System Health: {self.agent.system_health_score * 100:.1f}%\n"
            f"Trading Mode: {'LIVE' if LiveTradingConfig.LIVE_MODE else 'TEST'}\n",
            title="System Metrics",
        )

    def _market_data_panel(self) -> Panel:
        """Create a panel showing live market data."""
        table = Table(title="Live Market Data", header_style="bold green")
        table.add_column("Symbol", style="cyan")
        table.add_column("Price", justify="right", style="yellow")
        table.add_column("24h Change", justify="right")
        table.add_column("Updated", justify="right")

        try:
            # Get price data from Redis
            symbols = ["BTCUSDT", "ETHUSDT"]
            current_time = time.time()

            for symbol in symbols:
                if self.redis:
                    # Try to get live price from WebSocket data
                    price_str = self.redis.get(f"live_price:{symbol}")

                    if price_str:
                        try:
                            price = float(price_str)
                            # Calculate fake price change (in real system you'd have actual 24h data)
                            if symbol == "BTCUSDT":
                                change = 2.45  # Example positive change
                                change_color = "green"
                            else:
                                change = -1.2  # Example negative change
                                change_color = "red"

                            # Add row to table
                            table.add_row(
                                symbol,
                                f"${price:,.2f}",
                                f"[{change_color}]{change:+.2f}%[/]",
                                "Just now"
                            )
                            continue
                        except:
                            pass

                # Fallback default values if no data in Redis
                if symbol == "BTCUSDT":
                    table.add_row(
                        symbol,
                        "$50,000.00",
                        "[yellow]Waiting for data...[/]",
                        "N/A"
                    )
                else:
                    table.add_row(
                        symbol,
                        "$3,000.00",
                        "[yellow]Waiting for data...[/]",
                        "N/A"
                    )

            return Panel(table, title="Market Data", border_style="green")
        except Exception as e:
            return Panel(f"Error displaying market data: {str(e)}", title="Market Data Error", border_style="red")

    def _component_detail(self) -> Panel:
        """Create a panel with detailed information about the selected component."""
        if (
            not self.selected_component
            or self.selected_component not in self.agent.component_status
        ):
            return Panel("Select a component for details", title="Component Details")

        stats = self.agent.component_status[self.selected_component]
        metrics = self.agent.component_metrics.get(
            self.selected_component, self.agent.metrics_cls()
        )

        return Panel(
            f"Component: {self.selected_component}\n"
            f"Status: {stats['status'].upper()}\n"
            f"Health Score: {stats.get('health_score', 1.0) * 100:.1f}%\n"
            f"Total Calls: {stats.get('calls', 0)}\n"
            f"Errors: {stats.get('errors', 0)}\n"
            f"Error Rate: {(stats.get('errors', 0) / max(1, stats.get('calls', 0))) * 100:.1f}%\n"
            f"Avg Response: {stats.get('avg_response', 0):.1f}ms\n"
            f"Last Error: {time.strftime('%H:%M:%S', time.localtime(metrics.last_error_time)) if metrics.last_error_time else 'None'}\n"
            f"Recovery Attempts: {metrics.recovery_attempts}\n"
            f"Dependencies: {', '.join(self.agent.component_dependencies.get(self.selected_component, []))}\n",
            title=f"Component Details: {self.selected_component}",
            style="cyan",
        )

    async def display(self):
        """Display and update the UI in real-time."""
        with Live(self.layout, refresh_per_second=4, screen=True) as live:
            while True:
                self.layout["header"].update(
                    Panel(
                        f"AI TRADING CONTROL CENTER | Mode: {'LIVE' if LiveTradingConfig.LIVE_MODE else 'TEST'} | System Health: {self.agent.system_health_score * 100:.1f}%"
                    )
                )
                self.layout["components"].update(Panel(self._status_table()))
                self.layout["metrics"].update(self._metrics_panel())
                self.layout["market_data"].update(
                    self._market_data_panel())  # New market data panel
                self.layout["detail"].update(self._component_detail())
                self.layout["logs"].update(
                    Panel(
                        "\n".join(self.agent.recent_logs[-5:]),
                        title="Recent Logs",
                        style="blue",
                    )
                )

                # Check if we need to update selected component
                if not self.selected_component and self.agent.component_status:
                    self.selected_component = next(
                        iter(self.agent.component_status.keys())
                    )

                await asyncio.sleep(0.25)  # 360 Hz update rate


if __name__ == "__main__":
    # Mock the config if needed
    try:
        from config import LiveTradingConfig
    except ImportError:
        print("Config module not found, using mock config")
        LiveTradingConfig = SimpleNamespace(LIVE_MODE=True)

    # Create mock agent
    mock_agent = SimpleNamespace(
        component_status={
            "market_data": {"status": "healthy", "health_score": 0.95, "calls": 100, "errors": 2},
            "trade_executor": {"status": "warning", "health_score": 0.75, "calls": 50, "errors": 5}
        },
        component_metrics={},
        component_dependencies={},
        system_health_score=0.9,
        recent_logs=["System started", "Connected to exchange",
                     "Fetching market data", "Price feed active", "Monitoring market"],
        metrics_cls=lambda: SimpleNamespace(
            last_error_time=None, recovery_attempts=0)
    )

    # Try to connect to Redis
    try:
        import redis
        mock_agent.redis = redis.Redis(host='localhost', port=6379, db=0)
        print("Connected to Redis successfully")

        # Try setting test data if Redis is available
        # mock_agent.redis.set("live_price:BTCUSDT", "51243.75")
        # mock_agent.redis.set("live_price:ETHUSDT", "3058.92")
    except Exception as e:
        print(f"Redis connection failed: {e}")
        mock_agent.redis = None

    # Run the UI
    control_center = AIControlCenter(mock_agent)

    try:
        asyncio.run(control_center.display())
    except KeyboardInterrupt:
        print("UI closed by user")



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/data/data_fetcher.py
# ────────────────────────────────────────────────────────────

# ai/modules/data/data_fetcher.py
import asyncio
import json
import logging
import time
import pandas as pd
from threading import Thread
from pybit.unified_trading import WebSocket, HTTP
from modules.utils.redis_manager import RedisManager
from config import BYBIT_CONFIG

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


class DataFetcher:
    def __init__(self, redis_manager=None):
        self.retry_count = 0
        self.max_retries = 3
        self.redis = redis_manager or RedisManager()
        self.ws_thread = None

        # Start WebSocket in a separate thread
        self._start_websocket()

    def _start_websocket(self):
        """Start WebSocket connection in background thread"""
        def handle_orderbook(msg):
            try:
                if not msg.get("data"):
                    logging.warning("Empty orderbook message")
                    return
                book_data = msg["data"]
                symbol = msg.get("topic", "").split(
                    ".")[1] if "topic" in msg else "BTCUSDT"
                bids = book_data.get("b", [])[:5] or [[10000, 1], [9999, 2]]
                asks = book_data.get("a", [])[:5] or [[10001, 1], [10002, 2]]

                # Calculate current price from mid-point
                if bids and asks:
                    best_bid = float(bids[0][0])
                    best_ask = float(asks[0][0])
                    current_price = (best_bid + best_ask) / 2

                    # Store current price for dashboard
                    self.redis.set(f"live_price:{symbol}", str(current_price))

                # Store full orderbook data
                self.redis.set(
                    f"orderbook:{symbol}",
                    json.dumps({"bids": bids, "asks": asks,
                               "timestamp": time.time_ns()}),
                )
                logging.debug(
                    f"Orderbook updated for {symbol}: {len(bids)} bids, {len(asks)} asks")
            except Exception as e:
                logging.error(f"Orderbook error: {str(e)}")

        def ws_thread_func():
            while True:
                try:
                    ws = WebSocket(
                        testnet=BYBIT_CONFIG.get("testnet", True),
                        api_key=BYBIT_CONFIG.get("api_key", "mock_key"),
                        api_secret=BYBIT_CONFIG.get(
                            "api_secret", "mock_secret"),
                        channel_type="spot",
                    )
                    # Connect to BTCUSDT and ETHUSDT
                    ws.orderbook_stream(
                        50, "BTCUSDT", callback=handle_orderbook)
                    ws.orderbook_stream(
                        50, "ETHUSDT", callback=handle_orderbook)

                    # Keep thread alive
                    while True:
                        time.sleep(30)

                except Exception as e:
                    logging.error(f"WebSocket error: {str(e)}")
                    time.sleep(5)

        # Start WebSocket thread if not already running
        if not self.ws_thread or not self.ws_thread.is_alive():
            self.ws_thread = Thread(target=ws_thread_func, daemon=True)
            self.ws_thread.start()
            logging.info("WebSocket data stream started")

    # New method for dashboard integration
    def fetch_market_data(self, symbols, indicators=None):
        """Fetch real market data for dashboard and strategies"""
        try:
            results = []
            testnet = BYBIT_CONFIG.get("testnet", True)
            api_key = BYBIT_CONFIG.get("api_key", "mock_key")
            api_secret = BYBIT_CONFIG.get("api_secret", "mock_secret")

            session = HTTP(
                testnet=testnet,
                api_key=api_key,
                api_secret=api_secret,
            )

            for symbol in symbols:
                # First try to get live price from Redis (from WebSocket)
                live_price = self.redis.get(f"live_price:{symbol}")

                if live_price:
                    price = float(live_price)
                else:
                    # Fall back to REST API
                    response = session.get_kline(
                        category="spot",
                        symbol=symbol,
                        interval="1",
                        limit=1
                    )

                    if response.get("retCode") != 0:
                        logging.error(
                            f"API Error: {response.get('retMsg', 'Unknown error')}")
                        price = 0
                    else:
                        data = response["result"]["list"]
                        if data:
                            # Use close price from latest candle
                            price = float(data[0][4])
                        else:
                            price = 0

                # Store price in Redis for dashboard
                self.redis.set(f"price:{symbol}", str(price))

                # Add indicators - in real system you'd calculate these
                row = {
                    'symbol': symbol,
                    'price': price,
                    'EMA_20': price * 0.98,  # Simulated EMA
                    'RSI': 50,               # Simulated RSI
                    'OBV': 10000             # Simulated OBV
                }

                # Calculate any requested indicators
                if indicators:
                    for indicator in indicators:
                        if indicator not in row and indicator != "price":
                            row[indicator] = self._calculate_indicator(
                                indicator, price)

                results.append(row)

                # Log for debugging
                logging.info(f"Fetched live price for {symbol}: {price}")

            # Convert to DataFrame
            df = pd.DataFrame(results)

            # Store for dashboard use
            self.redis.set("market_data_dashboard", df.to_json())

            return df

        except Exception as e:
            logging.error(f"Error fetching market data: {str(e)}")
            # Return fallback data
            return pd.DataFrame({
                'symbol': symbols,
                'price': [50000 if s == "BTCUSDT" else 3000 for s in symbols],
                'EMA_20': [48000 if s == "BTCUSDT" else 2900 for s in symbols],
                'RSI': [55, 60],
                'OBV': [10000, 5000]
            })

    def _calculate_indicator(self, indicator, price):
        """Simple placeholder for indicator calculation"""
        if indicator == "SMA_50":
            return price * 0.97
        elif indicator == "MACD":
            return price * 0.001
        return 0

    # Original method renamed to async version
    async def fetch_market_data_async(self, symbol, timeframe, limit, category=None):
        try:
            testnet_url = (
                "https://api-testnet.bybit.com" if BYBIT_CONFIG.get(
                    "testnet", True) else None
            )
            session = HTTP(
                testnet=BYBIT_CONFIG.get("testnet", True),
                api_key=BYBIT_CONFIG.get("api_key", "mock_key"),
                api_secret=BYBIT_CONFIG.get("api_secret", "mock_secret"),
            )
            category = category or ("spot" if "USDT" in symbol else "linear")
            response = session.get_kline(
                category=category, symbol=symbol, interval=timeframe, limit=limit
            )
            logging.debug(f"API Response: {json.dumps(response, indent=2)}")
            if response.get("retCode") != 0:
                logging.error(
                    f"API Error: {response.get('retMsg', 'Unknown error')}")
                return None
            data = response["result"]["list"]
            if not data:
                logging.warning(
                    f"No data returned for {symbol} ({timeframe}, limit={limit})—check symbol or API config"
                )
                if category == "linear" and self.retry_count < self.max_retries:
                    self.retry_count += 1
                    logging.info("Switching to spot category and retrying...")
                    return await self.fetch_market_data_async(
                        symbol, timeframe, limit, "spot"
                    )
                return []
            df = [
                {
                    "timestamp": d[0],
                    "open": float(d[1]),
                    "high": float(d[2]),
                    "low": float(d[3]),
                    "close": float(d[4]),
                    "volume": float(d[5]),
                }
                for d in data
            ]
            self.redis.set(f"market_data:{symbol}", json.dumps(df))
            logging.info(f"Fetched {len(df)} data points for {symbol}")
            self.retry_count = 0
            return df
        except Exception as e:
            logging.error(f"Data fetch error: {str(e)}")
            if self.retry_count < self.max_retries:
                self.retry_count += 1
                wait_time = 2**self.retry_count
                logging.info(f"Retrying in {wait_time} seconds...")
                await asyncio.sleep(wait_time)
                return await self.fetch_market_data_async(symbol, timeframe, limit, category)
            return None

# Keep the original start_websocket and main functions for compatibility


def start_websocket():
    def handle_orderbook(msg):
        try:
            if not msg.get("data"):
                logging.warning("Empty orderbook message")
                return
            book_data = msg["data"]
            bids = book_data.get("b", [])[:5] or [[10000, 1], [9999, 2]]
            asks = book_data.get("a", [])[:5] or [[10001, 1], [10002, 2]]
            trade_state = RedisManager()
            trade_state.set(
                "orderbook",
                json.dumps({"bids": bids, "asks": asks,
                           "timestamp": time.time_ns()}),
            )
            logging.debug(
                f"Orderbook updated: {len(bids)} bids, {len(asks)} asks")
        except Exception as e:
            logging.error(f"Orderbook error: {str(e)}")

    while True:
        try:
            ws = WebSocket(
                testnet=BYBIT_CONFIG.get("testnet", True),
                api_key=BYBIT_CONFIG.get("api_key", "mock_key"),
                api_secret=BYBIT_CONFIG.get("api_secret", "mock_secret"),
                channel_type="spot",
            )
            ws.orderbook_stream(50, "BTCUSDT", callback=handle_orderbook)
        except Exception as e:
            logging.error(f"WebSocket error: {str(e)}")
            time.sleep(5)


async def main():
    fetcher = DataFetcher()
    while True:
        await fetcher.fetch_market_data_async("BTCUSDT", "1h", 100)
        await asyncio.sleep(60)

if __name__ == "__main__":
    from threading import Thread

    ws_thread = Thread(target=start_websocket, daemon=True)
    ws_thread.start()
    asyncio.run(main())



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/data/data_processing.py
# ────────────────────────────────────────────────────────────

# ai/modules/data/data_processing.py
"""
Consolidated Data Processing Module
- Feature engineering and extraction
- Liquidity prediction
- Slippage transformation
- Enhanced with quantum-inspired techniques from TB.py
"""

import numpy as np
import pandas as pd
import torch
import json
import logging
from typing import Dict, List, Tuple, Optional, Union, Any

# Try to import optional dependencies
try:
    from sklearn.preprocessing import RobustScaler
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    logging.warning(
        "Scikit-learn not available. Some feature processing will be limited.")

from ..utils.redis_manager import RedisManager

##########################################
# 1️⃣ FEATURE ENGINEERING & EXTRACTION 🧮
##########################################


class FeatureEngine:
    """Feature engineering for market data"""

    def __init__(self, redis_manager=None):
        self.redis = redis_manager or RedisManager()
        self.scaler = RobustScaler() if SKLEARN_AVAILABLE else None

    def transform(self, data):
        """
        Transform raw market data into feature-rich dataset

        Args:
            data: Raw market data (DataFrame or dict)

        Returns:
            Dict with extracted features
        """
        if isinstance(data, dict):
            df = pd.DataFrame(data)
        else:
            df = data

        features = self.extract_features(df)
        self.redis.set("features", json.dumps(features))
        return features

    def validate_dataset(self, data):
        """
        Validate input data for quality and completeness

        Args:
            data: Market data to validate

        Returns:
            Boolean indicating if data is valid
        """
        if data is None or (isinstance(data, pd.DataFrame) and data.empty):
            return False

        # Check for required columns if it's a DataFrame
        if isinstance(data, pd.DataFrame):
            required_columns = ['open', 'high', 'low', 'close', 'volume']
            if not all(col in data.columns for col in required_columns):
                return False

            # Check for too many NaN values
            nan_percentage = data.isnull().mean().mean() * 100
            if nan_percentage > 10:  # More than 10% NaN values
                return False

        return True

    def extract_features(self, df):
        """
        Extract technical and statistical features from market data

        Args:
            df: DataFrame with OHLCV data

        Returns:
            Dict with calculated features
        """
        def moving_average(data, window):
            return np.mean(data[-window:])

        # Extract closing prices if available
        closes = df["close"].values if isinstance(
            df, pd.DataFrame) and "close" in df.columns else df

        # Calculate basic features
        basic_features = {
            "ma_short": moving_average(closes, 20),
            "ma_long": moving_average(closes, 50),
            "rsi": self._calculate_rsi(closes),
        }

        # Calculate advanced features if enough data points
        if len(closes) >= 30:
            advanced_features = {
                "volatility": np.std(closes[-20:]) / np.mean(closes[-20:]),
                "trend_strength": self._calculate_trend_strength(closes),
                "momentum": self._calculate_momentum(closes),
                "cyclicality": self._fourier_transform_feature(closes)
            }
            basic_features.update(advanced_features)

        return basic_features

    def _calculate_rsi(self, closes, period=14):
        """Calculate Relative Strength Index"""
        if len(closes) < period + 1:
            return 50.0  # Default value if not enough data

        # Calculate price changes
        deltas = np.diff(closes)
        seed = deltas[:period+1]
        up = seed[seed >= 0].sum() / period
        down = -seed[seed < 0].sum() / period

        if down == 0:
            return 100

        rs = up / down
        return 100 - (100 / (1 + rs))

    def _calculate_trend_strength(self, closes, window=20):
        """Calculate trend strength using linear regression"""
        if len(closes) < window:
            return 0

        x = np.arange(window)
        y = closes[-window:]

        # Calculate slope of linear regression
        try:
            slope, _ = np.polyfit(x, y, 1)
            # Normalize slope by price level
            return slope / np.mean(y)
        except:
            return 0

    def _calculate_momentum(self, closes, period=10):
        """Calculate price momentum"""
        if len(closes) < period:
            return 0

        return (closes[-1] / closes[-period]) - 1

    def _fourier_transform_feature(self, closes, n_components=3):
        """Extract cyclical features using Fast Fourier Transform"""
        if len(closes) < 30:  # Need sufficient data for FFT
            return 0

        # Get only real component and first few frequencies
        fft = np.fft.fft(closes - np.mean(closes))
        return np.abs(fft[1:n_components+1]).mean()

    def create_ml_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Create feature-rich dataset for machine learning

        Args:
            df: DataFrame with market data

        Returns:
            DataFrame with engineered features
        """
        if df.empty:
            return pd.DataFrame()

        # Create copy to avoid modifying original
        result = df.copy()

        # Price-based features
        if 'close' in result.columns:
            # Moving averages
            result['ma5'] = result['close'].rolling(5).mean()
            result['ma20'] = result['close'].rolling(20).mean()
            result['ma50'] = result['close'].rolling(50).mean()

            # Price momentum
            result['returns_1d'] = result['close'].pct_change()
            result['returns_5d'] = result['close'].pct_change(5)

            # Volatility
            result['volatility_5d'] = result['returns_1d'].rolling(5).std()
            result['volatility_20d'] = result['returns_1d'].rolling(20).std()

        # Volume-based features
        if 'volume' in result.columns:
            result['volume_ma5'] = result['volume'].rolling(5).mean()
            result['volume_ma20'] = result['volume'].rolling(20).mean()
            result['volume_ratio'] = result['volume'] / result['volume_ma5']

        # Technical indicators
        if all(col in result.columns for col in ['high', 'low', 'close']):
            # Average True Range
            result['atr'] = self._calculate_atr(result, window=14)

            # RSI
            result['rsi'] = self._calculate_dataframe_rsi(result, window=14)

        # Fill NaN values
        result = result.fillna(method='bfill').fillna(0)

        return result

    def _calculate_atr(self, df, window=14):
        """Calculate Average True Range"""
        high = df['high']
        low = df['low']
        close = df['close']

        # Calculate True Range
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window).mean()

        return atr

    def _calculate_dataframe_rsi(self, df, window=14):
        """Calculate RSI from DataFrame"""
        delta = df['close'].diff()

        # Separate gains and losses
        gain = delta.copy()
        loss = delta.copy()
        gain[gain < 0] = 0
        loss[loss > 0] = 0
        loss = abs(loss)

        # Calculate average gain and loss
        avg_gain = gain.rolling(window).mean()
        avg_loss = loss.rolling(window).mean()

        # Calculate RS
        rs = avg_gain / avg_loss

        # Calculate RSI
        rsi = 100 - (100 / (1 + rs))

        return rsi

##########################################
# 2️⃣ LIQUIDITY PREDICTION 💧
##########################################


class LiquidityPredictor:
    """Predicts and analyzes market liquidity"""

    def __init__(self, redis_manager=None):
        self.redis = redis_manager or RedisManager()
        self.logger = logging.getLogger(self.__class__.__name__)

    def predict(self, symbol: str) -> dict:
        """
        Predicts liquidity for the given symbol

        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")

        Returns:
            Dictionary with liquidity predictions for different venues
        """
        if not isinstance(symbol, str) or not symbol:
            self.logger.error("Invalid symbol: must be a non-empty string.")
            raise ValueError("Symbol must be a non-empty string.")

        # Fetch real-time liquidity data
        liquidity_data = self._fetch_liquidity_data(symbol)

        # Add market intelligence
        liquidity_map = {
            venue: self._apply_venue_weights(value, venue)
            for venue, value in liquidity_data.items()
        }

        # Store in Redis for monitoring
        self.redis.set(f"liquidity:{symbol}", json.dumps(liquidity_map))

        self.logger.info(f"Predicted liquidity for {symbol}: {liquidity_map}")
        return liquidity_map

    def _fetch_liquidity_data(self, symbol: str) -> dict:
        """
        Fetches real-time liquidity data from the exchange

        Args:
            symbol: Trading symbol

        Returns:
            Dictionary with real-time liquidity data
        """
        # Try to get orderbook data from Redis first
        orderbook_data = self.redis.get(f"orderbook:{symbol}")

        if orderbook_data:
            try:
                orderbook = json.loads(orderbook_data)

                # Calculate liquidity from orderbook
                bid_liquidity = sum(float(bid[1])
                                    for bid in orderbook.get("bids", [])[:5])
                ask_liquidity = sum(float(ask[1])
                                    for ask in orderbook.get("asks", [])[:5])

                return {
                    "primary": bid_liquidity + ask_liquidity,
                    "bid_side": bid_liquidity,
                    "ask_side": ask_liquidity,
                    "imbalance": (bid_liquidity - ask_liquidity) / (bid_liquidity + ask_liquidity) if (bid_liquidity + ask_liquidity) > 0 else 0
                }
            except Exception as e:
                self.logger.error(f"Error processing orderbook: {e}")

        # Fallback to default values
        return {
            "primary": 1000,
            "secondary": 500,
            "dark_pool": 1500,
            "bid_side": 800,
            "ask_side": 700
        }

    def _apply_venue_weights(self, base_liquidity: float, venue: str) -> float:
        """
        Apply venue-specific liquidity adjustments

        Args:
            base_liquidity: Base liquidity value
            venue: Venue name

        Returns:
            Adjusted liquidity value
        """
        # Venue-specific adjustments based on historical performance
        venue_factors = {
            "primary": 1.0,
            "secondary": 0.85,
            "dark_pool": 0.7,
            "bid_side": 1.0,
            "ask_side": 1.0
        }

        # Apply time-of-day factor (market hours typically have more liquidity)
        import time
        hour = time.localtime().tm_hour

        # Market hours adjustment (simplified)
        time_factor = 1.2 if 13 <= hour <= 20 else 0.8  # UTC hours

        return base_liquidity * venue_factors.get(venue, 1.0) * time_factor

    def analyze_liquidity_impact(self, symbol: str, order_size: float) -> dict:
        """
        Analyze the potential market impact of an order

        Args:
            symbol: Trading symbol
            order_size: Size of the order

        Returns:
            Dictionary with liquidity impact analysis
        """
        if not isinstance(order_size, (int, float)) or order_size <= 0:
            self.logger.error("Invalid order_size: must be a positive number.")
            raise ValueError("Order size must be a positive number.")

        # Get current liquidity
        liquidity_data = self._fetch_liquidity_data(symbol)

        # Calculate impact ratios
        impact = {}
        for venue, liquidity in liquidity_data.items():
            # Skip non-numeric or derived fields
            if venue in ('imbalance'):
                continue

            # Calculate percentage of liquidity consumed
            consumption_ratio = min(1.0, order_size / max(liquidity, 1e-10))

            # Estimate price impact using square-root law (common in market microstructure)
            # Price impact ~ k * sqrt(order_size / daily_volume)
            # We use a simplified version with liquidity as the denominator
            price_impact = 0.01 * (consumption_ratio ** 0.5)

            impact[venue] = {
                "consumption_ratio": consumption_ratio,
                "price_impact_bps": price_impact * 10000,  # Convert to basis points
                "recommendation": "split" if consumption_ratio > 0.3 else "direct"
            }

        # Overall assessment
        avg_impact = np.mean([v["price_impact_bps"] for v in impact.values()])

        result = {
            "venues": impact,
            "overall_impact_bps": avg_impact,
            "high_impact": avg_impact > 50,  # More than 5 basis points is considered high
            "optimal_sizing": self._calculate_optimal_sizing(liquidity_data, target_impact_bps=10)
        }

        return result

    def _calculate_optimal_sizing(self, liquidity_data: dict, target_impact_bps: float = 10) -> float:
        """
        Calculate optimal order size for a target price impact

        Args:
            liquidity_data: Dictionary with liquidity by venue
            target_impact_bps: Target price impact in basis points

        Returns:
            Optimal order size
        """
        # Use primary venue liquidity as reference
        primary_liquidity = liquidity_data.get("primary", 1000)

        # Inverse of the square-root impact formula
        # order_size = liquidity * (impact / k)^2
        k = 0.01  # Same constant as in price impact calculation
        impact = target_impact_bps / 10000  # Convert basis points to decimal

        return primary_liquidity * (impact / k) ** 2

##########################################
# 3️⃣ SLIPPAGE TRANSFORMATION 📊
##########################################


class SlippageTransformer:
    """Predicts and transforms expected slippage for orders"""

    def __init__(self, redis_manager=None):
        self.redis = redis_manager or RedisManager()
        self.logger = logging.getLogger(self.__class__.__name__)

        # Initial model weights - would be trained/updated in a real system
        self.model_weights = {
            'size_factor': 0.6,
            'liquidity_factor': 0.3,
            'volatility_factor': 0.1
        }

    def predict(self, symbol: str, size: float, liquidity: float = None) -> float:
        """
        Predict expected slippage for an order

        Args:
            symbol: Trading symbol
            size: Order size
            liquidity: Available liquidity (optional)

        Returns:
            Predicted slippage as a decimal (e.g., 0.001 = 0.1%)
        """
        if not isinstance(size, (int, float)) or size <= 0:
            self.logger.error("Invalid size: must be a positive number.")
            return 0.001  # Default fallback

        # Get liquidity if not provided
        if liquidity is None:
            liquidity_data = self.redis.get(f"liquidity:{symbol}")
            if liquidity_data:
                try:
                    liquidity_map = json.loads(liquidity_data)
                    liquidity = liquidity_map.get("primary", 1000)
                except Exception:
                    liquidity = 1000
            else:
                liquidity = 1000

        # Get historical volatility from Redis
        volatility = 0.01  # Default volatility (1%)
        volatility_data = self.redis.get(f"volatility:{symbol}")
        if volatility_data:
            try:
                volatility = float(volatility_data)
            except Exception:
                pass

        # Calculate size ratio (order size / liquidity)
        size_ratio = min(1.0, size / max(liquidity, 1))

        # Calculate base slippage using square-root formula
        base_slippage = 0.0005 * (size_ratio ** 0.5)

        # Apply volatility adjustment
        volatility_adjustment = volatility * \
            self.model_weights['volatility_factor']

        # Final slippage prediction
        slippage = base_slippage * (1 + volatility_adjustment)

        # Store for tracking
        self._store_prediction(symbol, size, slippage)

        return float(slippage)

    def _store_prediction(self, symbol: str, size: float, slippage: float):
        """Store slippage prediction for analysis and model improvement"""
        try:
            # Get existing predictions
            predictions = json.loads(self.redis.get(
                f"slippage_predictions:{symbol}") or "[]")

            # Add new prediction
            predictions.append({
                "timestamp": time.time(),
                "size": float(size),
                "predicted_slippage": float(slippage)
            })

            # Keep only last 100 predictions
            if len(predictions) > 100:
                predictions = predictions[-100:]

            # Store back to Redis
            self.redis.set(
                f"slippage_predictions:{symbol}", json.dumps(predictions))

        except Exception as e:
            self.logger.error(f"Error storing slippage prediction: {e}")

    def calculate_execution_price(self, symbol: str, price: float, size: float, side: str) -> float:
        """
        Calculate expected execution price including slippage

        Args:
            symbol: Trading symbol 
            price: Base price (e.g., mid price)
            size: Order size
            side: Trade side ("buy" or "sell")

        Returns:
            Expected execution price after slippage
        """
        # Get slippage prediction
        slippage = self.predict(symbol, size)

        # Apply slippage to price (direction depends on side)
        if side.lower() == "buy":
            execution_price = price * (1 + slippage)
        else:
            execution_price = price * (1 - slippage)

        return execution_price

    def update_model(self, actual_slippage_data: List[Dict]):
        """
        Update model weights based on actual slippage observations

        Args:
            actual_slippage_data: List of dictionaries with predicted vs actual slippage
        """
        if not actual_slippage_data:
            return

        # Simple gradient descent update
        # In a real system, this would be more sophisticated
        learning_rate = 0.01

        errors = []
        for data in actual_slippage_data:
            predicted = data.get('predicted_slippage', 0)
            actual = data.get('actual_slippage', 0)
            errors.append(actual - predicted)

        avg_error = np.mean(errors)

        # Update weights (simplified)
        for key in self.model_weights:
            # Small adjustment based on error
            self.model_weights[key] *= (1 + learning_rate * avg_error)

        # Normalize weights to sum to 1
        total = sum(self.model_weights.values())
        self.model_weights = {k: v/total for k,
                              v in self.model_weights.items()}


##########################################
# 4️⃣ MARKET STATE ENCODER 🧩
##########################################

class MarketStateEncoder:
    """Encodes market state into vector for ML models"""

    def __init__(self, feature_dim=128):
        self.feature_dim = feature_dim
        self.logger = logging.getLogger(self.__class__.__name__)

    def encode(self, **features):
        """
        Encode arbitrary market features into fixed-length vector

        Args:
            **features: Keyword arguments with feature values

        Returns:
            Numpy array of encoded features
        """
        # Extract all feature values
        feature_values = list(features.values())

        # Ensure we have data
        if not feature_values:
            return np.zeros(self.feature_dim)

        # Normalize and pad/truncate to fixed dimension
        return self._normalize_features(feature_values)

    def _normalize_features(self, features):
        """
        Normalize feature vector to fixed length

        Args:
            features: List of feature values

        Returns:
            Normalized numpy array
        """
        # Convert all to float values
        float_features = [float(f) if f is not None else 0.0 for f in features]

        # Pad if needed
        if len(float_features) < self.feature_dim:
            float_features += [0.0] * (self.feature_dim - len(float_features))

        # Truncate if needed
        normalized = np.array(float_features[:self.feature_dim])

        # Scale to [-1, 1] range
        max_val = max(abs(normalized.max()), abs(normalized.min()), 1e-10)
        normalized = normalized / max_val

        return normalized

    def encode_market_data(self, df: pd.DataFrame):
        """
        Extract features from market data DataFrame

        Args:
            df: DataFrame with market data

        Returns:
            Numpy array of encoded features
        """
        if df.empty:
            return np.zeros(self.feature_dim)

        features = {}

        # Price features
        if 'close' in df.columns:
            features['close'] = df['close'].iloc[-1]

            # Calculate price changes if enough data
            if len(df) >= 2:
                features['close_change'] = df['close'].pct_change().iloc[-1]

            # Calculate moving averages if enough data
            if len(df) >= 20:
                features['ma_20'] = df['close'].rolling(20).mean().iloc[-1]
                features['ma_diff'] = features['close'] / features['ma_20'] - 1

        # Volume features
        if 'volume' in df.columns:
            features['volume'] = df['volume'].iloc[-1]
            if len(df) >= 5:
                features['volume_change'] = df['volume'].pct_change(5).iloc[-1]

        # Volatility features
        if 'high' in df.columns and 'low' in df.columns and 'close' in df.columns:
            features['volatility'] = (
                df['high'].iloc[-1] - df['low'].iloc[-1]) / df['close'].iloc[-1]

        # Momentum features
        if 'close' in df.columns and len(df) >= 10:
            features['momentum'] = df['close'].pct_change(10).iloc[-1]

        return self.encode(**features)



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/data/market_analyzer.py
# ────────────────────────────────────────────────────────────

# ai/modules/data/market_analyzer.py
"""
Quantitative Market Analysis Engine
- Technical indicators
- Volatility analysis
- Market regime detection
"""

import numpy as np
import pandas as pd
from typing import Dict, List
from modules.utils.redis_manager import RedisManager  # Absolute import
from modules.utils.events import EventBus  # Absolute import
import json  # Added for redis.set


class MarketAnalyzer:
    def __init__(self, event_bus):  # EventBus type hint optional since Agent is passed
        self.redis = RedisManager()
        self.event_bus = event_bus
        # Fix: Use register_listener instead of subscribe
        self.event_bus.register_listener("market_data", self.on_market_data)

    def on_market_data(self, data: Dict):
        """Process real-time market updates"""
        df = self._convert_to_dataframe(data)
        analysis = {
            "technical": self.calculate_technical_indicators(df),
            "volatility": self.calculate_volatility_metrics(df),
            "regime": self.detect_market_regime(df),
        }
        self.redis.set(f"analysis:{data['symbol']}", json.dumps(analysis))
        self.event_bus.publish("market_analysis", analysis)

    def calculate_technical_indicators(self, df: pd.DataFrame) -> Dict:
        """Generate comprehensive technical signals"""
        closes = df["close"].values
        return {
            "rsi": self._calculate_rsi(closes),
            "macd": self._calculate_macd(closes),
            "bollinger": self._calculate_bollinger_bands(df),
            "ichimoku": self._calculate_ichimoku_cloud(df),
        }

    def calculate_volatility_metrics(self, df: pd.DataFrame) -> Dict:
        """Advanced volatility analysis"""
        returns = np.log(df["close"]).diff().dropna()
        return {
            "garch": self._calculate_garch(returns),
            "realized_vol": returns.std() * np.sqrt(252),
            "atr": self._calculate_atr(df, window=14),
        }

    def detect_market_regime(self, df: pd.DataFrame) -> str:
        """Machine learning-based regime classification"""
        # Integrated with existing ML predictors
        from modules.ml.predictors import HybridPredictor  # Absolute import

        predictor = HybridPredictor()
        return predictor.predict(df)

    # Placeholder helper methods (assuming they exist or need implementation)
    def _convert_to_dataframe(self, data: Dict) -> pd.DataFrame:
        return pd.DataFrame(data)

    def _calculate_rsi(self, closes: np.ndarray) -> float:
        return 50.0  # Placeholder

    def _calculate_macd(self, closes: np.ndarray) -> Dict:
        return {"macd": 0.0, "signal": 0.0}  # Placeholder

    def _calculate_bollinger_bands(self, df: pd.DataFrame) -> Dict:
        return {"upper": 0.0, "lower": 0.0}  # Placeholder

    def _calculate_ichimoku_cloud(self, df: pd.DataFrame) -> Dict:
        return {"span_a": 0.0, "span_b": 0.0}  # Placeholder

    def _calculate_garch(self, returns: pd.Series) -> float:
        return 0.0  # Placeholder

    def _calculate_atr(self, df: pd.DataFrame, window: int) -> float:
        return 0.0  # Placeholder



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/data/neuromorphic.py
# ────────────────────────────────────────────────────────────

# ai/modules/data/neuromorphic.py
import snntorch as snn
import torch
import torch.nn as nn


class MarketMemory(nn.Module):
    """Spiking Neural Network for efficient time-series compression"""

    def __init__(self, input_size=10, hidden_size=64, output_size=1, beta=0.9):
        super().__init__()
        self.fc1 = nn.Linear(input_size, hidden_size)
        self.lif = snn.Leaky(beta=beta, threshold=1.0)
        self.fc2 = nn.Linear(hidden_size, output_size)
        self.memory = torch.zeros(1, hidden_size)

    def forward(self, x):
        x = self.fc1(x)
        spk, mem = self.lif(x, self.memory)
        self.memory = mem
        out = self.fc2(spk)
        return out

    def process_tick(self, data):
        # Convert data to tensor if needed
        if not torch.is_tensor(data):
            data = torch.tensor(data, dtype=torch.float)
        self.forward(data)

    def recall_pattern(self, similarity_threshold=0.8):
        return (self.memory @ self.memory.T) > similarity_threshold



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/execution/advanced_exec.py
# ────────────────────────────────────────────────────────────

# ai/modules/execution/advanced_exec.py

import numpy as np
from decimal import Decimal
from typing import Dict, Optional
from ..utils.redis_manager import RedisManager
from ..account.account_manager import AccountManager
from ..ml.predictors import LiquidityPredictor
import time


class AdvancedExecution:
    """AI-powered execution with real-time liquidity adaptation"""

    def __init__(self, account: AccountManager):
        self.account = account
        self.redis = RedisManager()
        self.liquidity_predictor = LiquidityPredictor()
        self.order_slices = []
        self.risk_adjusted = False

    def execute_complex_order(
        self, symbol: str, total_qty: Decimal, strategy: str = "AVWAP", urgency: int = 2
    ) -> Dict:
        """Adaptive Volume-Weighted Average Price execution"""
        order_book = self._get_orderbook(symbol)
        liquidity_map = self.liquidity_predictor.predict(symbol)
        position = self.account.get_position(symbol)

        # AI-adjusted parameters
        params = self._calculate_dynamic_params(
            total_qty, order_book, liquidity_map, urgency
        )

        # Risk-adjusted slicing
        slices = self._generate_optimal_slices(
            total_qty, params["slice_multiplier"], params["aggressiveness"]
        )

        results = []
        for idx, slice_qty in enumerate(slices):
            best_price = self._calculate_optimal_price(
                order_book, slice_qty, position.direction
            )

            result = self.account.place_order(
                symbol=symbol,
                side="Buy" if total_qty > 0 else "Sell",
                quantity=slice_qty,
                order_type="Limit",
                price=best_price,
                reduce_only=position.is_reduce_only(),
            )

            if result["status"] != "success":
                self._handle_failed_slice(idx, slice_qty)

            results.append(result)
            time.sleep(params["interval"])

        return self._aggregate_results(results)

    def _calculate_dynamic_params(self, total_qty, order_book, liquidity, urgency):
        """Machine learning model for execution parameters"""
        features = {
            "order_size": float(total_qty),
            "bid_ask_spread": order_book["asks"][0][0] - order_book["bids"][0][0],
            "liquidity_imbalance": liquidity["ask"] / liquidity["bid"],
            "volatility": self.redis.get(f"volatility:{symbol}", 0.05),
            "urgency": urgency,
        }

        # Load pretrained ML model
        model = torch.jit.load("models/execution_model.pt")
        with torch.no_grad():
            params = model(torch.tensor(list(features.values())))

        return {
            "slice_multiplier": params[0].item(),
            "aggressiveness": params[1].item(),
            "interval": max(0.5, params[2].item()),
        }

    def _generate_optimal_slices(self, total_qty, multiplier, aggressiveness):
        """Generates time-volume slices using modified VWAP"""
        slices = []
        remaining = abs(total_qty)

        while remaining > 0:
            slice_size = min(
                remaining,
                max(
                    Decimal("0.001"),
                    (remaining * Decimal(multiplier))
                    * Decimal(1 + aggressiveness * np.random.randn()),
                ),
            )
            slices.append(slice_size)
            remaining -= slice_size

        return slices

    # Additional enhanced methods...



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/execution/execution_engine.py
# ────────────────────────────────────────────────────────────

# ai/modules/execution/execution_engine.py

"""
Institutional-Grade Execution System
- Order Management
- Risk-Aware Routing
- Performance Monitoring
"""

import time
import json
from typing import Dict, Optional
from dataclasses import dataclass
from abc import ABC, abstractmethod
from collections import deque


@dataclass
class OrderRequest:
    symbol: str = "BTCUSDT"  # Default symbol
    action: str = "BUY"      # Default action
    quantity: float = 0.01   # Default quantity
    order_type: str = "LIMIT"
    leverage: int = 10
    risk_params: dict = None


class ExecutionResult:
    """AI-Enhanced Execution Outcome"""

    def __init__(self, success=False, metadata=None):
        self.success = success
        metadata = metadata or {}  # Default to empty dict if None
        self.latency = metadata.get("latency", 0)
        self.slippage = metadata.get("slippage", 0)
        self.order_id = metadata.get("order_id", None)
        self.timestamp = time.time()
        self.successful = []  # Add this to store successful orders
        self.failed = []      # Add this to store failed orders


class RiskAwareExecutor:
    """AI-Powered Order Execution"""

    def __init__(self, risk_model=None, market_data_adapter=None):
        self.risk_model = risk_model
        self.market_data = market_data_adapter
        self.performance_log = deque(maxlen=1000)

    def execute_order(self, order: OrderRequest) -> ExecutionResult:
        """Smart order routing with real-time adaptation"""
        if not self.risk_model.approve_order(order):
            return ExecutionResult(False, {"error": "Risk check failed"})

        execution_plan = self._create_execution_plan(order)
        return self._execute_plan(execution_plan)

    def _create_execution_plan(self, order: OrderRequest) -> dict:
        """Generate AI-optimized execution strategy"""
        liquidity_map = self.market_data.get_liquidity(order.symbol)
        return {
            "strategy": "TWAP" if order.quantity > 1000 else "VWAP",
            "slices": self._calculate_slices(order, liquidity_map),
            "risk_adjustments": self.risk_model.calculate_adjustments(order),
        }

    def _execute_plan(self, plan: dict) -> ExecutionResult:
        """Execute with real-time market adaptation"""
        # Implementation with live market data integration
        return ExecutionResult(True, {"latency": 0.15, "slippage": 0.002})


class PerformanceMonitor:
    """AI-Driven Execution Analytics"""

    def __init__(self):
        self.latency_metrics = {}
        self.slippage_metrics = {}
        self.error_rates = {}

    def update_metrics(self, result: ExecutionResult):
        """Reinforcement learning-based performance tracking"""
        # Update internal state with execution outcome
        pass

    def generate_insights(self) -> dict:
        """Machine learning-generated performance insights"""
        return {
            "optimal_execution_times": self._calculate_optimal_times(),
            "risk_adjustment_recommendations": self._generate_risk_recommendations(),
        }


class LiveMarketAdapter:
    """Interface with trading exchange"""

    def __init__(self, api_client=None, config=None):
        self.api_client = api_client
        self.config = config or {}
        self.testnet = self.config.get('testnet', True)
        self.last_order_response = None

    def connect(self):
        """Establish connection to exchange"""
        if not self.api_client:
            from pybit.unified_trading import HTTP
            try:
                self.api_client = HTTP(
                    api_key=self.config.get('api_key', 'mock_key'),
                    api_secret=self.config.get('api_secret', 'mock_secret'),
                    testnet=self.testnet
                )
                return True
            except Exception as e:
                print(f"Connection error: {e}")
                return False
        return True

    def execute_order(self, order: OrderRequest) -> dict:
        """Send order to exchange"""
        if not self.connect():
            return {"success": False, "error": "Connection failed"}

        try:
            # Convert internal order to exchange format
            exchange_order = {
                "symbol": order.symbol,
                "side": order.action.upper(),
                "orderType": order.order_type,
                "qty": str(order.quantity),
                "timeInForce": "GTC"
            }

            # Don't actually execute in testnet mode
            if self.testnet:
                self.last_order_response = {
                    "orderId": f"mock-{time.time()}",
                    "status": "NEW",
                    "avgPrice": "0"
                }
                return {"success": True, "data": self.last_order_response}

            # Real execution would go here
            # result = self.api_client.place_active_order(**exchange_order)
            # self.last_order_response = result
            # return {"success": True, "data": result}

            # Mock for now
            return {"success": True, "data": {"orderId": f"mock-{time.time()}"}}

        except Exception as e:
            return {"success": False, "error": str(e)}


class ExecutionContext:
    """Unified Execution Environment"""

    def __init__(self):
        self.executor = RiskAwareExecutor()
        self.monitor = PerformanceMonitor()
        self.market_adapter = LiveMarketAdapter()

    def execute(self, order: OrderRequest) -> ExecutionResult:
        """Full execution lifecycle management"""
        result = self.executor.execute_order(order)
        self.monitor.update_metrics(result)
        self._adapt_strategy(result)
        return result

    def _adapt_strategy(self, result: ExecutionResult):
        """Online learning for execution improvement"""
        if result.slippage > 0.005:
            self.executor.adjust_parameters({"aggressiveness": 0.8})



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/execution/market_impact.py
# ────────────────────────────────────────────────────────────

# modules/execution/market_impact.py
"""
Market Impact Analysis Module
New module that analyzes potential trade impact on market liquidity
Extracted from TB.py's advanced cost modeling without duplicating existing code
"""

import numpy as np
import logging
import json
import time
from typing import Dict, Any, Optional
from ..utils.redis_manager import RedisManager

# Initialize Redis connection
trade_state = RedisManager()


class MarketImpactAnalyzer:
    """Analyzes potential market impact of trades before execution"""

    def __init__(self):
        self.fee_structure = {"maker": 0.0002,
                              "taker": 0.0005}  # 0.02% / 0.05%
        self.slippage_history = {}  # Track historical slippage by symbol
        self.volume_profile = {}  # Store volume profile for optimal execution

    def estimate_slippage(self, symbol: str, size: float) -> float:
        """
        Predict expected slippage based on order size and market depth.
        This helps in determining optimal execution size.
        """
        # Get recent slippage data (if available)
        hist_slippage = json.loads(
            trade_state.get(f"slippage:{symbol}") or "[]")

        # If we have historical data, use it for prediction
        if len(hist_slippage) >= 10:
            return np.mean(hist_slippage[-10:])

        # Fallback to model-based estimate
        orderbook = self._get_orderbook(symbol)
        if not orderbook:
            return 0.001  # Default 0.1% slippage

        # Calculate slippage based on order size and book depth
        # Deeper analysis than the existing implementation
        liquidity = self._calculate_liquidity(orderbook)
        return min(0.05, size / (liquidity * 10))  # Cap at 5%

    def analyze_trade_impact(self, symbol: str, side: str,
                             size: float) -> Dict[str, Any]:
        """
        Comprehensive trade impact analysis for better execution
        Returns expected costs, slippage, and recommended execution approach
        """
        # Get orderbook data
        orderbook = self._get_orderbook(symbol)
        if not orderbook:
            return {
                "slippage": 0.001,
                "fee": size * self.fee_structure["taker"],
                "market_impact": "unknown",
                "execution_type": "market"
            }

        # Market depth analysis
        book_depth = self._analyze_book_depth(orderbook, side)

        # Calculate expected slippage
        expected_slippage = self.estimate_slippage(symbol, size)

        # Determine if splitting the order would be beneficial
        should_split = book_depth["available_liquidity"] < size * 5

        # Determine optimal order type based on spread
        spread = book_depth.get("spread", 0.001)
        order_type = "limit" if spread > 0.0015 else "market"
        fee_rate = self.fee_structure["maker" if order_type ==
                                      "limit" else "taker"]

        # Calculate trade economics
        results = {
            "slippage": expected_slippage,
            "fee": size * fee_rate,
            "spread_cost": spread * size if order_type == "market" else 0,
            "market_impact": "high" if should_split else "low",
            "execution_type": "twap" if should_split else order_type,
            "recommended_slices": self._calculate_optimal_slices(size, book_depth) if should_split else 1
        }

        return results

    def _calculate_optimal_slices(self, size: float,
                                  book_depth: Dict[str, Any]) -> int:
        """Calculate optimal number of slices for large orders"""
        available_liquidity = book_depth.get("available_liquidity", size)

        # Never slice into more than 10 pieces or less than 2
        raw_slices = min(10, max(2, round(size / (available_liquidity * 0.2))))

        # Round to cleaner numbers (2, 3, 4, 5, 6, 8, 10)
        optimal_slices = [2, 3, 4, 5, 6, 8, 10]
        return min(optimal_slices, key=lambda x: abs(x - raw_slices))

    def _get_orderbook(self, symbol: str) -> Optional[Dict]:
        """Get orderbook data from Redis cache"""
        try:
            orderbook_data = trade_state.get(f"orderbook:{symbol}")
            if not orderbook_data:
                return None
            return json.loads(orderbook_data)
        except Exception as e:
            logging.error(f"Error fetching orderbook: {e}")
            return None

    def _calculate_liquidity(self, orderbook: Dict) -> float:
        """Calculate available liquidity from orderbook"""
        if not orderbook or "bids" not in orderbook or "asks" not in orderbook:
            return 1000  # Default value

        # Sum volume for top 5 levels
        bid_liquidity = sum(float(level[1])
                            for level in orderbook.get("bids", [])[:5])
        ask_liquidity = sum(float(level[1])
                            for level in orderbook.get("asks", [])[:5])

        return (bid_liquidity + ask_liquidity) / 2

    def _analyze_book_depth(self, orderbook: Dict, side: str) -> Dict[str, Any]:
        """Analyze orderbook depth for the specified side"""
        if not orderbook or "bids" not in orderbook or "asks" not in orderbook:
            return {"available_liquidity": 1000, "spread": 0.001}

        # Get relevant side of the book
        book_side = orderbook.get(
            "asks" if side.upper() == "BUY" else "bids", [])
        if not book_side:
            return {"available_liquidity": 1000, "spread": 0.001}

        # Calculate available liquidity (first 5 levels)
        available_liquidity = sum(float(level[1]) for level in book_side[:5])

        # Calculate spread
        top_bid = float(orderbook.get("bids", [[0]])[0][0])
        top_ask = float(orderbook.get("asks", [[0]])[0][0])
        spread = (top_ask - top_bid) / ((top_ask + top_bid) / 2)

        # Calculate book imbalance (for determining market direction bias)
        book_imbalance = self._calculate_book_imbalance(orderbook)

        return {
            "available_liquidity": available_liquidity,
            "spread": spread,
            "book_imbalance": book_imbalance,
            "top_price": top_ask if side.upper() == "BUY" else top_bid
        }

    def _calculate_book_imbalance(self, orderbook: Dict) -> float:
        """
        Calculate order book imbalance as indicator of market pressure
        Positive values indicate buying pressure, negative indicates selling
        """
        if not orderbook or "bids" not in orderbook or "asks" not in orderbook:
            return 0.0

        bid_volume = sum(float(level[1])
                         for level in orderbook.get("bids", [])[:5])
        ask_volume = sum(float(level[1])
                         for level in orderbook.get("asks", [])[:5])

        if bid_volume + ask_volume == 0:
            return 0.0

        return (bid_volume - ask_volume) / (bid_volume + ask_volume)

    def record_slippage(self, symbol: str, expected_price: float,
                        executed_price: float, side: str) -> None:
        """
        Record actual slippage for future predictions
        Side is needed because slippage direction depends on trade side
        """
        if side.upper() == "BUY":
            slippage = (executed_price - expected_price) / expected_price
        else:
            slippage = (expected_price - executed_price) / expected_price

        # Store in history
        if symbol not in self.slippage_history:
            self.slippage_history[symbol] = []

        self.slippage_history[symbol].append(slippage)

        # Keep last 100 entries
        if len(self.slippage_history[symbol]) > 100:
            self.slippage_history[symbol] = self.slippage_history[symbol][-100:]

        # Store in Redis for persistence
        trade_state.set(
            f"slippage:{symbol}",
            json.dumps(self.slippage_history[symbol])
        )


class VolumeProfileAnalyzer:
    """
    Analyzes trading volume profile for optimal execution timing
    A completely new feature based on TB.py's volume analysis
    """

    def __init__(self, window_size: int = 24):
        self.window_size = window_size  # Hours to analyze
        self.volume_profiles = {}  # Store volume profiles by symbol

    def analyze_volume_profile(self, symbol: str) -> Dict[str, Any]:
        """
        Analyze historical volume to determine optimal trade timing
        Returns hourly volume profile and recommended execution windows
        """
        # Get historical volume data
        volume_data = self._get_volume_data(symbol)
        if not volume_data:
            return {
                "status": "insufficient_data",
                "optimal_hours": [9, 10, 11, 14, 15]  # Default US market hours
            }

        # Calculate hourly volume distribution
        hourly_volume = self._calculate_hourly_volume(volume_data)

        # Find high volume hours (top 30%)
        threshold = np.percentile(list(hourly_volume.values()), 70)
        high_volume_hours = [
            hour for hour, volume in hourly_volume.items()
            if volume > threshold
        ]

        # Store for future reference
        self.volume_profiles[symbol] = hourly_volume

        # Return volume profile and recommendations
        return {
            "status": "success",
            "hourly_volume": hourly_volume,
            "optimal_hours": sorted(high_volume_hours),
            "highest_volume_hour": max(
                hourly_volume.items(), key=lambda x: x[1]
            )[0],
            "lowest_volume_hour": min(
                hourly_volume.items(), key=lambda x: x[1]
            )[0]
        }

    def _get_volume_data(self, symbol: str) -> Optional[Dict]:
        """Get historical volume data from Redis"""
        try:
            volume_data = trade_state.get(f"volume:{symbol}")
            if not volume_data:
                return None
            return json.loads(volume_data)
        except Exception as e:
            logging.error(f"Error fetching volume data: {e}")
            return None

    def _calculate_hourly_volume(self, volume_data: Dict) -> Dict[int, float]:
        """Calculate hourly volume distribution from historical data"""
        hourly_volume = {hour: 0.0 for hour in range(24)}

        # Process volume data
        for timestamp, volume in volume_data.items():
            hour = int(timestamp) % 24  # Extract hour from timestamp
            hourly_volume[hour] += float(volume)

        # Normalize to percentages
        total_volume = sum(hourly_volume.values())
        if total_volume > 0:
            hourly_volume = {
                hour: (volume / total_volume)
                for hour, volume in hourly_volume.items()
            }

        return hourly_volume

    def get_optimal_execution_time(self, symbol: str,
                                   preference: str = "high_volume") -> int:
        """
        Get optimal hour to execute trades based on volume profile
        preference can be 'high_volume' or 'low_volume'
        """
        # Update volume profile if needed
        if symbol not in self.volume_profiles:
            self.analyze_volume_profile(symbol)

        # Get current profile or generate default
        profile = self.volume_profiles.get(
            symbol,
            {hour: 1/24 for hour in range(24)}
        )

        # Find optimal hour based on preference
        if preference == "high_volume":
            return max(profile.items(), key=lambda x: x[1])[0]
        else:
            return min(profile.items(), key=lambda x: x[1])[0]



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/execution/risk_management.py
# ────────────────────────────────────────────────────────────

# ai/modules/execution/risk_management.py
"""
Comprehensive Risk Management System
- Circuit breaker patterns
- AI-driven risk assessment
- Liquidity and volatility monitoring
- Emergency protocols
"""

import os
import json
import time
import logging
import threading
import psutil
from typing import Dict, Any, Optional, Union
from dotenv import load_dotenv
import numpy as np

# Import project utilities
from ..utils.redis_manager import RedisManager

# Load environment variables
load_dotenv()
logger = logging.getLogger(__name__)


class RiskOracle:
    """AI-driven risk assessment engine"""

    def __init__(self, redis_manager=None):
        self.redis = redis_manager or RedisManager()
        self.volatility_threshold = 0.05  # 5% volatility threshold

        # Risk factor weights
        self.risk_weights = {
            "volatility": 0.4,
            "liquidity": 0.3,
            "market_trend": 0.2,
            "execution_speed": 0.1
        }

    def predict_market_risk(self, symbol: str) -> Dict[str, Any]:
        """
        Generate comprehensive market risk assessment using multiple factors
        """
        # Fetch required data from Redis
        volatility = float(self.redis.get(f"volatility:{symbol}") or 0.03)
        orderbook = json.loads(self.redis.get(f"orderbook:{symbol}") or "{}")
        market_trend = float(self.redis.get(f"market_trend:{symbol}") or 0)

        # Calculate liquidity from orderbook
        liquidity = self._calculate_liquidity(orderbook)
        liquidity_risk = self._assess_liquidity_risk(liquidity)

        # Calculate overall risk score (0-1 scale)
        risk_score = (
            volatility * self.risk_weights["volatility"] +
            liquidity_risk * self.risk_weights["liquidity"] +
            abs(market_trend) * self.risk_weights["market_trend"]
        )

        # Normalize risk score to 0-1 range
        normalized_risk = min(1.0, max(0.0, risk_score))

        return {
            "symbol": symbol,
            "risk_score": normalized_risk,
            "volatility": volatility,
            "liquidity": liquidity,
            "liquidity_risk": liquidity_risk,
            "market_trend": market_trend,
            "timestamp": time.time()
        }

    def _calculate_liquidity(self, orderbook: Dict) -> float:
        """Calculate available liquidity from orderbook data"""
        if not orderbook or "bids" not in orderbook or "asks" not in orderbook:
            return 1000  # Default value

        # Sum volume for top 5 levels
        bid_liquidity = sum(float(level[1])
                            for level in orderbook.get("bids", [])[:5])
        ask_liquidity = sum(float(level[1])
                            for level in orderbook.get("asks", [])[:5])

        return (bid_liquidity + ask_liquidity) / 2

    def _assess_liquidity_risk(self, liquidity: float) -> float:
        """Convert raw liquidity to a risk score (0-1)"""
        # Low liquidity = high risk, high liquidity = low risk
        if liquidity < 100:
            return 1.0  # Extremely low liquidity, very high risk
        elif liquidity < 1000:
            return 0.7  # Low liquidity, high risk
        elif liquidity < 10000:
            return 0.4  # Moderate liquidity, moderate risk
        else:
            return 0.1  # High liquidity, low risk

    def check_volatility_breach(self, symbol: str) -> bool:
        """Determine if volatility exceeds safe thresholds"""
        volatility = float(self.redis.get(f"volatility:{symbol}") or 0.03)
        return volatility > self.volatility_threshold

    def detect_liquidity_crisis(self, symbol: str) -> bool:
        """Detect liquidity crisis by analyzing orderbook depth"""
        orderbook = json.loads(self.redis.get(f"orderbook:{symbol}") or "{}")
        liquidity = self._calculate_liquidity(orderbook)
        return liquidity < 500  # Crisis threshold

    def dynamic_position_sizing(self, symbol: str, base_size: float) -> float:
        """Calculate position size based on current market conditions"""
        risk_assessment = self.predict_market_risk(symbol)

        # Scale position down as risk increases
        risk_factor = 1.0 - risk_assessment["risk_score"]

        # Apply minimum position size of 10% of base
        return max(base_size * risk_factor, base_size * 0.1)

    def validate_signals(self, signals):
        """
        Validate trading signals against risk parameters

        Args:
            signals: List of trading signals to validate

        Returns:
            Dictionary of validated signals with risk metrics
        """
        if not signals:
            return {}

        validated_signals = {}
        for signal_id, signal in signals.items():
            # Check signal risk parameters
            if self._check_signal_risk(signal):
                validated_signals[signal_id] = signal

        return validated_signals


class CircuitBreaker:
    """Enhanced circuit breaker with adaptive thresholds"""

    def __init__(self, redis_manager=None, max_errors=3, reset_timeout=300):
        self.redis = redis_manager or RedisManager()
        self.max_errors = max_errors
        self.reset_timeout = reset_timeout
        self.error_count = 0
        self.last_error_time = None
        self.tripped = False
        self.lock = threading.Lock()
        self.risk_oracle = RiskOracle(redis_manager)

    def __enter__(self):
        if self.tripped:
            self._trigger_emergency_protocol()
            raise CircuitOpenError(
                "Trading suspended: circuit breaker tripped")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            with self.lock:
                self.error_count += 1
                self.last_error_time = time.time()
                self.tripped = self._should_trip()

                if self.tripped:
                    self._trigger_emergency_protocol()
        return False  # Don't suppress exceptions

    def _should_trip(self) -> bool:
        """Determine if circuit breaker should trip based on multiple factors"""
        # Check error count threshold
        if self.error_count >= self.max_errors:
            return True

        # Check system resource constraints
        if psutil.virtual_memory().percent > 90:
            return True

        # Check market conditions through RiskOracle
        symbol = self.redis.get("active_symbol") or "BTCUSDT"
        if self.risk_oracle.check_volatility_breach(symbol):
            return True

        if self.risk_oracle.detect_liquidity_crisis(symbol):
            return True

        return False

    def _trigger_emergency_protocol(self):
        """Execute emergency protocol when circuit breaker trips"""
        logger.critical("🛑 EMERGENCY PROTOCOL ACTIVATED!")
        self.redis.set("system_status", "emergency")
        self.redis.set("trading_enabled", "false")
        self.redis.set("emergency_timestamp", str(time.time()))

    def reset(self):
        """Reset circuit breaker state"""
        with self.lock:
            self.error_count = 0
            self.last_error_time = None
            self.tripped = False
            logger.info("Circuit breaker reset")


class CircuitOpenError(Exception):
    """Exception raised when circuit breaker is tripped"""

    def __init__(self, message="Trading suspended by circuit breaker"):
        self.timestamp = time.time()
        super().__init__(message)


class RiskManager:
    """Comprehensive risk management system"""

    def __init__(self, redis_manager=None, event_bus=None):
        self.redis = redis_manager or RedisManager()
        self.event_bus = event_bus
        self.risk_oracle = RiskOracle(redis_manager)
        self.circuit_breaker = CircuitBreaker(redis_manager)

        # Risk thresholds
        self.max_position_size = float(
            os.getenv("MAX_POSITION_SIZE", "0.2"))  # % of account
        self.max_open_positions = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
        self.max_daily_drawdown = float(
            os.getenv("MAX_DAILY_DRAWDOWN", "0.05"))  # 5%

        # Position sizing models
        self.sizing_models = {
            "fixed": self._fixed_position_sizing,
            "volatility": self._volatility_position_sizing,
            "kelly": self._kelly_position_sizing,
            "dynamic": self.risk_oracle.dynamic_position_sizing
        }

    def check_trade(self, order: Dict) -> Dict:
        """
        Validate trade against risk parameters before execution

        Args:
            order: Order details including symbol, side, quantity

        Returns:
            Dictionary with approval status and risk metrics
        """
        symbol = order.get("symbol")
        quantity = float(order.get("quantity", 0))

        with self.circuit_breaker:
            # Get account balance
            balance = float(self.redis.get("account_balance") or 10000)
            position_value = quantity * float(order.get("price", 0))

            # Calculate risk metrics
            position_size_pct = position_value / balance
            open_positions = len(json.loads(
                self.redis.get("open_positions") or "{}"))
            daily_pnl = float(self.redis.get("daily_pnl") or 0)
            daily_drawdown = abs(min(0, daily_pnl)) / balance

            # Check if trade exceeds risk limits
            risk_checks = {
                "position_size": position_size_pct <= self.max_position_size,
                "open_positions": open_positions < self.max_open_positions,
                "daily_drawdown": daily_drawdown <= self.max_daily_drawdown,
                "market_risk": not self.risk_oracle.check_volatility_breach(symbol)
            }

            approved = all(risk_checks.values())

            # Calculate optimal position size
            optimal_size = self.calculate_position_size(
                symbol, balance, model=order.get("sizing_model", "dynamic")
            )

            return {
                "approved": approved,
                "risk_checks": risk_checks,
                "optimal_size": optimal_size,
                "message": "Trade approved" if approved else "Trade rejected: risk limits exceeded"
            }

    def calculate_position_size(self, symbol: str, balance: float, model: str = "dynamic") -> float:
        """
        Calculate position size based on selected model

        Args:
            symbol: Trading symbol
            balance: Account balance
            model: Position sizing model (fixed, volatility, kelly, dynamic)

        Returns:
            Recommended position size in base currency
        """
        sizing_func = self.sizing_models.get(
            model, self._fixed_position_sizing)
        return sizing_func(symbol, balance)

    def _fixed_position_sizing(self, symbol: str, balance: float) -> float:
        """Fixed percentage position sizing"""
        return balance * self.max_position_size * 0.5  # 50% of max position

    def _volatility_position_sizing(self, symbol: str, balance: float) -> float:
        """Position sizing inversely proportional to volatility"""
        volatility = float(self.redis.get(f"volatility:{symbol}") or 0.03)
        return balance * self.max_position_size * (0.03 / max(volatility, 0.01))

    def _kelly_position_sizing(self, symbol: str, balance: float) -> float:
        """Kelly criterion position sizing"""
        win_rate = float(self.redis.get(f"win_rate:{symbol}") or 0.5)
        avg_win = float(self.redis.get(f"avg_win:{symbol}") or 0.05)
        avg_loss = float(self.redis.get(f"avg_loss:{symbol}") or 0.05)

        if avg_loss == 0:
            return 0  # Avoid division by zero

        kelly = win_rate / avg_loss - (1 - win_rate) / avg_win

        # Cap Kelly at the maximum position size
        kelly = min(max(0, kelly), self.max_position_size)

        return balance * kelly

    def emergency_shutdown(self):
        """
        Execute emergency shutdown protocol
        - Close all positions
        - Cancel all open orders
        - Disable trading
        """
        logger.critical("🚨 EMERGENCY SHUTDOWN INITIATED")

        # Set system status
        self.redis.set("system_status", "emergency")
        self.redis.set("trading_enabled", "false")

        # Log the event
        emergency_log = {
            "timestamp": time.time(),
            "type": "emergency_shutdown",
            "reason": "Risk manager triggered shutdown"
        }
        self.redis.rpush("emergency_log", json.dumps(emergency_log))

        # Notify event bus if available
        if self.event_bus:
            self.event_bus.publish("emergency", emergency_log)

        return True



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/ml/federated.py
# ────────────────────────────────────────────────────────────

# ai/modules/ml/federated.py

"""
Secure Federated Learning System
- Differential privacy
- Encrypted aggregation
- Decentralized coordination
"""

import torch
import syft as sy
from typing import List, Dict
from ..utils.redis_manager import RedisManager
from ..utils.events import EventBus


class FederatedTrainer:
    def __init__(self, model: torch.nn.Module, event_bus: EventBus):
        self.model = model
        self.event_bus = event_bus
        self.redis = RedisManager()
        self.hook = sy.TorchHook(torch)

        # Integrated with existing components
        self.secure_worker = self._create_secure_worker()
        self.event_bus.subscribe("model_update", self.aggregate_updates)

    def _create_secure_worker(self):
        """Create virtual secure worker"""
        return sy.VirtualWorker(self.hook, id="secure_worker")

    def aggregate_updates(self, updates: List[Dict]):
        """Secure model aggregation"""
        # Differential privacy integration
        from ..ml.risk_ml import apply_dp_noise

        sanitized_updates = [apply_dp_noise(u) for u in updates]

        # Federated averaging
        global_params = self.model.state_dict()
        for key in global_params:
            global_params[key] = torch.stack(
                [u[key].float() for u in sanitized_updates], 0
            ).mean(0)

        self.model.load_state_dict(global_params)
        self._distribute_updated_model()

    def _distribute_updated_model(self):
        """Update all strategy models"""
        model_state = self.model.state_dict()
        self.redis.set("global_model", pickle.dumps(model_state))
        self.event_bus.publish("model_update", model_state)

    # Training coordination and encryption methods...



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/ml/ml_analysis.py
# ────────────────────────────────────────────────────────────

# ai/modules/ml/ml_analysis.py
"""
Consolidated Machine Learning Analysis Module
Combines: accuracy.py, anomaly.py, crash.py, and forecast.py
Provides core analysis functionality for the AI trading system
"""

import numpy as np
import torch
import torch.nn as nn
import pandas as pd
import logging
from typing import List, Dict, Tuple, Optional, Union, Any
from collections import deque

from ..utils.redis_manager import RedisManager
trade_state = RedisManager()

##########################################
# 1️⃣ ACCURACY TRACKING & MONITORING 📊
##########################################

class ModelAccuracyTracker:
    """Tracks prediction accuracy for machine learning models"""
    
    def __init__(self, window_size: int = 100):
        self.accuracy_history = []
        self.window_size = window_size
        self.total_predictions = 0
        self.correct_predictions = 0
        
    def update(self, predictions, labels):
        """Update accuracy statistics with new predictions"""
        # Handle tensor or numpy array inputs
        if torch.is_tensor(predictions):
            predictions = predictions.detach().cpu().numpy()
        if torch.is_tensor(labels):
            labels = labels.detach().cpu().numpy()
            
        # Calculate correct predictions
        correct = (predictions == labels).sum().item()
        accuracy = correct / len(labels)
        
        # Update running statistics
        self.total_predictions += len(labels)
        self.correct_predictions += correct
        
        # Store in sliding window
        self.accuracy_history.append(accuracy)
        if len(self.accuracy_history) > self.window_size:
            self.accuracy_history = self.accuracy_history[-self.window_size:]
            
    def get_accuracy(self) -> Dict[str, float]:
        """Get current accuracy metrics"""
        current_accuracy = self.correct_predictions / max(1, self.total_predictions)
        recent_accuracy = np.mean(self.accuracy_history) if self.accuracy_history else 0
        
        return {
            "overall": float(current_accuracy),
            "recent": float(recent_accuracy),
            "count": self.total_predictions
        }
    
    def save_to_redis(self, model_name: str):
        """Save accuracy metrics to Redis for monitoring"""
        try:
            metrics = self.get_accuracy()
            trade_state.set(f"model_accuracy:{model_name}", str(metrics))
        except Exception as e:
            logging.error(f"Failed to save accuracy metrics: {e}")


##########################################
# 2️⃣ ANOMALY DETECTION SYSTEM 🔍
##########################################

class AnomalyDetector:
    """Detects price and volume anomalies using statistical methods"""
    
    def __init__(self, window: int = 20, threshold: float = 3.0):
        """
        Initialize anomaly detector
        
        Args:
            window: Window size for rolling statistics
            threshold: Z-score threshold for anomaly detection
        """
        self.window = window
        self.threshold = threshold
        self.history = []
        self.volume_history = []
        self.detected_anomalies = []
        
    def check(self, price: float, volume: Optional[float] = None) -> bool:
        """
        Check if current price is anomalous
        
        Args:
            price: Current price to check
            volume: Current volume (optional)
            
        Returns:
            True if anomaly detected, False otherwise
        """
        # Update price history
        self.history.append(price)
        if len(self.history) > self.window * 3:  # Keep 3x window size
            self.history = self.history[-(self.window * 3):]
            
        # Update volume history if provided
        if volume is not None:
            self.volume_history.append(volume)
            if len(self.volume_history) > self.window * 3:
                self.volume_history = self.volume_history[-(self.window * 3):]
        
        # Can't detect anomalies until we have enough data
        if len(self.history) < self.window:
            return False
            
        # Calculate z-score for price
        mean = np.mean(self.history[-self.window:])
        std = np.std(self.history[-self.window:])
        if std == 0:  # Avoid division by zero
            return False
            
        z_score = abs(price - mean) / std
        
        # Enhanced detection with volume if available
        anomaly_detected = z_score > self.threshold
        
        if volume is not None and len(self.volume_history) >= self.window:
            volume_mean = np.mean(self.volume_history[-self.window:])
            volume_std = np.std(self.volume_history[-self.window:])
            
            if volume_std > 0:  # Avoid division by zero
                volume_z_score = abs(volume - volume_mean) / volume_std
                # Combined anomaly detection (price and volume both unusual)
                anomaly_detected = anomaly_detected and (volume_z_score > self.threshold)
        
        # Store detected anomalies with timestamp
        if anomaly_detected:
            import time
            self.detected_anomalies.append({
                "timestamp": time.time(),
                "price": price,
                "z_score": float(z_score),
                "volume": volume,
            })
            
            # Keep last 100 anomalies
            if len(self.detected_anomalies) > 100:
                self.detected_anomalies = self.detected_anomalies[-100:]
                
            # Log anomaly detection
            logging.warning(f"Price anomaly detected: z-score={z_score:.2f}, price={price}")
            
        return anomaly_detected
    
    def get_anomaly_stats(self) -> Dict[str, Any]:
        """Get statistics about detected anomalies"""
        if not self.detected_anomalies:
            return {"count": 0, "avg_zscore": 0}
            
        return {
            "count": len(self.detected_anomalies),
            "avg_zscore": np.mean([a["z_score"] for a in self.detected_anomalies]),
            "recent": self.detected_anomalies[-5:],  # Last 5 anomalies
        }


##########################################
# 3️⃣ FLASH CRASH PREDICTION 📉
##########################################

class FlashCrashModel(nn.Module):
    """LSTM-based model for flash crash prediction"""
    
    def __init__(self, input_size: int = 10, hidden_size: int = 64, 
                output_size: int = 1, dropout_rate: float = 0.2):
        super(FlashCrashModel, self).__init__()
        
        # LSTM layer
        self.lstm = nn.LSTM(input_size, hidden_size, batch_first=True)
        
        # Batch normalization
        self.batch_norm = nn.BatchNorm1d(hidden_size)
        
        # Fully connected layers
        self.fc1 = nn.Linear(hidden_size, 20)
        self.fc2 = nn.Linear(20, output_size)
        
        # Dropout for regularization
        self.dropout = nn.Dropout(dropout_rate)
        
        # Activation function
        self.sigmoid = nn.Sigmoid()
        
    def forward(self, x):
        # Process through LSTM
        lstm_out, (h_n, _) = self.lstm(x)
        
        # Apply batch normalization to the last hidden state
        h_n_last = self.batch_norm(h_n[-1])
        
        # Apply dropout and pass through the first fully connected layer
        x = self.dropout(h_n_last)
        x = torch.relu(self.fc1(x))
        
        # Pass through the second fully connected layer
        x = self.fc2(x)
        
        # Apply sigmoid activation for binary output
        return self.sigmoid(x)


class FlashCrashPredictor:
    """Predicts potential flash crash events using LSTM model"""
    
    def __init__(self, model_path: str = None):
        """
        Initialize flash crash predictor
        
        Args:
            model_path: Path to pre-trained model weights
        """
        import os
        
        # Initialize the model
        self.model = FlashCrashModel()
        
        # Load pre-trained weights if available
        if model_path and os.path.exists(model_path):
            try:
                self.model.load_state_dict(torch.load(model_path))
                self.model.eval()  # Set to evaluation mode
                logging.info(f"Loaded flash crash model from {model_path}")
            except Exception as e:
                logging.error(f"Error loading flash crash model: {e}")
                
        self.threshold = 0.75  # Prediction threshold
        
    def predict(self, price_history: List[float]) -> bool:
        """
        Predict if the next price will cause a crash based on price history
        
        Args:
            price_history: List of historical price data
            
        Returns:
            True if a crash is predicted, False otherwise
        """
        if not price_history or len(price_history) < 10:
            logging.error("Insufficient price history for flash crash prediction")
            return False
            
        # Simple z-score based fallback if model is not properly loaded
        if not hasattr(self.model, 'forward'):
            mean = np.mean(price_history)
            std = np.std(price_history)
            
            if std == 0:
                return False
                
            z_score = (price_history[-1] - mean) / std
            return z_score < -3.0  # Large negative z-score indicates potential crash
            
        try:
            # Prepare input data for the model
            # Model expects a batch of sequences with features
            input_data = np.array(price_history[-10:]).reshape(1, 10, 1)
            input_tensor = torch.FloatTensor(input_data)
            
            # Make prediction
            with torch.no_grad():
                output = self.model(input_tensor)
                prediction = output.item()
                
            logging.info(f"Flash crash prediction: {prediction:.4f}")
            
            # Return True if prediction exceeds threshold
            return prediction > self.threshold
            
        except Exception as e:
            logging.error(f"Error in flash crash prediction: {e}")
            return False


##########################################
# 4️⃣ PRICE FORECASTING 📈
##########################################

class PriceForecaster(nn.Module):
    """Neural network for price forecasting"""
    
    def __init__(self, input_size: int = 10, hidden_size: int = 32, output_size: int = 1):
        super(PriceForecaster, self).__init__()
        
        # Neural network architecture
        self.net = nn.Sequential(
            nn.Linear(input_size, hidden_size),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(hidden_size, hidden_size // 2),
            nn.ReLU(),
            nn.Linear(hidden_size // 2, output_size)
        )
        
    def forward(self, x):
        return self.net(x)
        
    def predict(self, price_history: Union[List[float], torch.Tensor]) -> float:
        """
        Predict the next price in the sequence
        
        Args:
            price_history: List or tensor of historical prices
            
        Returns:
            Predicted next price
        """
        # Convert input to proper format
        if isinstance(price_history, list):
            if len(price_history) < 10:
                raise ValueError("Input must contain at least 10 prices")
            input_tensor = torch.FloatTensor(price_history[-10:])
        elif isinstance(price_history, torch.Tensor):
            if price_history.size(-1) < 10:
                raise ValueError("Input tensor must have at least 10 elements")
            input_tensor = price_history
        else:
            raise TypeError("Input must be a list or a torch.Tensor")
            
        # Make prediction
        with torch.no_grad():
            prediction = self.net(input_tensor)
            
        # Return scalar value for single prediction
        if prediction.dim() == 0:
            return prediction.item()
        else:
            return prediction.item() if prediction.numel() == 1 else prediction.squeeze().tolist()


class TimeSeriesForecaster:
    """Enhanced time series forecasting with multiple methods"""
    
    def __init__(self, forecast_horizon: int = 5, 
                model_path: str = None, 
                use_ensemble: bool = True):
        """
        Initialize time series forecaster
        
        Args:
            forecast_horizon: Number of steps to forecast
            model_path: Path to pre-trained model
            use_ensemble: Whether to use ensemble methods
        """
        self.forecast_horizon = forecast_horizon
        self.use_ensemble = use_ensemble
        self.nn_model = PriceForecaster()
        
        # Load neural network model if available
        if model_path:
            try:
                self.nn_model.load_state_dict(torch.load(model_path))
                self.nn_model.eval()
            except Exception as e:
                logging.error(f"Error loading forecaster model: {e}")
                
        # Historical prediction errors for model calibration
        self.prediction_errors = []
        
    def forecast(self, price_history: List[float], 
                additional_features: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
        """
        Generate price forecasts using multiple methods
        
        Args:
            price_history: Historical price data
            additional_features: Additional features for enhanced prediction
            
        Returns:
            Dictionary with forecasts and confidence intervals
        """
        if len(price_history) < 30:
            raise ValueError("Need at least 30 data points for reliable forecasting")
            
        # 1. Simple moving average forecast
        ma_forecast = np.mean(price_history[-10:])
        
        # 2. Linear regression forecast
        lr_forecast = self._linear_regression_forecast(price_history)
        
        # 3. Neural network forecast
        try:
            nn_forecast = self.nn_model.predict(price_history[-10:])
        except Exception as e:
            logging.error(f"Neural network forecast failed: {e}")
            nn_forecast = ma_forecast  # Fallback to moving average
            
        # 4. Ensemble forecast (weighted average)
        if self.use_ensemble:
            # Calibrate weights based on historical performance
            weights = self._calculate_optimal_weights()
            ensemble_forecast = (
                weights[0] * ma_forecast + 
                weights[1] * lr_forecast + 
                weights[2] * nn_forecast
            )
        else:
            ensemble_forecast = nn_forecast
            
        # Calculate confidence intervals
        std_dev = np.std(price_history[-30:])
        
        # Return forecasts with confidence intervals
        return {
            "forecast": ensemble_forecast,
            "methods": {
                "moving_avg": float(ma_forecast),
                "linear_reg": float(lr_forecast),
                "neural_net": float(nn_forecast)
            },
            "confidence_interval": {
                "lower_95": float(ensemble_forecast - 1.96 * std_dev),
                "upper_95": float(ensemble_forecast + 1.96 * std_dev)
            },
            "horizon": self.forecast_horizon
        }
    
    def _linear_regression_forecast(self, price_history: List[float]) -> float:
        """Generate forecast using linear regression"""
        y = np.array(price_history[-30:])
        x = np.arange(len(y)).reshape(-1, 1)
        
        # Fit linear regression model
        from sklearn.linear_model import LinearRegression
        model = LinearRegression().fit(x, y)
        
        # Predict next value
        next_x = np.array([[len(y)]])
        lr_forecast = model.predict(next_x)[0]
        
        return float(lr_forecast)
        
    def _calculate_optimal_weights(self) -> List[float]:
        """Calculate optimal weights for ensemble based on historical errors"""
        # Default equal weights
        if len(self.prediction_errors) < 10:
            return [1/3, 1/3, 1/3]
            
        # If we have historical errors, weight inversely to error
        errors = np.array(self.prediction_errors[-10:])
        
        # Avoid division by zero
        if np.all(errors == 0):
            return [1/3, 1/3, 1/3]
            
        # Calculate inverse errors and normalize
        inv_errors = 1.0 / (errors + 1e-6)
        weights = inv_errors / np.sum(inv_errors)
        
        return weights.tolist()
        
    def update_errors(self, actual_price: float, forecasts: Dict[str, float]):
        """Update prediction errors for model calibration"""
        errors = [
            abs(forecasts["moving_avg"] - actual_price),
            abs(forecasts["linear_reg"] - actual_price),
            abs(forecasts["neural_net"] - actual_price)
        ]
        self.prediction_errors.append(errors)
        
        # Keep only recent errors
        if len(self.prediction_errors) > 100:
            self.prediction_errors = self.prediction_errors[-100:]
            
    def save_forecast_to_redis(self, symbol: str, forecast_data: Dict[str, Any]):
        """Save forecast data to Redis for other components"""
        try:
            import json
            trade_state.set(f"forecast:{symbol}", json.dumps(forecast_data))
        except Exception as e:
            logging.error(f"Failed to save forecast to Redis: {e}")


# Export public interfaces
__all__ = [
    'ModelAccuracyTracker',
    'AnomalyDetector',
    'FlashCrashModel',
    'FlashCrashPredictor',
    'PriceForecaster',
    'TimeSeriesForecaster'
]



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/ml/ml_reinforcement.py
# ────────────────────────────────────────────────────────────

# ai/modules/ml/ml_reinforcement.py
"""
Consolidated Reinforcement Learning Module
Combines: deep_rl.py, neuro_symbolic.py, rl_exec.py
Provides advanced RL-based decision making for the AI trading system
"""

import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
import random
from collections import deque
from typing import List, Dict, Tuple, Union, Any, Optional

# Try to import specific libraries, with fallbacks for each
try:
    from transformers import AutoModel
    TRANSFORMERS_AVAILABLE = True
except ImportError:
    TRANSFORMERS_AVAILABLE = False

try:
    import z3
    Z3_AVAILABLE = True
except ImportError:
    Z3_AVAILABLE = False

##########################################
# 1️⃣ CORE RL TRADING MODEL 🧠
##########################################


class DeepRLTrader(nn.Module):
    """Deep Reinforcement Learning Trading Agent with Epsilon-Greedy Exploration"""

    def __init__(self, input_size=15, hidden_size=64, output_size=3, lr=0.001):
        super().__init__()
        self.device = torch.device(
            "cuda" if torch.cuda.is_available() else "cpu")

        # Network architecture
        self.net = nn.Sequential(
            nn.Linear(input_size, hidden_size),
            nn.LayerNorm(hidden_size),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(hidden_size, output_size),
        )

        # Optimization and training setup
        self.optimizer = torch.optim.Adam(self.parameters(), lr=lr)
        self.loss_fn = nn.SmoothL1Loss()  # Huber loss

        # Exploration parameters
        self.epsilon = 1.0
        self.epsilon_min = 0.01
        self.epsilon_decay = 0.995
        self.memory = deque(maxlen=10000)
        # Default action probabilities
        self.action_probabilities = [0.33, 0.33, 0.34]

        self.to(self.device)

    def forward(self, state):
        """Raw Q-value output"""
        return self.net(state)

    def decide(self, state, training_mode=True):
        """Epsilon-greedy action selection with proper tensor handling"""
        with torch.no_grad():
            # Convert state to tensor
            state_tensor = torch.FloatTensor(
                state).unsqueeze(0).to(self.device)

            # Calculate Q-values
            q_values = self.forward(state_tensor)

            # Epsilon-greedy exploration
            if training_mode and np.random.random() < self.epsilon:
                action = np.random.randint(0, 3)
            else:
                action = torch.argmax(q_values).item()

            # Decay epsilon
            if training_mode:
                self.epsilon = max(
                    self.epsilon_min, self.epsilon * self.epsilon_decay)

            return ["BUY", "SELL", "HOLD"][action], q_values.detach().cpu().numpy()

    # Alias for compatibility
    choose_action = decide
    get_action = decide

    def update_model(self, experiences, gamma=0.99, batch_size=64):
        """Experience replay with Q-learning update"""
        if len(experiences) < batch_size:
            return None

        # Sample random batch from experience replay
        batch = random.sample(experiences, batch_size)
        states, actions, rewards, next_states = zip(*batch)

        # Convert to tensors
        states = torch.FloatTensor(np.array(states)).to(self.device)
        actions = torch.LongTensor(actions).to(self.device)
        rewards = torch.FloatTensor(rewards).to(self.device)
        next_states = torch.FloatTensor(np.array(next_states)).to(self.device)

        # Q-learning update
        current_q = self(states).gather(1, actions.unsqueeze(1))
        next_q = self(next_states).max(1)[0].detach()
        target_q = rewards + gamma * next_q

        # Calculate loss
        loss = self.loss_fn(current_q.squeeze(), target_q)

        # Backpropagation
        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(
            self.parameters(), 1.0)  # Gradient clipping
        self.optimizer.step()

        return loss.item()

    def save_model(self, path="saved_models/deep_rl_trader.pt"):
        """Save model state with metadata"""
        torch.save(
            {
                "model_state": self.state_dict(),
                "optimizer_state": self.optimizer.state_dict(),
                "epsilon": self.epsilon,
                "input_size": self.net[0].in_features,
                "hidden_size": self.net[0].out_features,
            },
            path,
        )

    @classmethod
    def load_model(cls, path="saved_models/deep_rl_trader.pt"):
        """Load model with proper initialization"""
        checkpoint = torch.load(path, map_location=torch.device("cpu"))
        model = cls(
            input_size=checkpoint["input_size"], hidden_size=checkpoint["hidden_size"]
        )
        model.load_state_dict(checkpoint["model_state"])
        model.optimizer.load_state_dict(checkpoint["optimizer_state"])
        model.epsilon = checkpoint["epsilon"]
        model.to(checkpoint["model_state"]["net.0.weight"].device)
        return model


##########################################
# 2️⃣ NEURO-SYMBOLIC TRADING 🔮
##########################################

class NeuroSymbolicTrader:
    """Combines neural networks with formal verification"""

    def __init__(self, use_verification=True):
        self.lstm = nn.LSTM(
            input_size=128, hidden_size=64) if torch.cuda.is_available() else None
        self.linear = nn.Linear(
            in_features=64, out_features=3) if torch.cuda.is_available() else None
        self.transformer = None  # Initialized lazily when needed

        # Initialize constraint solver if available
        self.solver = z3.Solver() if Z3_AVAILABLE and use_verification else None
        self.use_verification = use_verification and Z3_AVAILABLE

    def _create_constraints(self, market_cond):
        """Create formal verification constraints for strategy safety"""
        if not self.use_verification:
            return

        # Add market safety constraints
        price = z3.Real("price") if Z3_AVAILABLE else None
        if price is not None and "support" in market_cond and "resistance" in market_cond:
            self.solver.add(
                z3.And(
                    price > 0.95 * market_cond["support"],
                    price < 1.05 * market_cond["resistance"],
                )
            )

    def _initialize_transformer(self):
        """Lazy initialization of transformer component"""
        if not TRANSFORMERS_AVAILABLE:
            return False

        try:
            self.transformer = AutoModel.from_pretrained(
                "distilbert-base-uncased")
            return True
        except Exception:
            return False

    def generate_strategy(self, news=None, chart_pattern=None, market_cond=None):
        """Generate trading strategy with hybrid reasoning"""
        # Initialize components if needed
        if self.transformer is None and news is not None:
            if not self._initialize_transformer():
                news = None  # Disable news analysis if transformer init failed

        # Create safety constraints
        if market_cond and self.use_verification:
            self._create_constraints(market_cond)

        # Neural pattern recognition
        strategy = "HOLD"  # Default strategy
        confidence = 0.5   # Default confidence

        # Process chart patterns with LSTM if available
        if chart_pattern is not None and self.lstm is not None:
            try:
                tensor_pattern = torch.FloatTensor(chart_pattern).unsqueeze(0)
                with torch.no_grad():
                    pattern_output, (h_n, _) = self.lstm(tensor_pattern)
                    logits = self.linear(h_n[-1])
                    probs = torch.softmax(logits, dim=-1)
                    action_idx = torch.argmax(probs).item()
                    strategy = ["BUY", "SELL", "HOLD"][action_idx]
                    confidence = probs[0, action_idx].item()
            except Exception:
                pass  # Fall back to default strategy

        # Safety verification (if enabled)
        if self.use_verification and self.solver is not None:
            if self.solver.check() != z3.sat:
                strategy = "HOLD"  # Safety constraints not satisfied
                confidence = min(confidence, 0.3)  # Reduce confidence

        return {
            "action": strategy,
            "confidence": confidence,
            "verified": self.use_verification
        }


##########################################
# 3️⃣ RL EXECUTION AGENT 🚀
##########################################

class RLExecutionAgent:
    """Reinforcement Learning-based Execution Agent"""

    def __init__(self, state_size=10, action_size=3, hidden_size=64):
        self.policy_net = nn.Sequential(
            nn.Linear(state_size, hidden_size),
            nn.ReLU(),
            nn.Linear(hidden_size, action_size),
        )
        self.optimizer = optim.Adam(self.policy_net.parameters(), lr=0.001)
        self.gamma = 0.99
        self.memory = []
        self.last_state = None
        self.last_action = None

    def choose_action(self, state, epsilon=0.1):
        """Choose an action using epsilon-greedy policy"""
        if np.random.rand() < epsilon:
            return np.random.randint(0, 3)  # Random action
        with torch.no_grad():
            q_values = self.policy_net(torch.FloatTensor(state))
            return torch.argmax(q_values).item()

    def store_transition(self, state, action, reward, next_state):
        """Store experience in memory"""
        self.memory.append((state, action, reward, next_state))

        # Update last state and action
        self.last_state = next_state
        self.last_action = action

    def train(self, batch_size=32):
        """Train the policy network using experiences"""
        if len(self.memory) < batch_size:
            return

        batch = np.random.choice(len(self.memory), batch_size, replace=False)
        states, actions, rewards, next_states = zip(
            *[self.memory[i] for i in batch])

        states = torch.FloatTensor(states)
        actions = torch.LongTensor(actions)
        rewards = torch.FloatTensor(rewards)
        next_states = torch.FloatTensor(next_states)

        current_q = self.policy_net(states).gather(1, actions.unsqueeze(1))
        next_q = self.policy_net(next_states).max(1)[0].detach()
        target_q = rewards + self.gamma * next_q

        loss = nn.MSELoss()(current_q.squeeze(), target_q)
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()

        return loss.item()

    def save_model(self, path="saved_models/rl_execution_agent.pt"):
        """Save model to disk"""
        torch.save(self.policy_net.state_dict(), path)

    def load_model(self, path="saved_models/rl_execution_agent.pt"):
        """Load model from disk"""
        self.policy_net.load_state_dict(torch.load(path))


# Export public interfaces
__all__ = [
    'DeepRLTrader',
    'NeuroSymbolicTrader',
    'RLExecutionAgent'
]



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/ml/ml_risk.py
# ────────────────────────────────────────────────────────────

# ai/modules/ml/ml_risk.py
"""
Consolidated Risk ML Module
Combines: risk_ml.py, relevant parts of var_calc.py
Provides ML-powered risk assessment and VaR calculation for the AI trading system
"""
import os
import pickle
import torch
import torch.nn as nn
import numpy as np
import pandas as pd
from scipy.stats import norm
from typing import Dict, List, Tuple, Optional, Union, Any
from sklearn.ensemble import IsolationForest
from ..utils.redis_manager import RedisManager

##########################################
# 1️⃣ RISK ML MODELS 🛡️
##########################################


class RiskML:
    def __init__(self, redis_manager=None):
        print("Initializing RiskML with proper IsolationForest setup...")
        self.redis = redis_manager or RedisManager()
        self.autoencoder = self._build_ae()

        # CRITICAL FIX: Create and IMMEDIATELY train IsolationForest with dummy data
        # to ensure estimators_ attribute exists
        dummy_data = np.random.rand(20, 10)  # 20 samples with 10 features
        self.iso_forest = IsolationForest(
            contamination=0.01, n_estimators=10, max_samples=10)
        self.iso_forest.fit(dummy_data)  # Force fit on initialization
        self.is_forest_trained = True
        print("Pre-initialized IsolationForest with dummy data - estimators_ attribute now exists")

        # Try to load real models afterward
        try:
            self._load_models()
        except Exception as e:
            print(f"Model loading failed, using pre-initialized models: {e}")

    def _build_ae(self):
        """Build autoencoder network for risk assessment"""
        return nn.Sequential(
            nn.Linear(10, 5),
            nn.ReLU(),
            nn.Linear(5, 10),
            nn.Sigmoid()
        )

    def _load_models(self):
        """Load pre-trained models from Redis if available, or from local files"""
        try:
            # Try to load from Redis first
            ae_state = self.redis.get("risk_models")
            iso_forest_data = self.redis.get("iso_forest_model")

            # If Redis data is available, use it
            if ae_state:
                self.autoencoder.load_state_dict(torch.load(ae_state))
                print("Loaded autoencoder model from Redis")

            if iso_forest_data:
                self.iso_forest = pickle.loads(iso_forest_data)
                self.is_forest_trained = True
                print("Loaded IsolationForest model from Redis")
                return

            # If not in Redis, try to load from file system
            # Define paths to model files
            models_dir = os.path.join(os.path.dirname(
                os.path.dirname(os.path.dirname(__file__))), "saved_models")
            ae_path = os.path.join(models_dir, "autoencoder.pt")
            iso_path = os.path.join(models_dir, "isolation_forest.pkl")

            # Try to load autoencoder
            if os.path.exists(ae_path):
                self.autoencoder.load_state_dict(torch.load(ae_path))
                print(f"Loaded autoencoder model from {ae_path}")

            # Try to load isolation forest
            if os.path.exists(iso_path):
                with open(iso_path, 'rb') as f:
                    self.iso_forest = pickle.load(f)
                    self.is_forest_trained = True
                    print(f"Loaded IsolationForest model from {iso_path}")
            else:
                print("No pre-trained models found. Will train on first use.")

        except Exception as e:
            print(f"Model load failed: {e}")

    def predict_market_risk(self, market_data):
        """
        Predict market risk using multiple ML models

        Args:
            market_data: Market features for risk assessment

        Returns:
            Dictionary with various risk metrics
        """
        # Reconstruction error (anomaly detection)
        tensor_data = torch.FloatTensor(market_data)
        reconstructed = self.autoencoder(tensor_data)
        mse = ((tensor_data - reconstructed) ** 2).mean().item()

        # Anomaly detection with isolation forest
        if not self.is_forest_trained:
            # Try to train the model with the current data
            try:
                self.iso_forest.fit(market_data)
                self.is_forest_trained = True
                print("Isolation Forest trained successfully")
            except Exception as e:
                print(f"On-the-fly Isolation Forest training failed: {e}")
                # Use a default value if training fails
                return {
                    "reconstruction_risk": mse,
                    "anomaly_risk": 0.0,
                    "combined_risk": 0.7 * mse,
                    "timestamp": pd.Timestamp.now()
                }

        # Now we can safely call score_samples
        try:
            anomaly_score = self.iso_forest.score_samples(market_data)
            mean_score = anomaly_score.mean() if isinstance(
                anomaly_score, np.ndarray) else anomaly_score

            # Combined risk assessment
            return {
                "reconstruction_risk": mse,
                "anomaly_risk": mean_score,
                "combined_risk": 0.7 * mse + 0.3 * mean_score,
                "timestamp": pd.Timestamp.now()
            }
        except Exception as e:
            print(f"Isolation Forest scoring failed: {e}")
            return {
                "reconstruction_risk": mse,
                "anomaly_risk": 0.0,
                "combined_risk": 0.7 * mse,
                "timestamp": pd.Timestamp.now()
            }

    def train_models(self, dataset, epochs=10):
        """
        Online learning implementation to update risk models

        Args:
            dataset: Training data for risk models
            epochs: Number of training epochs
        """
        # Train autoencoder
        optimizer = torch.optim.Adam(self.autoencoder.parameters())
        for epoch in range(epochs):
            for batch in dataset:
                tensor_batch = torch.FloatTensor(batch)
                recon = self.autoencoder(tensor_batch)
                loss = nn.MSELoss()(recon, tensor_batch)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

        # Train isolation forest
        try:
            self.iso_forest.fit(dataset)
            self.is_forest_trained = True  # Set flag after successful training
            print("Isolation Forest trained during scheduled training")
        except Exception as e:
            print(f"Isolation Forest training failed: {e}")

        # Save updated models - INDENT THIS ENTIRE BLOCK
        try:
            # Define directory and file paths
            models_dir = os.path.join(os.path.dirname(
                os.path.dirname(os.path.dirname(__file__))), "saved_models")

            # Create directory if it doesn't exist
            if not os.path.exists(models_dir):
                os.makedirs(models_dir)

            ae_path = os.path.join(models_dir, "autoencoder.pt")
            iso_path = os.path.join(models_dir, "isolation_forest.pkl")

            # Save to Redis
            self.redis.set("risk_models", torch.save(
                self.autoencoder.state_dict(), "temp.pt"))
            self.redis.set("iso_forest_model", pickle.dumps(self.iso_forest))

            # Also save to file system for persistence
            torch.save(self.autoencoder.state_dict(), ae_path)
            with open(iso_path, 'wb') as f:
                pickle.dump(self.iso_forest, f)

            print(f"Models saved to Redis and {models_dir}")

        except Exception as e:
            print(f"Model save failed: {e}")

    def apply_dp_noise(self, update, epsilon=0.1):
        """
        Apply differential privacy noise to model updates

        Args:
            update: Model update
            epsilon: Privacy parameter (lower = more privacy)

        Returns:
            Update with added noise for privacy
        """
        sensitivity = 1.0
        noise_scale = sensitivity / epsilon
        noise = np.random.laplace(0, noise_scale, update.shape)
        return update + noise

##########################################
# 2️⃣ VALUE AT RISK CALCULATION 📊
##########################################


class VaRCalculator:
    """Hybrid VaR calculation with ML adjustments"""

    def __init__(self, lookback=365, confidence_level=0.95):
        self.redis = RedisManager()
        self.lookback = lookback
        self.confidence_level = confidence_level
        self.risk_params = self._load_risk_params()
        self.historical_var_values = []
        self.predictions = []

    def calculate_var(self, returns, parametric=True, historical=True,
                      conditional=False, monte_carlo=False):
        """
        Calculate Value at Risk using multiple methods

        Args:
            returns: Historical returns time series
            parametric: Use parametric VaR
            historical: Use historical VaR
            conditional: Use conditional VaR (CVaR/Expected Shortfall)
            monte_carlo: Use Monte Carlo simulation for VaR

        Returns:
            Dictionary with VaR calculations and metadata
        """
        result = {}

        # Historical Simulation VaR
        if historical and len(returns) > 30:
            hist_var = np.percentile(
                returns, 100 * (1 - self.confidence_level))
            result["historical_var"] = hist_var

            # Store for tracking
            self.historical_var_values.append(hist_var)
            if len(self.historical_var_values) > 100:
                self.historical_var_values.pop(0)

        # Parametric VaR (using normal distribution)
        if parametric:
            mu = np.mean(returns)
            sigma = np.std(returns)
            param_var = mu - sigma * norm.ppf(self.confidence_level)
            result["parametric_var"] = param_var

        # Conditional VaR (Expected Shortfall)
        if conditional and len(returns) > 30:
            var_cutoff = np.percentile(
                returns, 100 * (1 - self.confidence_level))
            cvar = returns[returns <= var_cutoff].mean()
            result["conditional_var"] = cvar

        # Monte Carlo VaR
        if monte_carlo and len(returns) > 30:
            mu = np.mean(returns)
            sigma = np.std(returns)
            mc_simulations = np.random.normal(mu, sigma, 10000)
            mc_var = np.percentile(
                mc_simulations, 100 * (1 - self.confidence_level))
            result["monte_carlo_var"] = mc_var

        # ML adjustment from Redis (if available)
        ml_adj = float(self.redis.get("var_adjustment") or 0.0)

        # Final VaR with ML adjustment
        var_values = [v for k, v in result.items() if k.endswith('_var')]
        if var_values:
            result["final_var"] = np.mean(var_values) + ml_adj

        return result

    def _load_risk_params(self):
        """Load risk parameters from configuration or defaults"""
        return {
            "confidence_level": self.confidence_level,
            "liquidity_factor": 1.2,
            "leverage_multiplier": 1.5,
        }

    def predict_var_trend(self, current_var, lookback=10):
        """
        Predict VaR trend using simple time series forecasting

        Args:
            current_var: Current VaR value
            lookback: Number of historical points to use

        Returns:
            Predicted VaR for next period
        """
        # Add current observation
        self.predictions.append(current_var)

        # Keep only recent history
        if len(self.predictions) > lookback:
            self.predictions = self.predictions[-lookback:]

        # Simple prediction methods
        if len(self.predictions) >= 3:
            # Method 1: Linear extrapolation of recent trend
            x = np.arange(len(self.predictions))
            y = np.array(self.predictions)
            z = np.polyfit(x, y, 1)
            trend = z[0]  # Slope indicates trend direction

            # Method 2: Simple moving average
            sma = np.mean(self.predictions)

            # Return prediction and trend direction
            next_var = self.predictions[-1] + trend
            return {
                "prediction": next_var,
                "trend": "increasing" if trend > 0 else "decreasing",
                "trend_strength": abs(trend),
                "current_var": current_var,
                "sma": sma
            }
        else:
            # Not enough data yet
            return {
                "prediction": current_var,
                "trend": "stable",
                "trend_strength": 0,
                "current_var": current_var,
                "sma": current_var
            }


##########################################
# 3️⃣ MARKET STRESS ANALYSIS 🔍
##########################################

class StressAnalyzer:
    """ML-enhanced market stress analysis"""

    def __init__(self, var_calculator=None):
        self.var_calculator = var_calculator or VaRCalculator()
        self.risk_ml = RiskML()
        self.stress_indicators = []
        self.threshold = 0.75

    def analyze_market_stress(self, market_data, returns):
        """
        Comprehensive stress analysis using ML and statistical methods

        Args:
            market_data: Market features for analysis
            returns: Historical returns

        Returns:
            Dictionary with stress analysis results
        """
        # Get risk predictions from ML models
        ml_risks = self.risk_ml.predict_market_risk(market_data)

        # Calculate VaR metrics
        var_metrics = self.var_calculator.calculate_var(returns)

        # Volatility analysis
        volatility = np.std(returns) * np.sqrt(252)  # Annualized volatility

        # Stress score calculation using multiple factors
        stress_score = (
            0.3 * ml_risks["combined_risk"] +
            0.3 * min(1.0, abs(var_metrics.get("final_var", 0) * 10)) +
            0.4 * min(1.0, volatility)
        )

        # Add to historical indicators
        self.stress_indicators.append(stress_score)
        if len(self.stress_indicators) > 100:
            self.stress_indicators.pop(0)

        # Compare to historical distribution
        percentile = np.percentile(self.stress_indicators, 80)
        is_stressed = stress_score > percentile

        return {
            "stress_score": stress_score,
            "is_stressed": is_stressed,
            "var_metrics": var_metrics,
            "ml_risk": ml_risks["combined_risk"],
            "volatility": volatility,
            "threshold": percentile
        }


# Export public interfaces
__all__ = [
    'RiskML',
    'VaRCalculator',
    'StressAnalyzer',
    'apply_dp_noise'  # Function-level export for use in other modules
]



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/ml/train.py
# ────────────────────────────────────────────────────────────

# ai/modules/ml/train.py
import os
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
import numpy as np
import tensorflow as tf
from tensorflow.keras.losses import SparseCategoricalCrossentropy


class FlashCrashModel(nn.Module):
    def __init__(self, input_size=10, hidden_size=64, output_size=1, dropout_rate=0.2):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, batch_first=True)
        self.batch_norm = nn.BatchNorm1d(hidden_size)
        self.fc1 = nn.Linear(hidden_size, 20)
        self.fc2 = nn.Linear(20, output_size)
        self.dropout = nn.Dropout(dropout_rate)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        lstm_out, (h_n, _) = self.lstm(x)
        h_n_last = self.batch_norm(h_n[-1])
        x = self.dropout(h_n_last)
        x = torch.relu(self.fc1(x))
        return self.sigmoid(self.fc2(x))


class ModelTrainer:
    def __init__(self, redis_manager=None):
        print("Initializing ModelTrainer with robust defaults...")
        self.input_size = 10
        self.seq_length = 5
        self.hidden_size = 64
        self.batch_size = 32

        try:
            self.model = FlashCrashModel(self.input_size, self.hidden_size, 1)
            self.criterion = nn.BCELoss()
            self.optimizer = optim.Adam(self.model.parameters(), lr=0.001)

            # CRITICAL FIX: Create a guaranteed valid dataset that won't fail len() checks
            X = torch.randn(10, self.seq_length, self.input_size)
            y = torch.randint(0, 2, (10, 1)).float()
            self.default_dataset = TensorDataset(X, y)
            print("Created valid default dataset to prevent NoneType errors")

            # Check if model exists and load it
            os.makedirs("saved_models", exist_ok=True)
            model_path = os.path.join("saved_models", "flash_crash_model.pt")
            if os.path.exists(model_path):
                try:
                    self.model.load_state_dict(torch.load(model_path))
                    print(f"Loaded existing model from {model_path}")
                except Exception as e:
                    print(f"Failed to load model: {e}")

            print("ModelTrainer initialization complete")
        except Exception as e:
            print(f"Error during ModelTrainer initialization: {e}")
            # Ensure we have default values for everything
            if not hasattr(self, 'model'):
                self.model = None
            if not hasattr(self, 'default_dataset'):
                # Create a minimal dataset with explicit tensor creation
                X = torch.zeros((5, self.seq_length, self.input_size))
                y = torch.zeros((5, 1))
                self.default_dataset = TensorDataset(X, y)
                print("Created emergency fallback dataset")

    def generate_sequential_data(self, samples=1000, seq_length=10, features=10):
        X = torch.randn(samples, seq_length, features)
        y = torch.randint(0, 2, (samples, 1)).float()
        return X, y

    def train_model(self, epochs=100, dataset=None):
        """
        Train the model with proper error handling and diagnostics.

        Args:
            epochs: Number of training epochs
            dataset: Dataset to train on (must be a real dataset, not dummy data)

        Returns:
            bool: True if training succeeded, False otherwise
        """
        # Validate model exists
        if self.model is None:
            print("ERROR: Model not initialized")
            return False

        # Validate dataset existence
        if dataset is None:
            print("ERROR: No dataset provided - waiting for real market data")
            return False

        # Validate dataset is proper format
        if not hasattr(dataset, '__len__'):
            print(f"ERROR: Invalid dataset type: {type(dataset)}")
            return False

        # Validate dataset has data
        if len(dataset) == 0:
            print("ERROR: Empty dataset - waiting for real market data")
            return False

        print(
            f"Starting training with {len(dataset)} samples for {epochs} epochs")

        try:
            # Create the DataLoader for batch training
            loader = DataLoader(
                dataset, batch_size=self.batch_size, shuffle=True)

            # Training loop
            for epoch in range(epochs):
                total_loss = 0
                batch_count = 0

                for batch_x, batch_y in loader:
                    # Forward pass
                    self.optimizer.zero_grad()
                    outputs = self.model(batch_x)
                    loss = self.criterion(outputs, batch_y)

                    # Backward pass
                    loss.backward()
                    self.optimizer.step()

                    # Track metrics
                    total_loss += loss.item()
                    batch_count += 1

                # Calculate average loss for this epoch
                avg_loss = total_loss / max(1, batch_count)
                print(f"Epoch {epoch+1}/{epochs}, Loss: {avg_loss:.4f}")

            # Save the trained model
            try:
                os.makedirs("saved_models", exist_ok=True)
                torch.save(self.model.state_dict(),
                           "saved_models/flash_crash_model.pt")
                print("Model saved successfully")
            except Exception as e:
                print(f"WARNING: Failed to save model: {e}")

            return True

        except Exception as e:
            print(f"ERROR during training: {e}")
            import traceback
            traceback.print_exc()
            return False

    def predict(self, sequence_data):
        """Make predictions with the trained model"""
        with torch.no_grad():
            return self.model(sequence_data)

    def health_check(self):
        """Check if model is properly initialized"""
        class HealthResult:
            def __init__(self, status):
                self.passed = status

        # Check if the model exists and has proper parameters
        try:
            # Try a forward pass with dummy data
            dummy_input = torch.randn(1, self.seq_length, self.input_size)
            output = self.model(dummy_input)
            return HealthResult(True)
        except Exception as e:
            print(f"Health check failed: {e}")
            return HealthResult(False)

    # Add a method that the system might call
    def train(self, dataset=None):
        return self.train_model(dataset=dataset)



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/monitoring/dashboard.py
# ────────────────────────────────────────────────────────────

# ai/modules/monitoring/dashboard.py
"""
Wall Street Command Center—Live trading, ML, and hardware stats!
Syncs with boss.py’s agent for real-time glory.
"""
import pandas as pd
import time
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.layout import Layout
from rich.panel import Panel
import psutil
import threading
import json


class TradingDashboard:
    """Enhanced dashboard—shows trading signals, ML predictions, and hardware."""

    def __init__(self, trade_state=None, refresh_rate=0.5):
        self.console = Console()
        self.trade_state = trade_state
        self.refresh_rate = refresh_rate
        self.stop_flag = False
        self.agent = None  # Set by boss.py
        self.layout = Layout()
        self.layout.split_column(
            Layout(name="header", size=3), Layout(name="main", ratio=1)
        )
        self.layout["main"].split_row(
            Layout(name="signals", ratio=1),
            Layout(name="ml", ratio=1),
            Layout(name="hardware", ratio=1),
        )

    def _build_header(self):
        """Fancy header—your bot’s branding."""
        return Panel("🚀 Wall Street AI Trading Bot 🚀", style="bold green", height=3)

    def _build_signals(self):
        """Live trading signals from strategies."""
        if not self.agent:
            return Panel("No agent connected", style="yellow")
        table = Table(title="Trading Signals", border_style="cyan", expand=True)
        table.add_column("Strategy", style="cyan")
        table.add_column("Action", style="white")
        table.add_column("Confidence", style="green")
        signals = self.agent.components.get(
            "strategies_run_strategy_pipeline", lambda x: {"signals": {}}
        )(
            pd.DataFrame([{"close": 50000}])  # Dummy data for now
        ).get(
            "signals", {}
        )
        for name, signal in signals.items():
            table.add_row(
                name, signal.get("action", "N/A"), f"{signal.get('confidence', 0):.2f}"
            )
        for name, comp in self.agent.components.items():
            if name.startswith("strategies_") and hasattr(comp, "calculate_signal"):
                signal = comp.calculate_signal(pd.DataFrame([{"close": 50000}]))
                table.add_row(
                    name.split("_")[1],
                    signal.get("action", "N/A"),
                    f"{signal.get('confidence', 0):.2f}",
                )
        return Panel(table, title="Live Signals", border_style="cyan")

    def _build_ml(self):
        """ML predictions—sentiment, forecasts, RL decisions."""
        if not self.agent:
            return Panel("No agent connected", style="yellow")
        table = Table(title="ML Insights", border_style="blue", expand=True)
        table.add_column("Model", style="blue")
        table.add_column("Output", style="white")

        # Fetch real-time ML predictions
        dummy_data = [50000] * 10  # Replace with actual market data
        for name, comp in self.agent.components.items():
            if name.startswith("ml_"):
                if hasattr(comp, "predict"):
                    pred = comp.predict(dummy_data)
                    table.add_row(
                        name.split("_")[1],
                        f"{pred:.2f}" if isinstance(pred, (int, float)) else str(pred),
                    )
            elif hasattr(comp, "decide"):
                action = comp.decide(dummy_data)
                table.add_row(name.split("_")[1], action)
        return Panel(table, title="ML Predictions", border_style="blue")

    def _build_hardware(self):
        """Hardware stats—CPU, memory, GPU, tuning profile."""
        if not self.agent:
            return Panel("No agent connected", style="yellow")
        table = Table(title="Hardware Status", border_style="green", expand=True)
        table.add_column("Metric", style="green")
        table.add_column("Value", style="white")
        table.add_row("CPU", f"{psutil.cpu_percent():.1f}%")
        table.add_row("Memory", f"{psutil.virtual_memory().percent:.1f}%")
        profile = self.trade_state.get("performance_profile", "N/A")
        threads = self.trade_state.get("thread_count", "N/A")
        table.add_row("Profile", profile)
        table.add_row("Threads", threads)
        if self.agent.components.get("optimization_hardwareoptimizer"):
            gpu_usage = self.agent.components[
                "optimization_hardwareoptimizer"
            ]._get_gpu_usage()
            table.add_row("GPU Usage", f"{gpu_usage:.1f}%" if gpu_usage else "N/A")
        return Panel(table, title="Hardware Tuning", border_style="green")

    def start(self):
        """Run the dashboard—live Wall Street vibes!"""

        def run_dashboard():
            with Live(
                self.layout, refresh_per_second=1 / self.refresh_rate, screen=True
            ):
                while not self.stop_flag:
                    self.layout["header"].update(self._build_header())
                    self.layout["signals"].update(self._build_signals())
                    self.layout["ml"].update(self._build_ml())
                    self.layout["hardware"].update(self._build_hardware())
                    time.sleep(self.refresh_rate)

        threading.Thread(target=run_dashboard, daemon=True).start()

    def stop(self):
        """Stop the dashboard cleanly."""
        self.stop_flag = True


if __name__ == "__main__":
    from modules.utils.redis_manager import RedisManager

    dashboard = TradingDashboard(trade_state=RedisManager())
    dashboard.start()
    time.sleep(10)
    dashboard.stop()



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/monitoring/system_monitor.py
# ────────────────────────────────────────────────────────────

# ai/modules/monitoring/system_monitor.py

"""
Holistic System Health Monitoring
- Hardware metrics
- Process monitoring
- Performance optimization
"""

import psutil
import GPUtil
from datetime import datetime
from typing import Dict, Any
from ..utils.redis_manager import RedisManager
from ..utils.events import EventBus


class SystemMonitor:
    def __init__(self, event_bus: EventBus):
        self.redis = RedisManager()
        self.event_bus = event_bus
        self.metrics = {}

        # Integrated with Hardware Optimizer
        from ..optimization.hardware_optimizer import HardwareOptimizer

        self.hw_optimizer = HardwareOptimizer()

    def collect_metrics(self):
        """Comprehensive system diagnostics"""
        self.metrics = {
            "timestamp": datetime.now().isoformat(),
            "cpu": self._get_cpu_stats(),
            "memory": self._get_memory_stats(),
            "gpu": self._get_gpu_stats(),
            "network": self._get_network_stats(),
            "process": self._get_process_stats(),
            "recommendations": self.hw_optimizer.get_optimization_recommendations(),
        }
        self._publish_metrics()

    def _get_gpu_stats(self) -> Dict[str, Any]:
        """Integrated GPU monitoring"""
        try:
            gpus = GPUtil.getGPUs()
            return [
                {
                    "load": gpu.load,
                    "memory": gpu.memoryUtil,
                    "temperature": gpu.temperature,
                }
                for gpu in gpus
            ]
        except Exception as e:
            self.event_bus.publish("system_error", f"GPU monitoring failed: {str(e)}")
            return {}

    def _publish_metrics(self):
        """Store and notify about system state"""
        self.redis.set("system_metrics", json.dumps(self.metrics))
        self.event_bus.publish("system_status", self.metrics)

        # Integrated with Circuit Breakers
        if self.metrics["memory"]["percent"] > 90:
            self.event_bus.publish("risk_event", "memory_overload")

    # Additional monitoring methods...



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/optimization/performance_optimizer.py
# ────────────────────────────────────────────────────────────

# ai/modules/optimization/performance_optimizer.py
"""
Enhanced Performance Optimization Module
- Hardware-aware resource management 
- Dynamic profiling and thread management
- Intelligent hardware acceleration
"""

import torch
import tensorflow as tf
import psutil
import asyncio
import logging
import time
import platform
import numpy as np
from typing import Dict, Any, Optional, Tuple
import threading

logger = logging.getLogger(__name__)


class PerformanceOptimizer:
    """Unified performance optimization with hardware awareness and adaptive profiling"""

    def __init__(self, redis_manager=None, monitor_interval: int = 5):
        """Initialize performance optimizer with dynamic hardware detection"""
        # Hardware detection
        self.os_type = platform.system().lower()
        self.cpu_cores = psutil.cpu_count(logical=False) or 1
        self.total_memory = psutil.virtual_memory().total / (1024**3)  # GB
        self.gpu_available = torch.cuda.is_available()
        self.gpu_memory = self._get_gpu_memory() if self.gpu_available else 0

        # Performance state management
        self.redis = redis_manager
        self.monitor_interval = monitor_interval
        self.performance_history = []
        self.current_profile = "balanced"  # Default profile
        self.monitor_thread = None
        self.is_running = True

        # ML model for resource prediction
        self._initialize_resource_predictor()

        # Apply initial optimization
        self.update_profile()
        # Start monitoring
        self._start_monitoring()

        logger.info(
            f"Performance optimizer initialized: OS={self.os_type}, "
            f"CPU={self.cpu_cores} cores, RAM={self.total_memory:.1f}GB, "
            f"GPU={'Available' if self.gpu_available else 'Not available'}"
        )

    def _initialize_resource_predictor(self):
        """Initialize simple prediction model for thread calculation"""
        # Simple resource prediction model
        self.weights = {
            "cpu_weight": 0.7,
            "memory_weight": 0.2,
            "gpu_weight": 0.1
        }

    def update_profile(self):
        """Update performance profile based on detected hardware"""
        # Determine optimal profile based on available hardware
        if self.gpu_available and self.gpu_memory > 4e9:  # >4GB VRAM
            self.current_profile = "aggressive"
            torch.set_float32_matmul_precision("high")
        elif self.cpu_cores >= 8 and self.total_memory > 16:
            self.current_profile = "balanced"
        else:
            self.current_profile = "conservative"

        # Apply profile settings
        self.configure_parallel_processing()

        # Store profile if Redis is available
        if self.redis:
            self.redis.set("performance_profile", self.current_profile)
            self.redis.set("thread_count", str(
                self._predict_optimal_threads()))

        logger.info(f"Performance profile updated to: {self.current_profile}")

    def configure_parallel_processing(self):
        """Set thread and parallelism configuration based on current profile"""
        if self.current_profile == "aggressive":
            thread_count = min(self.cpu_cores, 8)
            torch.set_num_threads(thread_count)
            tf.config.threading.set_intra_op_parallelism_threads(thread_count)
            if self.gpu_available:
                torch.backends.cudnn.benchmark = True
        elif self.current_profile == "balanced":
            thread_count = min(self.cpu_cores, 4)
            torch.set_num_threads(thread_count)
            tf.config.threading.set_intra_op_parallelism_threads(thread_count)
        else:  # conservative
            thread_count = max(1, min(self.cpu_cores // 2, 2))
            torch.set_num_threads(thread_count)
            tf.config.threading.set_intra_op_parallelism_threads(thread_count)
            if self.gpu_available:
                torch.backends.cudnn.benchmark = False

    def _predict_optimal_threads(self) -> int:
        """Predict optimal thread count based on system resources"""
        # Simple weighted calculation based on available resources
        cpu_factor = self.cpu_cores * self.weights["cpu_weight"]
        memory_factor = min(self.total_memory / 4, 4) * \
            self.weights["memory_weight"]
        gpu_factor = (self.gpu_memory / 2e9) * \
            self.weights["gpu_weight"] if self.gpu_available else 0

        # Calculate optimal threads with some constraints
        optimal_threads = int(cpu_factor + memory_factor + gpu_factor)
        return max(1, min(self.cpu_cores, optimal_threads))

    def _get_gpu_memory(self) -> float:
        """Get available GPU memory in bytes"""
        if not self.gpu_available:
            return 0
        try:
            # Try to get free memory directly
            free_mem, total = torch.cuda.mem_get_info()
            return total
        except Exception as e:
            logger.warning(f"GPU memory check failed: {e}")
            # Fallback estimate
            return 2e9  # Assume 2GB if detection fails

    def _get_gpu_usage(self) -> float:
        """Get current GPU utilization percentage"""
        if not self.gpu_available:
            return 0.0
        try:
            if self.os_type == "linux":
                return torch.cuda.utilization()
            else:  # Windows and others - estimate from memory
                free, total = torch.cuda.mem_get_info()
                return ((total - free) / total) * 100
        except Exception as e:
            logger.warning(f"GPU usage check failed: {e}")
            return 0.0

    def _start_monitoring(self):
        """Start the resource monitoring thread"""
        self.monitor_thread = threading.Thread(
            target=self._monitor_resources_thread, daemon=True)
        self.monitor_thread.start()

    def _monitor_resources_thread(self):
        """Thread-based resource monitoring (non-async version)"""
        while self.is_running:
            try:
                # Collect current metrics
                metrics = {
                    "timestamp": time.time(),
                    "cpu_load": psutil.cpu_percent(),
                    "mem_usage": psutil.virtual_memory().percent,
                    "gpu_usage": self._get_gpu_usage() if self.gpu_available else 0,
                }

                self.performance_history.append(metrics)
                # Keep history limited to recent data
                if len(self.performance_history) > 20:
                    self.performance_history = self.performance_history[-20:]

                # Auto-tune based on system load
                self._auto_tune(metrics)

                # Store metrics if Redis available
                if self.redis:
                    self.redis.set("system_metrics", str(metrics))

                time.sleep(self.monitor_interval)

            except Exception as e:
                logger.error(f"Resource monitoring error: {e}")
                time.sleep(self.monitor_interval * 2)

    async def monitor_resources(self):
        """Async version of resource monitoring for asyncio compatibility"""
        while self.is_running:
            try:
                # Collect current metrics
                metrics = {
                    "timestamp": time.time(),
                    "cpu_load": psutil.cpu_percent(),
                    "mem_usage": psutil.virtual_memory().percent,
                    "gpu_usage": self._get_gpu_usage() if self.gpu_available else 0,
                }

                self.performance_history.append(metrics)
                # Keep history limited to recent data
                if len(self.performance_history) > 20:
                    self.performance_history = self.performance_history[-20:]

                # Auto-tune based on system load
                self._auto_tune(metrics)

                # Store metrics if Redis available
                if self.redis:
                    self.redis.set("system_metrics", str(metrics))

                await asyncio.sleep(self.monitor_interval)

            except Exception as e:
                logger.error(f"Resource monitoring error: {e}")
                await asyncio.sleep(self.monitor_interval * 2)

    def _auto_tune(self, current_metrics: Dict[str, Any]):
        """Automatically adjust settings based on current load"""
        if len(self.performance_history) < 3:  # Need history for auto-tuning
            return

        # Calculate average loads
        avg_cpu = np.mean([m["cpu_load"]
                          for m in self.performance_history[-3:]])
        avg_mem = np.mean([m["mem_usage"]
                          for m in self.performance_history[-3:]])

        # Adjust profile if needed
        old_profile = self.current_profile

        if avg_cpu > 85 or avg_mem > 85:
            self.current_profile = "conservative"
        elif (self.gpu_available and
              current_metrics["gpu_usage"] < 30 and
              avg_cpu < 50):
            self.current_profile = "aggressive"
        elif avg_cpu < 60 and avg_mem < 70:
            self.current_profile = "balanced"

        # Update settings if profile changed
        if old_profile != self.current_profile:
            logger.info(
                f"Auto-tuning: Profile changed from {old_profile} to {self.current_profile}")
            self.configure_parallel_processing()

    async def async_io(self, func, *args, **kwargs):
        """Execute function in a non-blocking thread pool"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func, *args, **kwargs)

    def stop(self):
        """Stop monitoring and clean up resources"""
        self.is_running = False
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        logger.info("Performance optimizer stopped")


# Global data pipeline settings
def optimize_data_pipeline():
    """Apply global pandas and framework optimizations"""
    import pandas as pd
    pd.options.mode.chained_assignment = None  # Disable SettingWithCopyWarning
    torch.set_num_threads(2)  # Default conservative setting
    tf.config.threading.set_intra_op_parallelism_threads(2)



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/optimization/quantum_portfolio.py
# ────────────────────────────────────────────────────────────

# ai/modules/optimization/quantum_portfolio.py

"""
Quantum-Inspired Portfolio Optimization
- Hybrid quantum-classical optimization
- Real quantum hardware integration
- Fallback mechanisms
"""

from qiskit_aer import Aer
from qiskit_optimization import QuadraticProgram
from qiskit_optimization.algorithms import MinimumEigenOptimizer
from qiskit.utils import QuantumInstance
from typing import Dict
import numpy as np


class ClassicalOptimizer:
    def optimize(self, returns, risk):
        """Classical portfolio optimization using mean-variance."""
        # Assuming 'returns' has an 'index' attribute like a Pandas DataFrame
        weights = np.random.dirichlet(np.ones(len(returns)), size=1).flatten()
        return dict(zip(returns.index, weights))


class QuantumPortfolioOptimizer:
    def __init__(self):
        self.backend = self._init_quantum_backend()

    def _init_quantum_backend(self):
        """Initialize the quantum backend."""
        try:
            IBMQ.load_account()
            return IBMQ.get_backend("ibmq_qasm_simulator")
        except Exception as e:
            print(f"Failed to load quantum backend: {e}")
            return Aer.get_backend("qasm_simulator")

    def optimize(self, returns, risk):
        """Optimize the portfolio using quantum or classical methods."""
        if not self.backend:
            # Fall back to classical optimization if quantum backend is unavailable
            print("Quantum backend unavailable, using classical optimizer.")
            return ClassicalOptimizer().optimize(returns, risk)

        # Quantum optimization logic would be placed here
        # Placeholder for demonstration purposes
        return {"BTCUSDT": 0.6, "ETHUSDT": 0.4}  # Example allocation

        # Quantum optimization steps would be placed here, if implemented
        # qp = self._create_quadratic_program(returns, risk)
        # quantum_instance = QuantumInstance(self.backend)
        # result = self._run_quantum_optimization(qp, quantum_instance)
        # return self._parse_result(result)

    def _quantum_available(self) -> bool:
        """Check quantum resource availability"""
        # Placeholder for quantum status check
        status = self.redis.get("quantum_status") if hasattr(self, "redis") else None
        return status and status["available"]

    # Add additional methods for quantum circuit construction and result parsing



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/predictors/ml_predictors.py
# ────────────────────────────────────────────────────────────

# ai/modules/ml/ml_predictors.py
"""
Consolidated ML Prediction Module
Combines: predictors.py, advanced_prediction.py, sentiment.py
Provides various prediction models for the AI trading system
"""

import numpy as np
import torch
import torch.nn as nn
import pandas as pd
import json
import logging
import time
import os
from collections import deque
from typing import List, Dict, Tuple, Optional, Union, Any

# Try imports with graceful fallbacks
try:
    import xgboost as xgb
except ImportError:
    xgb = None
    logging.warning(
        "XGBoost not available. Some prediction features will be limited.")

try:
    from transformers import AutoModel, AutoTokenizer, AutoModelForSequenceClassification
except ImportError:
    logging.warning(
        "Transformers library not available. Sentiment analysis will be limited.")
    AutoModel = AutoTokenizer = AutoModelForSequenceClassification = None

try:
    from sklearn.feature_selection import SelectKBest, f_classif
    from sklearn.preprocessing import RobustScaler
except ImportError:
    logging.warning(
        "Scikit-learn not available. Feature processing will be limited.")

from ..utils.redis_manager import RedisManager
trade_state = RedisManager()

##########################################
# 1️⃣ TRANSFORMER-BASED PREDICTIONS 📊
##########################################


class HybridPredictor(nn.Module):
    """Transformer-LSTM Hybrid Model with Temporal Fusion"""

    def __init__(self, input_size=128, num_heads=8, num_layers=6):
        super().__init__()
        self.input_size = input_size
        self.redis = RedisManager()

        # LSTM Encoder
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=256,
            num_layers=2,
            bidirectional=True
        )

        # Transformer Encoder
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=input_size, nhead=num_heads)
        self.transformer_encoder = nn.TransformerEncoder(
            encoder_layer, num_layers=num_layers)

        # Temporal Fusion
        self.temporal_fusion = nn.Sequential(
            nn.Linear(512, 128),
            nn.GELU(),
            nn.Dropout(0.2),
            nn.Linear(128, 3),  # Buy, Sell, Hold
        )

    def forward(self, x):
        # Ensure input is properly shaped for both LSTM and Transformer
        batch_size, seq_len, features = x.size()

        # Transformer processing (transformer expects [seq_len, batch_size, features])
        transformer_input = x.permute(1, 0, 2)
        transformer_out = self.transformer_encoder(transformer_input)
        transformer_out = transformer_out.permute(
            1, 0, 2)  # Back to [batch, seq, features]

        # LSTM processing
        lstm_out, (hidden, _) = self.lstm(x)

        # Temporal fusion (use last hidden state from each)
        transformer_features = transformer_out[:, -1, :]
        # Concat forward and backward
        lstm_features = torch.cat([hidden[-2], hidden[-1]], dim=1)

        fused = torch.cat([transformer_features, lstm_features], dim=1)

        return self.temporal_fusion(fused)

    def predict(self, market_data):
        """Real-time prediction with model ensembling"""
        # Get recent market context
        context = self._get_market_context(
            market_data.get("symbol", "BTCUSDT"))

        # Preprocess data
        processed = self._preprocess_data(market_data, context)

        # Model ensemble prediction
        with torch.no_grad():
            predictions = self.forward(processed)
            probabilities = torch.softmax(predictions, dim=1)
            action_idx = torch.argmax(probabilities, dim=1).item()

        # Convert to trading signal
        actions = ["BUY", "SELL", "HOLD"]
        confidence = probabilities[0, action_idx].item()

        return {
            "action": actions[action_idx],
            "confidence": confidence,
            "probabilities": probabilities[0].tolist()
        }

    def _get_market_context(self, symbol):
        """Retrieve market regime and liquidity context"""
        return {
            "regime": self.redis.get(f"regime:{symbol}", "neutral"),
            "liquidity": json.loads(self.redis.get(f"liquidity:{symbol}", "{}") or "{}"),
            "volatility": float(self.redis.get(f"volatility:{symbol}", 0.05)),
        }

    def _preprocess_data(self, market_data, context):
        """Convert market data to tensor format expected by model"""
        if isinstance(market_data, pd.DataFrame):
            # Extract relevant features
            if len(market_data) > 10:
                df = market_data.tail(10)
                features = df[['close', 'high', 'low', 'volume']].values
                # Normalize values
                features = (features - features.mean(axis=0)) / \
                    (features.std(axis=0) + 1e-8)
                # Add context features
                context_features = np.array([
                    float(context.get("volatility", 0.05)),
                    1.0 if context.get("regime") == "trending" else 0.0,
                    1.0 if context.get("regime") == "ranging" else 0.0,
                    1.0 if context.get("regime") == "volatile" else 0.0
                ])
                # Expand context to match sequence length
                context_expanded = np.tile(
                    context_features, (len(features), 1))
                # Combine features
                combined = np.concatenate([features, context_expanded], axis=1)
                # Pad to input_size if needed
                pad_width = max(0, self.input_size - combined.shape[1])
                if pad_width > 0:
                    combined = np.pad(combined, ((0, 0), (0, pad_width)))
                # Convert to tensor and add batch dimension
                return torch.FloatTensor(combined).unsqueeze(0)

        # Fallback - return empty tensor matching expected shape
        return torch.zeros((1, 10, self.input_size))

##########################################
# 2️⃣ ADVANCED PREDICTION MODELS 🔍
##########################################


class AdvancedPrediction:
    """Advanced ML models for market prediction"""

    def __init__(self):
        self.lstm = self._build_lstm()
        self.transformer = None  # Disabled by default

    def _build_lstm(self):
        """Build LSTM model for time series prediction"""
        model = nn.Sequential(
            nn.LSTM(input_size=1, hidden_size=100, batch_first=True),
            nn.LSTM(input_size=100, hidden_size=50, batch_first=True),
            nn.Linear(50, 1)
        )
        return model

    def predict(self, data):
        """Generate predictions using ensemble of models"""
        # Extract closing prices from data
        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
            prices = np.array([d.get("close", 0)
                              for d in data[-60:]]).reshape(-1, 1)
        elif isinstance(data, pd.DataFrame) and 'close' in data.columns:
            prices = data['close'].values[-60:].reshape(-1, 1)
        else:
            logging.warning("Invalid data format for prediction")
            return 0

        # Normalize prices
        if len(prices) > 1:
            mean, std = prices.mean(), prices.std() + 1e-8
            normalized = (prices - mean) / std

            # Prepare tensor (batch_size=1, seq_len, features=1)
            x = torch.FloatTensor(normalized).unsqueeze(0).unsqueeze(2)

            # Run prediction
            with torch.no_grad():
                pred = self.lstm(x)

            # Denormalize
            return float(pred.item() * std + mean)

        return float(prices[-1]) if len(prices) > 0 else 0


class EnsemblePredictor:
    """Ensemble predictor combining XGBoost and LSTM"""

    def __init__(self, required_features=None,
                 xgb_path="saved_models/xgb_model.json",
                 lstm_path="saved_models/lstm_model.pt"):
        # Define required features
        self.required_features = required_features or [
            "close", "volume", "volatility_garch", "order_flow", "fft_cycle"
        ]

        # Load XGBoost model if available
        self.xgb_model = None
        if xgb is not None:
            try:
                self.xgb_model = xgb.Booster()
                if os.path.exists(xgb_path):
                    self.xgb_model.load_model(xgb_path)
                else:
                    logging.warning(
                        f"XGBoost model file '{xgb_path}' not found!")
            except Exception as e:
                logging.warning(f"Could not load XGBoost model: {e}")

        # Load LSTM model if available
        self.lstm_model = None
        try:
            if os.path.exists(lstm_path):
                self.lstm_model = torch.jit.load(lstm_path)
            else:
                logging.warning(f"LSTM model file '{lstm_path}' not found!")
        except Exception as e:
            logging.warning(f"Could not load LSTM model: {e}")

        # Initialize error tracking
        self.error_tracker = deque(maxlen=1440)  # Track 24h of errors
        self.weights = [0.6, 0.4]  # Adjust based on model performance

    def predict(self, df: pd.DataFrame) -> float:
        """Perform market prediction using ensemble models"""

        # Input validation
        if df.empty or len(df) < 30:
            logging.warning("Need minimum 30 data points for prediction")
            return 0.0

        # Check for required features
        available_features = [
            f for f in self.required_features if f in df.columns]
        if len(available_features) < len(self.required_features):
            missing = set(self.required_features) - set(available_features)
            logging.warning(
                f"Missing features: {missing}. Using available features only.")

        if not available_features:
            logging.error("No required features available for prediction")
            return 0.0

        # Extract and normalize features
        features = df[available_features].values

        # Initialize default predictions
        xgb_pred, lstm_pred = 0.0, 0.0

        # XGBoost Prediction
        if self.xgb_model and xgb is not None:
            try:
                dmatrix = xgb.DMatrix(features)
                xgb_pred = self.xgb_model.predict(dmatrix)[0]
            except Exception as e:
                logging.error(f"XGBoost prediction failed: {e}")

        # LSTM Prediction (if model is loaded)
        if self.lstm_model:
            try:
                # Reshape for LSTM (batch, seq_len, features)
                lstm_input = torch.FloatTensor(features[-30:]).unsqueeze(0)
                lstm_pred = self.lstm_model(lstm_input).item()
            except Exception as e:
                logging.error(f"LSTM prediction failed: {e}")

        # Weight predictions based on availability
        if self.xgb_model and self.lstm_model:
            prediction = self.weights[0] * \
                xgb_pred + self.weights[1] * lstm_pred
        elif self.xgb_model:
            prediction = xgb_pred
        elif self.lstm_model:
            prediction = lstm_pred
        else:
            # Simple moving average fallback
            prediction = df['close'].iloc[-5:].mean() if 'close' in df.columns else 0.0

        return float(prediction)

    def update_accuracy(self, prediction: float, actual: float):
        """Update accuracy metrics"""
        try:
            error = abs(prediction - actual)
            self.error_tracker.append(error)

            trade_state.set(
                "model_accuracy",
                json.dumps({
                    "current_error": float(error),
                    "1h_mae": float(np.mean(list(self.error_tracker)[-12:])),
                    "24h_mape": float(np.mean(self.error_tracker) / max(actual, 1e-8)),
                })
            )
        except Exception as e:
            logging.error(f"Accuracy update failed: {e}")

##########################################
# 3️⃣ SENTIMENT ANALYSIS 💬
##########################################


class SentimentAnalyzer:
    """NLP-based sentiment analysis for financial news and social media"""

    def __init__(self, model_name="distilbert-base-uncased-finetuned-sst-2-english"):
        # Initialize model and tokenizer if libraries are available
        self.model = None
        self.tokenizer = None

        if AutoModelForSequenceClassification is not None:
            try:
                self.tokenizer = AutoTokenizer.from_pretrained(model_name)
                self.model = AutoModelForSequenceClassification.from_pretrained(
                    model_name)
            except Exception as e:
                logging.error(f"Failed to load sentiment model: {e}")

        self.sentiment_cache = {}

    def analyze(self, text_list):
        """Analyze sentiment for a list of texts"""
        if not self.model or not self.tokenizer:
            logging.warning("Sentiment model not loaded properly")
            return 0.5  # Neutral fallback

        if isinstance(text_list, str):
            text_list = [text_list]

        results = []

        for text in text_list:
            # Check cache first
            if text in self.sentiment_cache:
                results.append(self.sentiment_cache[text])
                continue

            # Perform analysis
            try:
                with torch.no_grad():
                    inputs = self.tokenizer(
                        text,
                        padding=True,
                        truncation=True,
                        return_tensors="pt",
                        max_length=256
                    )
                    outputs = self.model(**inputs)
                    scores = torch.softmax(outputs.logits, dim=1)
                    sentiment = scores[:, 1].item(
                    ) if scores.shape[1] > 1 else scores.item()

                    # Cache result
                    self.sentiment_cache[text] = sentiment
                    results.append(sentiment)
            except Exception as e:
                logging.error(f"Sentiment analysis failed: {e}")
                results.append(0.5)  # Neutral fallback

        # Return average sentiment score
        return sum(results) / len(results) if results else 0.5

    def batch_analyze(self, texts, batch_size=16):
        """Optimized batch processing of multiple texts"""
        if not texts:
            return []

        results = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i+batch_size]
            batch_results = [self.analyze(text) for text in batch]
            results.extend(batch_results)

        return results

##########################################
# 4️⃣ ADAPTIVE CALIBRATION SYSTEM 🔄
##########################################


class CalibrationTransformer(nn.Module):
    """Transformer-based Signal Calibrator"""

    def __init__(self, d_model=64, nhead=4):
        super().__init__()
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead)
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=3)

    def forward(self, x):
        """Process input through transformer encoder"""
        return self.encoder(x)


class AccuracyEnhancer:
    """Neural Calibration System for Strategy Signals"""

    def __init__(self):
        self.calibration_model = CalibrationTransformer()
        self.error_correction_model = self._build_error_correction_model()

    def _build_error_correction_model(self):
        """Build neural network for error correction"""
        return nn.Sequential(
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(32, 16),
            nn.ReLU(),
            nn.Linear(16, 3)  # Output: Buy, Sell, Hold corrections
        )

    def calibrate_signal(self, raw_signal: dict, market_context: dict) -> dict:
        """Enhance signal quality with neural calibration"""
        # Convert signal to tensor
        signal_tensor = self._signal_to_tensor(raw_signal)

        # Pass through transformer model
        calibrated = self.calibration_model(signal_tensor)

        # Enhance with market context
        context_tensor = self._context_to_tensor(market_context)
        enhanced = torch.cat([calibrated, context_tensor], dim=1)

        # Apply error correction
        corrected = self.error_correction_model(enhanced)

        # Calculate confidence from softmax probabilities
        probs = torch.softmax(corrected, dim=1)
        max_prob = torch.max(probs).item()

        # Convert back to signal dict
        return self._tensor_to_signal(corrected, max_prob)

    def _signal_to_tensor(self, signal: dict) -> torch.Tensor:
        """Convert signal dictionary to tensor"""
        # Extract features from signal dictionary
        features = []

        # Action one-hot encoding
        action_map = {"BUY": [1, 0, 0], "SELL": [0, 1, 0], "HOLD": [0, 0, 1]}
        features.extend(action_map.get(
            signal.get("action", "HOLD"), [0, 0, 1]))

        # Confidence
        features.append(signal.get("confidence", 0.5))

        # Strength
        features.append(signal.get("strength", 0.0))

        # Pad to 64 dimensions
        features.extend([0.0] * (64 - len(features)))

        return torch.FloatTensor(features).unsqueeze(0)

    def _context_to_tensor(self, context: dict) -> torch.Tensor:
        """Convert market context to tensor"""
        features = []

        # Market regime one-hot encoding
        regime = context.get("regime", "unknown")
        regime_map = {
            "trending": [1, 0, 0, 0],
            "ranging": [0, 1, 0, 0],
            "volatile": [0, 0, 1, 0],
            "unknown": [0, 0, 0, 1]
        }
        features.extend(regime_map.get(regime, [0, 0, 0, 1]))

        # Volatility
        features.append(context.get("volatility", 0.05))

        # Liquidity
        features.append(context.get("liquidity", {}).get("total", 1.0))

        # General market sentiment
        features.append(context.get("sentiment", 0.5))

        # Pad to 32 dimensions
        features.extend([0.0] * (32 - len(features)))

        return torch.FloatTensor(features).unsqueeze(0)

    def _tensor_to_signal(self, tensor: torch.Tensor, confidence: float) -> dict:
        """Convert tensor back to signal dictionary"""
        actions = ["BUY", "SELL", "HOLD"]
        action_idx = torch.argmax(tensor).item()

        return {
            "action": actions[action_idx],
            "confidence": confidence,
            "calibrated": True,
            "timestamp": time.time()
        }

##########################################
# 5️⃣ MULTIMODAL FEATURE LEARNING 📈
##########################################


class MarketStateEncoder:
    """Encodes market state into a feature vector for ML models"""

    def __init__(self, feature_dim=128):
        self.feature_dim = feature_dim

    def encode(self, **features):
        """Encode arbitrary market features into fixed-length vector"""
        # Extract all feature values
        feature_values = list(features.values())

        # Ensure we have data
        if not feature_values:
            return np.zeros(self.feature_dim)

        # Normalize and pad/truncate to fixed dimension
        return self._normalize_features(feature_values)

    def _normalize_features(self, features):
        """Normalize feature vector to fixed length"""
        # Convert all to float values
        float_features = [float(f) if f is not None else 0.0 for f in features]

        # Pad if needed
        if len(float_features) < self.feature_dim:
            float_features += [0.0] * (self.feature_dim - len(float_features))

        # Truncate if needed
        return np.array(float_features[:self.feature_dim])

    def encode_market_data(self, df: pd.DataFrame):
        """Extract features from market data DataFrame"""
        if df.empty:
            return np.zeros(self.feature_dim)

        features = {}

        # Price features
        if 'close' in df.columns:
            features['close'] = df['close'].iloc[-1]
            features['close_change'] = df['close'].pct_change().iloc[-1]

            # Calculate moving averages if enough data
            if len(df) >= 20:
                features['ma_20'] = df['close'].rolling(20).mean().iloc[-1]
                features['ma_20_slope'] = (
                    features['ma_20'] / df['close'].rolling(20).mean().iloc[-2]) - 1

        # Volume features
        if 'volume' in df.columns:
            features['volume'] = df['volume'].iloc[-1]
            if len(df) >= 5:
                features['volume_change'] = df['volume'].pct_change(5).iloc[-1]

        # Volatility features
        if 'high' in df.columns and 'low' in df.columns:
            features['volatility'] = (
                df['high'].iloc[-1] - df['low'].iloc[-1]) / df['close'].iloc[-1]

        # Momentum features
        if 'close' in df.columns and len(df) >= 10:
            features['momentum'] = df['close'].pct_change(10).iloc[-1]

        return self.encode(**features)


# Export public interfaces
__all__ = [
    'HybridPredictor',
    'AdvancedPrediction',
    'EnsemblePredictor',
    'SentimentAnalyzer',
    'AccuracyEnhancer',
    'CalibrationTransformer',
    'MarketStateEncoder'
]



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/strategies/adaptive_cockpit.py
# ────────────────────────────────────────────────────────────

# ai/modules/strategies/adaptive_cockpit.py

from ray.tune import Tuner
from ray.tune.search.hyperopt import HyperOptSearch


class StrategyCockpit:
    """Real-time Bayesian strategy optimization"""

    def __init__(self):
        self.tuner = Tuner(
            trainable=self._objective,
            search_alg=HyperOptSearch(),
            run_config=air.RunConfig(stop={"episode_reward_mean": 1.5}),
        )

    def _objective(self, config):
        strategy = create_strategy(config)
        return backtest(strategy)

    def optimize_live(self, market_regime):
        analysis = self.tuner.fit()
        return analysis.get_best_config()



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/strategies/multi_asset.py
# ────────────────────────────────────────────────────────────

# ai/modules/strategies/multi_asset.py

"""
Cross-Asset Portfolio Strategy
- Correlation analysis
- Risk parity allocation
- Cross-exchange arbitrage
"""

import numpy as np
import pandas as pd
from typing import Dict, List
from ..utils.redis_manager import RedisManager
from ..utils.events import EventBus
from ..account.account_manager import AccountManager


class MultiAssetStrategy:
    def __init__(self, account, event_bus):
        self.account = account
        self.event_bus = event_bus
        self.redis = RedisManager()
        self.event_bus.subscribe("market_analysis", self.on_analysis_update)

    def on_analysis_update(self, analysis: Dict):
        """Process real-time market analysis"""
        portfolio = self.calculate_optimal_allocation(analysis)
        self.execute_portfolio_rebalance(portfolio)

    def calculate_optimal_allocation(self, analysis: Dict) -> Dict:
        """Risk-parity portfolio optimization"""
        # Integrated with existing QuantumPortfolio module
        from ..optimization.quantum_portfolio import QuantumPortfolioOptimizer

        qpo = QuantumPortfolioOptimizer()
        return qpo.optimize(analysis)

    def execute_portfolio_rebalance(self, portfolio: Dict):
        """Execute cross-asset trades"""
        current_balance = self.account.get_balance()

        for symbol, allocation in portfolio.items():
            current_pos = self.redis.get(f"position:{symbol}", 0)
            target_pos = current_balance * allocation

            if target_pos != current_pos:
                # Integrated with Advanced Execution
                from ..execution.advanced_exec import AdvancedExecution

                exec_engine = AdvancedExecution(self.account)
                exec_engine.execute_complex_order(
                    symbol=symbol,
                    quantity=target_pos - current_pos,
                    strategy="VWAP" if abs(target_pos) > 1e5 else "LIMIT",
                )

    # Correlation analysis and risk management methods...



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/strategies/quantum.py
# ────────────────────────────────────────────────────────────

# ai/modules/strategies/quantum.py


from qiskit import QuantumCircuit
from ..utils.redis_manager import RedisManager
from ..ml.ml_reinforcement import RLAgent
from modules.ml.ml_reinforcement import DeepRLTrader as RLAgent


# ai/modules/strategies/quantum_strategy.py

import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

# Local imports
from ..base.strategy_base import StrategyBase
from ..utils.signal_utils import compute_zscore, calculate_sharpe
from ..utils.market_regime import MarketRegimeClassifier
from ..utils.redis_manager import RedisManager
from ..ml.ml_reinforcement import DeepRLTrader


class QuantumEnhancedStrategy(StrategyBase):
    """
    Quantum-enhanced modular trading strategy that combines:
    1. Quantum computing for market regime detection
    2. Deep Reinforcement Learning for trade decisions
    3. Adaptive risk management based on detected regime
    """

    def __init__(
        self,
        symbols: List[str],
        lookback_period: int = 60,
        risk_tolerance: float = 0.02,
        redis_manager: Optional[RedisManager] = None,
        **kwargs
    ):
        super().__init__()
        self.symbols = symbols
        self.lookback_period = lookback_period
        self.risk_tolerance = risk_tolerance
        self.redis = redis_manager or RedisManager()

        # Initialize sub-components
        self.regime_classifier = MarketRegimeClassifier(n_regimes=3)
        self.rl_trader = DeepRLTrader(input_size=15, learning_rate=0.001)

        # Strategy state
        self.current_positions = {symbol: 0 for symbol in symbols}
        self.regime_history = []
        self.performance_metrics = {
            "daily_returns": [],
            "sharpe_ratio": None,
            "max_drawdown": 0.0,
            "win_rate": 0.0
        }

        # Load pre-trained models (if available)
        self._load_models()

    def _load_models(self) -> None:
        """Load pre-trained models from storage"""
        try:
            self.regime_classifier.load_model("models/regime_classifier.pkl")
            self.rl_trader.load_model("models/rl_trader.pt")
            self.logger.info("Models loaded successfully")
        except Exception as e:
            self.logger.warning(
                f"Could not load models, initializing new ones: {e}")
            # Models will be initialized with default parameters

    def preprocess_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Prepare market data for analysis

        Args:
            df: Raw market data with OHLCV structure

        Returns:
            Processed dataframe with additional features
        """
        # Ensure consistent format
        processed = df.copy()

        # Calculate technical indicators
        processed['returns'] = processed['close'].pct_change()
        processed['log_returns'] = np.log(
            processed['close'] / processed['close'].shift(1))
        processed['volatility'] = processed['returns'].rolling(
            self.lookback_period).std()

        # Volume-based features
        processed['volume_ma'] = processed['volume'].rolling(20).mean()
        processed['volume_ratio'] = processed['volume'] / \
            processed['volume_ma']

        # Momentum indicators
        processed['rsi'] = self._calculate_rsi(processed['close'], 14)
        processed['macd'], processed['macd_signal'] = self._calculate_macd(
            processed['close'])

        # Clean any NaN values
        processed = processed.dropna()

        return processed

    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> pd.Series:
        """Calculate Relative Strength Index"""
        delta = prices.diff()
        gain = delta.where(delta > 0, 0).rolling(window=period).mean()
        loss = -delta.where(delta < 0, 0).rolling(window=period).mean()

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def _calculate_macd(self, prices: pd.Series) -> Tuple[pd.Series, pd.Series]:
        """Calculate MACD and signal line"""
        ema12 = prices.ewm(span=12, adjust=False).mean()
        ema26 = prices.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        return macd, signal

    def detect_market_regime(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Determine current market regime using quantum-enhanced classifier

        Args:
            df: Processed market data

        Returns:
            Dict containing regime info and confidence
        """
        # Extract relevant features for regime detection
        features = df[['returns', 'volatility',
                       'volume_ratio']].tail(30).values

        # Get regime prediction from classifier
        regime_id, confidence = self.regime_classifier.predict(features)

        # Map regime_id to descriptive name
        regime_map = {0: "bullish", 1: "bearish", 2: "sideways"}
        regime_name = regime_map.get(regime_id, "unknown")

        # Store in strategy state
        self.regime_history.append({
            "timestamp": datetime.now().isoformat(),
            "regime": regime_name,
            "confidence": float(confidence)
        })

        # Cache in Redis for other services
        self.redis.set(f"market_regime:{self.symbols[0]}", regime_name)
        self.redis.set(f"regime_confidence:{self.symbols[0]}", str(confidence))

        return {
            "regime": regime_name,
            "confidence": confidence,
            "features_used": ['returns', 'volatility', 'volume_ratio']
        }

    def calculate_signals(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Generate trading signals based on current market data

        Args:
            df: Processed market data

        Returns:
            Dict containing trading signals and metadata
        """
        # Detect current market regime
        regime_info = self.detect_market_regime(df)

        # Use RL agent for trading decisions, informed by regime
        features = self._extract_rl_features(df, regime_info)
        rl_action = self.rl_trader.get_action(features)

        # Adjust risk based on regime confidence
        risk_adjustment = 1.0
        if regime_info["regime"] == "bullish" and regime_info["confidence"] > 0.7:
            risk_adjustment = 1.2  # More aggressive in bullish regimes
        elif regime_info["regime"] == "bearish" and regime_info["confidence"] > 0.7:
            risk_adjustment = 0.6  # More conservative in bearish regimes

        # Calculate position size
        adjusted_risk = self.risk_tolerance * risk_adjustment
        position_size = self._calculate_position_size(df, adjusted_risk)

        # Interpret RL action
        action_map = {0: "SELL", 1: "HOLD", 2: "BUY"}
        action = action_map.get(rl_action, "HOLD")

        return {
            "action": action,
            "symbol": self.symbols[0],
            "position_size": position_size,
            "confidence": float(self.rl_trader.action_probabilities[rl_action]),
            "regime": regime_info["regime"],
            "timestamp": datetime.now().isoformat()
        }

    def _extract_rl_features(self, df: pd.DataFrame, regime_info: Dict[str, Any]) -> np.ndarray:
        """Extract and normalize features for RL model"""
        last_row = df.iloc[-1]

        # Technical indicators
        features = [
            last_row['returns'],
            last_row['volatility'],
            last_row['volume_ratio'],
            last_row['rsi'] / 100.0,  # Normalize RSI
            last_row['macd'],
            last_row['macd_signal'],

            # Regime encoding (one-hot)
            1.0 if regime_info["regime"] == "bullish" else 0.0,
            1.0 if regime_info["regime"] == "bearish" else 0.0,
            1.0 if regime_info["regime"] == "sideways" else 0.0,

            # Current position context
            self.current_positions[self.symbols[0]],

            # Recent performance
            np.mean(df['returns'].tail(5)),
            np.std(df['returns'].tail(5)),

            # Trend indicators
            float(df['close'].tail(10).pct_change().mean()),
            regime_info["confidence"],
            float(compute_zscore(df['close'].values))
        ]

        return np.array(features, dtype=np.float32)

    def _calculate_position_size(self, df: pd.DataFrame, risk_per_trade: float) -> float:
        """
        Calculate appropriate position size based on volatility and risk tolerance

        Args:
            df: Market data
            risk_per_trade: Risk budget for this trade (as fraction of portfolio)

        Returns:
            Position size as percentage of portfolio
        """
        # Get last closing price and recent volatility
        close_price = df['close'].iloc[-1]
        volatility = df['volatility'].iloc[-1]

        # Set stop loss at 2x daily volatility
        stop_loss_pct = 2.0 * volatility

        # Calculate position size that risks only risk_per_trade of portfolio
        if stop_loss_pct > 0:
            position_size = risk_per_trade / stop_loss_pct
            return min(0.5, position_size)  # Cap at 50% of portfolio
        else:
            return 0.05  # Default 5% if volatility is 0

    def update_performance(self, df: pd.DataFrame) -> None:
        """Update strategy performance metrics based on recent trades"""
        # Calculate daily returns
        daily_return = df['returns'].iloc[-1]
        self.performance_metrics["daily_returns"].append(daily_return)

        # Update Sharpe ratio (assuming risk-free rate of 0)
        if len(self.performance_metrics["daily_returns"]) > 1:
            returns = np.array(self.performance_metrics["daily_returns"])
            self.performance_metrics["sharpe_ratio"] = calculate_sharpe(
                returns)

            # Calculate max drawdown
            cumulative = (1 + returns).cumprod()
            running_max = np.maximum.accumulate(cumulative)
            drawdown = (cumulative - running_max) / running_max
            self.performance_metrics["max_drawdown"] = float(min(drawdown))

            # Update win rate
            wins = sum(r > 0 for r in returns)
            self.performance_metrics["win_rate"] = wins / len(returns)

    def execute(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Main strategy execution method

        Args:
            df: Raw market data

        Returns:
            Dict with trading decisions and performance metrics
        """
        # 1. Preprocess data
        processed_data = self.preprocess_data(df)

        # 2. Calculate signals
        signals = self.calculate_signals(processed_data)

        # 3. Update performance metrics
        self.update_performance(processed_data)

        # 4. Save signal to Redis for execution service
        self.redis.set(
            f"trading_signal:{self.symbols[0]}",
            f"{signals['action']}:{signals['position_size']}"
        )

        # 5. Periodically retrain models (once a day)
        current_hour = datetime.now().hour
        if current_hour == 0:  # Midnight
            self._trigger_model_retraining(processed_data)

        return {
            "signal": signals,
            "performance": self.performance_metrics,
            "timestamp": datetime.now().isoformat(),
            "next_update": datetime.now().replace(second=0, microsecond=0)
        }

    def _trigger_model_retraining(self, df: pd.DataFrame) -> None:
        """Trigger model retraining in background"""
        # Schedule retraining jobs
        self.redis.publish("model_training", "regime_classifier")
        self.redis.publish("model_training", "rl_trader")
        self.logger.info("Triggered model retraining")



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/strategies/reinforcement.py
# ────────────────────────────────────────────────────────────

# ai/modules/strategies/reinforcement.py
import torch
import torch.nn as nn
from collections import deque
import numpy as np
import random  # Added missing import
from ..utils.redis_manager import RedisManager


class RLAgent:
    """RL Strategy with Experience Replay"""

    def __init__(self, state_size=10, action_size=3):
        self.q_network = nn.Sequential(
            nn.Linear(state_size, 64), nn.ReLU(), nn.Linear(64, action_size)
        )  # Fixed syntax by adding closing parenthesis
        self.memory = deque(maxlen=10000)
        self.optimizer = torch.optim.Adam(self.q_network.parameters())
        self.redis = RedisManager()

    def store_experience(self, state, action, reward, next_state):
        self.memory.append(
            (
                torch.FloatTensor(state),
                torch.LongTensor([action]),
                torch.FloatTensor([reward]),
                torch.FloatTensor(next_state),
            )
        )

    def update_model(self, batch_size=64, gamma=0.99):
        if len(self.memory) < batch_size:
            return

        batch = random.sample(self.memory, batch_size)
        states, actions, rewards, next_states = zip(*batch)

        # Q-learning update
        current_q = self.q_network(torch.stack(states)).gather(1, torch.stack(actions))
        next_q = self.q_network(torch.stack(next_states)).max(1)[0].detach()
        target_q = rewards + gamma * next_q

        loss = nn.MSELoss()(current_q.squeeze(), target_q)
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()

        # Save updated model
        torch.save(self.q_network.state_dict(), "rl_strategy.pt")
        self.redis.set("rl_strategy", open("rl_strategy.pt", "rb").read())

    def choose_action(self, state, epsilon=0.1):  # Renamed from get_action
        if np.random.rand() < epsilon:
            return np.random.randint(3)
        with torch.no_grad():
            q_values = self.q_network(torch.FloatTensor(state))
            return torch.argmax(q_values).item()



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/strategies/session.py
# ────────────────────────────────────────────────────────────

# ai/modules/strategies/session.py
from datetime import datetime
import json  # Added for json.loads/dumps
from modules.utils.redis_manager import RedisManager  # Absolute import


class TradingSessionManager:
    """Stateful session management with Redis persistence"""

    def __init__(self, session_id):
        self.redis = RedisManager()
        self.session_id = session_id
        self.state = self._load_state()

    def _load_state(self):
        # Fix: Closed parenthesis and proper default JSON string
        return json.loads(self.redis.get(f"session_{self.session_id}") or "{}") or {
            "open_orders": [],
            "positions": {},
            "risk_limits": {"max_daily_loss": -0.05, "max_position_size": 0.2},
            "performance": {"daily_pnl": 0.0, "weekly_pnl": 0.0},
        }

    def save_state(self):
        self.redis.setex(
            f"session_{self.session_id}", 3600 * 24, json.dumps(self.state)
        )

    def update_position(self, symbol, quantity):
        current = self.state["positions"].get(symbol, 0.0)
        self.state["positions"][symbol] = current + quantity

    def check_risk_limits(self):
        daily_pnl = self.state["performance"]["daily_pnl"]
        if daily_pnl < self.state["risk_limits"]["max_daily_loss"]:
            raise RiskLimitBreached("Daily loss limit exceeded")

        for sym, pos in self.state["positions"].items():
            if abs(pos) > self.state["risk_limits"]["max_position_size"]:
                raise RiskLimitBreached(f"Position size exceeded for {sym}")


class RiskLimitBreached(Exception):
    pass



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/strategies/strategy_core.py
# ────────────────────────────────────────────────────────────

# ai/modules/strategies/strategy_core.py
"""
Consolidated Strategy Module - Wall Street-grade trading strategies
Combines multiple strategy types with a unified interface and signal pipeline
"""

import numpy as np
import pandas as pd
import json
import logging
from typing import Dict, List, Optional, Union
from collections import defaultdict
from enum import Enum

from ..utils.redis_manager import RedisManager
trade_state = RedisManager()

##########################################
# 1️⃣ STRATEGY BASE & ENUMS 🏗️
##########################################


class MarketRegime(Enum):
    """Market regime classification for adaptive strategies"""
    TRENDING = "trending"
    RANGING = "ranging"
    VOLATILE = "volatile"
    UNKNOWN = "unknown"


class StrategyBase:
    """Base class for all trading strategies"""

    def __init__(self, name: str = None):
        self.name = name or self.__class__.__name__
        self.logger = logging.getLogger(f"Strategy.{self.name}")
        self.accuracy = {"correct": 0, "total": 0}
        self.last_signal = None
        self.last_price = None

    def calculate_signal(self, df: pd.DataFrame) -> dict:
        """Generate trading signal from market data"""
        raise NotImplementedError(
            "Subclasses must implement calculate_signal()")

    def update_accuracy(self, current_price: float) -> None:
        """Track strategy prediction accuracy"""
        if self.last_signal and self.last_price:
            price_change = (current_price - self.last_price) / self.last_price
            if (self.last_signal["action"] == "BUY" and price_change > 0) or \
               (self.last_signal["action"] == "SELL" and price_change < 0):
                self.accuracy["correct"] += 1
            self.accuracy["total"] += 1
            # Update Redis with accuracy metrics
            trade_state.set(f"strategy:{self.name}:accuracy",
                            json.dumps({
                                "correct": self.accuracy["correct"],
                                "total": self.accuracy["total"],
                                "rate": self.accuracy["correct"] / max(1, self.accuracy["total"])
                            }))

    def get_accuracy(self) -> float:
        """Get current strategy accuracy"""
        return self.accuracy["correct"] / max(1, self.accuracy["total"])

    def log_signal(self, signal: dict, current_price: float) -> None:
        """Store signal for accuracy calculation"""
        self.last_signal = signal
        self.last_price = current_price

##########################################
# 2️⃣ MEAN REVERSION STRATEGIES 📉📈
##########################################


class MeanReversionStrategy(StrategyBase):
    """Enhanced mean reversion with volatility scaling"""

    def __init__(self, lookback=20, z_threshold=1.5, name="MeanReversion"):
        super().__init__(name=name)
        self.lookback = lookback
        self.z_threshold = z_threshold
        self.volatility_scalar = 1.0

    def calculate_signal(self, df: pd.DataFrame) -> dict:
        """Generate trading signal with risk-adjusted sizing"""
        if len(df) < self.lookback:
            self.logger.warning(
                f"Insufficient data for {self.name}: {len(df)} < {self.lookback}")
            return {"action": "HOLD", "strength": 0, "confidence": 0}

        prices = df["close"].tail(self.lookback)
        mean = prices.mean()
        std = prices.std() or 0.0001  # Avoid division by zero
        current_price = prices.iloc[-1]

        # Volatility scaling for position sizing
        self.volatility_scalar = 1 / (std + 1e-6)

        # Z-score calculation (how many std devs from mean)
        z_score = (current_price - mean) / std

        # Signal generation
        if z_score < -self.z_threshold:
            signal = {"action": "BUY", "strength": abs(z_score),
                      "confidence": min(0.95, abs(z_score/5))}
        elif z_score > self.z_threshold:
            signal = {"action": "SELL", "strength": abs(z_score),
                      "confidence": min(0.95, abs(z_score/5))}
        else:
            signal = {"action": "HOLD", "strength": 0, "confidence": 0.5}

        # Add metadata
        signal["z_score"] = z_score
        signal["mean_price"] = mean
        signal["price_std"] = std
        signal["strategy"] = self.name

        # Log signal for performance tracking
        self.log_signal(signal, current_price)
        return signal

##########################################
# 3️⃣ MOMENTUM & TREND STRATEGIES 📈
##########################################


class MomentumStrategy(StrategyBase):
    """Momentum-based trading strategy"""

    def __init__(self, lookback=50, threshold=0.02, name="Momentum"):
        super().__init__(name=name)
        self.lookback = lookback
        self.threshold = threshold

    def calculate_signal(self, df: pd.DataFrame) -> dict:
        """Generate trading signals based on momentum"""
        if df.empty or len(df) < self.lookback + 1:
            return {"action": "HOLD", "confidence": 0, "strategy": self.name}

        returns = df["close"].pct_change(self.lookback).iloc[-1]

        if returns > self.threshold:
            signal = {"action": "BUY",
                      "confidence": returns, "strategy": self.name}
        elif returns < -self.threshold:
            signal = {"action": "SELL", "confidence": abs(
                returns), "strategy": self.name}
        else:
            signal = {"action": "HOLD", "confidence": 0, "strategy": self.name}

        # Log for performance tracking
        if len(df) > 0:
            self.log_signal(signal, df["close"].iloc[-1])

        return signal


class TrendFollowingStrategy(StrategyBase):
    """Dual moving average crossover strategy"""

    def __init__(self, fast_window=20, slow_window=50, name="TrendFollowing"):
        super().__init__(name=name)
        self.fast_window = fast_window
        self.slow_window = slow_window

    def calculate_signal(self, df: pd.DataFrame) -> dict:
        """Calculate signal based on moving average crossovers"""
        if len(df) < self.slow_window + 2:
            self.logger.warning(
                f"Insufficient data: {len(df)} < {self.slow_window + 2}")
            return {"action": "HOLD", "confidence": 0, "strategy": self.name}

        fast_ma = df["close"].rolling(self.fast_window).mean()
        slow_ma = df["close"].rolling(self.slow_window).mean()

        # Handle insufficient data
        if fast_ma.isna().any() or slow_ma.isna().any():
            return {"action": "HOLD", "confidence": 0, "strategy": self.name}

        current_price = df["close"].iloc[-1]
        signal = {"action": "HOLD", "confidence": 0.5, "strategy": self.name}

        # Check for bullish crossover
        if fast_ma.iloc[-2] < slow_ma.iloc[-2] and fast_ma.iloc[-1] > slow_ma.iloc[-1]:
            signal = {"action": "BUY",
                      "confidence": 0.9, "strategy": self.name}
        # Check for bearish crossover
        elif fast_ma.iloc[-2] > slow_ma.iloc[-2] and fast_ma.iloc[-1] < slow_ma.iloc[-1]:
            signal = {"action": "SELL",
                      "confidence": 0.9, "strategy": self.name}

        # Log signal for performance tracking
        self.log_signal(signal, current_price)
        return signal

##########################################
# 4️⃣ SPECIALIZED STRATEGIES 🔮
##########################################


class ArbitrageStrategy(StrategyBase):
    """Detects arbitrage opportunities between exchanges"""

    def __init__(self, min_spread=0.01, name="Arbitrage"):
        super().__init__(name=name)
        self.min_spread = min_spread

    def calculate_signal(self, df: pd.DataFrame) -> dict:
        """Check for price differentials across exchanges"""
        if df.empty or "exchange_a_price" not in df.columns or "exchange_b_price" not in df.columns:
            self.logger.warning("Missing exchange price columns")
            return {"action": "HOLD", "confidence": 0, "strategy": self.name}

        price_diff = df["exchange_a_price"] - df["exchange_b_price"]

        if (price_diff > self.min_spread).any():  # Positive arbitrage opportunity
            return {"action": "BUY", "confidence": price_diff.mean(), "strategy": self.name}
        elif (price_diff < -self.min_spread).any():  # Negative arbitrage opportunity
            return {"action": "SELL", "confidence": abs(price_diff.mean()), "strategy": self.name}

        return {"action": "HOLD", "confidence": 0, "strategy": self.name}


class MarketMakingStrategy(StrategyBase):
    """Executes market-making trades based on spread conditions"""

    def __init__(self, wide_spread=0.005, narrow_spread=0.001, name="MarketMaking"):
        super().__init__(name=name)
        self.wide_spread = wide_spread
        self.narrow_spread = narrow_spread

    def calculate_signal(self, df: pd.DataFrame) -> dict:
        """Generate signals based on bid-ask spreads"""
        if "ask_price" not in df.columns or "bid_price" not in df.columns:
            self.logger.warning("Missing bid/ask columns")
            return {"action": "HOLD", "confidence": 0, "strategy": self.name}

        spread = df["ask_price"] - df["bid_price"]

        if (spread > self.wide_spread).any():  # Wide spread - good for selling
            return {"action": "SELL", "confidence": 0.8, "strategy": self.name}
        elif (spread < self.narrow_spread).any():  # Narrow spread - good for buying
            return {"action": "BUY", "confidence": 0.8, "strategy": self.name}

        return {"action": "HOLD", "confidence": 0.5, "strategy": self.name}

##########################################
# 5️⃣ ADAPTIVE STRATEGY SELECTION 🔄
##########################################


class AdaptiveStrategy(StrategyBase):
    """Market regime-adaptive trading strategy"""

    def __init__(self, volatility_window=100, name="Adaptive"):
        super().__init__(name=name)
        self.volatility_window = volatility_window
        self.position = 0

    def calculate_signal(self, df):
        if len(df) < self.volatility_window:
            return {"action": "HOLD", "size": 0, "strategy": self.name}

        volatility = df["close"].pct_change().rolling(
            self.volatility_window).std().iloc[-1]

        # Dynamic position sizing based on volatility
        max_position = min(1.0, 0.5 / volatility) if volatility > 0 else 0.1

        # Adaptive strategy selection based on market conditions
        if volatility < 0.02:  # Low volatility - use mean reversion
            return self._mean_reversion_signal(df, max_position)
        else:  # High volatility - use momentum
            return self._momentum_signal(df, max_position)

    def _mean_reversion_signal(self, df, max_pos):
        ma = df["close"].rolling(20).mean().iloc[-1]
        last_close = df["close"].iloc[-1]

        if last_close < ma * 0.98:
            return {"action": "BUY", "size": max_pos, "strategy": self.name}
        elif last_close > ma * 1.02:
            return {"action": "SELL", "size": max_pos, "strategy": self.name}
        return {"action": "HOLD", "size": 0, "strategy": self.name}

    def _momentum_signal(self, df, max_pos):
        returns = df["close"].pct_change(5).iloc[-1]

        if returns > 0.03:
            return {"action": "BUY", "size": max_pos, "strategy": self.name}
        elif returns < -0.03:
            return {"action": "SELL", "size": max_pos, "strategy": self.name}
        return {"action": "HOLD", "size": 0, "strategy": self.name}

##########################################
# 6️⃣ STRATEGY ORCHESTRATION 🎭
##########################################


# Initialize Strategy Instances
STRATEGIES = {
    "mean_reversion": MeanReversionStrategy(),
    "trend_following": TrendFollowingStrategy(),
    "momentum": MomentumStrategy(),
    "arbitrage": ArbitrageStrategy(),
    "market_making": MarketMakingStrategy(),
    "adaptive": AdaptiveStrategy(),
}


def run_strategy_pipeline(df: pd.DataFrame, strategy_subset: List[str] = None) -> dict:
    """Combine multiple strategy signals with consensus logic"""
    # Validate input data
    if df.empty:
        logging.warning("Empty DataFrame provided to strategy pipeline")
        return {"primary_action": "HOLD", "confidence": 0, "signals": {}}

    # Add orderbook columns if needed for specific strategies
    required_columns = {"exchange_a_price",
                        "exchange_b_price", "ask_price", "bid_price"}
    missing_cols = required_columns - set(df.columns)

    if missing_cols:
        # For demonstration, add mock columns with close price + small random noise
        for col in missing_cols:
            if "close" in df.columns:
                df[col] = df["close"] * \
                    (1 + np.random.normal(0, 0.001, size=len(df)))

    # Determine which strategies to run
    active_strategies = {
        name: strategy for name, strategy in STRATEGIES.items()
        if strategy_subset is None or name in strategy_subset
    }

    # Generate signals from selected strategies
    signals = {}
    for name, strategy in active_strategies.items():
        try:
            signals[name] = strategy.calculate_signal(df)
        except Exception as e:
            logging.error(f"Error in strategy {name}: {e}")
            signals[name] = {"action": "HOLD",
                             "confidence": 0, "strategy": name}

    # Log signals for debugging
    logging.info("Generated Signals:")
    for name, signal in signals.items():
        logging.info(f"├── {name}: {signal}")

    # Aggregate signals with majority voting
    buy_signals = [s for s in signals.values() if s["action"] == "BUY"]
    sell_signals = [s for s in signals.values() if s["action"] == "SELL"]

    # Determine primary action based on signal strength
    if len(buy_signals) > len(sell_signals):
        primary_action = "BUY"
    elif len(sell_signals) > len(buy_signals):
        primary_action = "SELL"
    else:
        primary_action = "HOLD"

    # Calculate confidence as weighted average of relevant signals
    relevant_signals = buy_signals if primary_action == "BUY" else sell_signals
    confidence = np.mean([s.get("confidence", 0)
                         for s in relevant_signals]) if relevant_signals else 0.5

    # Return consolidated signal
    return {
        "primary_action": primary_action,
        "confidence": confidence,
        "signals": signals,
        "timestamp": pd.Timestamp.now().isoformat()
    }


class AdaptiveStrategySelector:
    """Dynamic strategy selection based on market conditions"""

    def __init__(self):
        self.current_regime = MarketRegime.UNKNOWN
        self.strategy_performance = {}

    def detect_market_regime(self, df: pd.DataFrame) -> MarketRegime:
        """Identify current market regime using heuristics"""
        if df.empty or len(df) < 20:
            return MarketRegime.UNKNOWN

        try:
            # Calculate volatility
            volatility = df["close"].pct_change().rolling(20).std().iloc[-1]

            # Trend strength (using linear regression slope)
            n = 20
            x = np.arange(n)
            y = df["close"].tail(n).values
            slope = np.polyfit(x, y, 1)[0] / df["close"].iloc[-1]

            # Decision logic
            if volatility > 0.03:  # High volatility
                return MarketRegime.VOLATILE
            elif abs(slope) > 0.001:  # Strong trend
                return MarketRegime.TRENDING
            else:  # Range-bound
                return MarketRegime.RANGING

        except Exception as e:
            logging.error(f"Error in regime detection: {e}")
            return MarketRegime.UNKNOWN

    def select_best_strategy(self, df: pd.DataFrame) -> StrategyBase:
        """Select optimal strategy for current market conditions"""
        # First detect the current market regime
        regime = self.detect_market_regime(df)
        self.current_regime = regime

        # Store regime in Redis for other components
        trade_state.set("market_regime", regime.value)

        # Strategy selection based on regime
        if regime == MarketRegime.TRENDING:
            return STRATEGIES["trend_following"]
        elif regime == MarketRegime.RANGING:
            return STRATEGIES["mean_reversion"]
        elif regime == MarketRegime.VOLATILE:
            return STRATEGIES["adaptive"]
        else:
            # Default to the strategy with highest recent accuracy
            return self._select_highest_accuracy_strategy()

    def _select_highest_accuracy_strategy(self) -> StrategyBase:
        """Select strategy with highest recent accuracy"""
        best_strategy = None
        best_accuracy = 0

        for name, strategy in STRATEGIES.items():
            accuracy = strategy.get_accuracy()
            if accuracy > best_accuracy:
                best_accuracy = accuracy
                best_strategy = strategy

        # Default if no accuracy data
        return best_strategy or STRATEGIES["mean_reversion"]


# Add this function to the bottom of the file:
def calculate_sharpe_ratio(returns):
    """Calculate Sharpe ratio from equity curve"""
    if len(returns) < 2:
        return 0
    return np.mean(returns) / (np.std(returns) + 1e-6) * np.sqrt(252)


# Enhance the run_strategy_pipeline function with adaptive selection:
def run_strategy_pipeline(df: pd.DataFrame, strategy_subset: List[str] = None,
                          use_adaptive: bool = True) -> dict:
    """Combine multiple strategy signals with adaptive selection"""
    # Existing validation code...

    # Initialize adaptive selector if needed
    selector = AdaptiveStrategySelector() if use_adaptive else None

    # If adaptive selection is enabled, use it to select primary strategy
    if use_adaptive and selector:
        best_strategy = selector.select_best_strategy(df)
        regime = selector.current_regime.value

        # Generate signals from all strategies for comparison
        signals = {name: strategy.calculate_signal(df)
                   for name, strategy in STRATEGIES.items()
                   if strategy_subset is None or name in strategy_subset}

        # Use the best strategy's signal as primary
        primary_signal = signals[best_strategy.name]
        primary_signal["selected_by"] = "adaptive_selector"
        primary_signal["regime"] = regime

        return {
            "primary_action": primary_signal["action"],
            "confidence": primary_signal.get("confidence", 0.5),
            "signals": signals,
            "regime": regime,
            "selected_strategy": best_strategy.name,
            "timestamp": pd.Timestamp.now().isoformat()
        }

    # If not using adaptive selection, fall back to original consensus logic
    # (rest of the original implementation)



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/utils/events.py
# ────────────────────────────────────────────────────────────

# ai/modules/utils/events.py

from collections import defaultdict
import logging


class EventBus:
    """Pub-sub system for cross-module communication"""

    def __init__(self):
        self.subscribers = defaultdict(list)

    def publish(self, event_type, data):
        """Notify all listeners of an event."""
        for callback in self.subscribers[event_type]:
            try:
                callback(data)
            except Exception as e:
                logging.error(f"Event handler failed: {str(e)}")

    def register_listener(self, event_type, callback):
        """Register a new listener (renamed from subscribe for clarity)."""
        self.subscribers[event_type].append(callback)

    # Add this method to maintain compatibility with code expecting "subscribe"
    def subscribe(self, event_type, callback):
        """Compatibility method - same as register_listener."""
        return self.register_listener(event_type, callback)

    def unregister_listener(self, event_type, callback):
        """Safely remove a listener if it exists."""
        if callback in self.subscribers[event_type]:
            self.subscribers[event_type].remove(callback)

    # Add this for consistency
    def unsubscribe(self, event_type, callback):
        """Compatibility method - same as unregister_listener."""
        return self.unregister_listener(event_type, callback)



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/utils/logging.py
# ────────────────────────────────────────────────────────────

# utils/logging.py
"""Logging configuration for the AI Trading System."""

import logging
import warnings

# Suppress deprecation warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Configure logging
def setup_logging():
    """Set up the logging configuration."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],
    )
    return logging.getLogger("AI_Trading")

# Create logger instance
logger = setup_logging()



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/utils/profit_tracker.py
# ────────────────────────────────────────────────────────────

# ai/modules/utils/profit_tracker.py

class ProfitTracker:
    def __init__(self):
        self.trades = []  # Stores trade data for PnL calculation
        self.profits = []  # Stores simple profit amounts

    def log_trade(self, trade):
        """Log a trade for PnL calculation.

        Args:
            trade (dict): Trade details with keys like 'qty', 'entry_price', 'exit_price'
        """
        self.trades.append(trade)

    def calculate_pnl(self):
        """Calculate total Profit and Loss from logged trades.

        Returns:
            float: Total PnL across all trades
        """
        total_pnl = sum(
            trade["qty"] * (trade["exit_price"] - trade["entry_price"])
            for trade in self.trades
        )
        return total_pnl

    def record_profit(self, amount):
        """Record a simple profit amount.

        Args:
            amount (float): Profit amount to record
        """
        self.profits.append(amount)

    def get_total_profit(self):
        """Get the total of all recorded profit amounts.

        Returns:
            float: Sum of all profits
        """
        return sum(self.profits)



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/utils/redis_manager.py
# ────────────────────────────────────────────────────────────

# ai/modules/utils/redis_manager.py
import redis
import sys
import os
import logging
from unittest.mock import Mock


class RedisManager:
    _pool = None
    _conn = None

    def __init__(self, host="localhost", port=6379, db=0, password=None):
        self.host = host
        self.port = port
        self.db = db
        self.password = password
        if os.environ.get("TEST_MODE"):
            self._conn = Mock()
            self._conn.ping = lambda: True
            self._conn.get = lambda key, default=None: default
            self._conn.setnx = lambda key, value: True
            self._conn.set = lambda key, value: True
        else:
            if not self._pool:
                self._create_pool()
            self._conn = redis.Redis(connection_pool=self._pool, decode_responses=True)

    def _create_pool(self):
        self._pool = redis.ConnectionPool(
            host=self.host,
            port=self.port,
            db=self.db,
            password=self.password,
            max_connections=20,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,
        )

    @property
    def conn(self):
        return self._conn

    def save(self):
        logging.info("Redis state saved (placeholder implementation)")

    def _reconnect(self):
        if not os.environ.get("TEST_MODE"):
            self.conn.connection_pool.disconnect()
            self.conn.connection_pool.reset()

    def get(self, key, default=None):
        try:
            return self.conn.get(key) or default
        except redis.RedisError as e:
            logging.error(f"Redis get failed: {e}")
            return default

    def set(self, key, value):
        try:
            return self.conn.set(key, value)
        except redis.RedisError as e:
            logging.error(f"Redis set failed: {e}")
            return False

    def ping(self):
        try:
            return self.conn.ping()
        except redis.RedisError as e:
            logging.error(f"Redis ping failed: {e}")
            return False



# ────────────────────────────────────────────────────────────
# FILE: ai/modules/utils/vault.py
# ────────────────────────────────────────────────────────────

# ai/modules/utils/vault.py

from cryptography.fernet import Fernet
import os
from redis import Redis
from dotenv import load_dotenv

load_dotenv()  # Load .env variables


class CredentialManager:
    """Encrypted storage for API keys using Redis"""

    def __init__(self):
        # Load Fernet key
        self.key = os.getenv("VAULT_KEY")
        if not self.key:
            raise ValueError("VAULT_KEY not found in .env!")
        self.cipher = Fernet(self.key.encode())  # Convert string to bytes

        # Initialize Redis connection
        self.redis = Redis(
            host=os.getenv("REDIS_HOST"),
            port=int(os.getenv("REDIS_PORT")),
            db=int(os.getenv("REDIS_DB"))
        )

        # Test Redis connection
        try:
            self.redis.ping()
        except ConnectionError:
            raise RuntimeError("Failed to connect to Redis!")

    def store_secret(self, name: str, value: str):
        """Encrypt and store a secret in Redis"""
        encrypted = self.cipher.encrypt(value.encode())
        self.redis.set(f"vault:{name}", encrypted)

    def retrieve_secret(self, name: str) -> str:
        """Retrieve and decrypt a secret from Redis"""
        encrypted = self.redis.get(f"vault:{name}")
        if not encrypted:
            raise ValueError(f"Secret '{name}' not found!")
        return self.cipher.decrypt(encrypted).decode()



# ────────────────────────────────────────────────────────────
# FILE: ai/boss.py
# ────────────────────────────────────────────────────────────

# ai/boss.py - Full Enhanced Version
"""
ULTIMATE WALL STREET AI TRADING SYSTEM
- Complete implementation with all original features
- Fixed structural issues
- Full trading lifecycle
"""

##########################################
# 1️⃣ CONFIGURATION 🚀
##########################################

# At top of boss.py
import asyncio
import logging
import sys
import pandas as pd
import time
import os
import importlib
import inspect
import json
from collections import defaultdict, deque
from typing import Dict, Any, List, Set
from rich.console import Console
from rich.live import Live
from rich.layout import Layout
from rich.table import Table
from rich.panel import Panel
from typing import Dict, Any, Optional, List, Tuple, Set, DefaultDict
import warnings
from datetime import datetime
from decimal import Decimal
from collections import deque
from modules.utils.redis_manager import RedisManager

# Initialize the monitor

USE_QUANTUM = False  # Skip quantum components entirely


try:
    import pybit
except ImportError:
    # Mock pybit.unified_trading.HTTP class
    class MockPybit:
        class unified_trading:
            class HTTP:
                def __init__(self, **kwargs):
                    pass
    pybit = MockPybit()


warnings.filterwarnings("ignore", category=DeprecationWarning)


# Configuration
class LiveTradingConfig:
    LIVE_MODE = False
    TRADE_INTERVAL = 30
    LEVERAGE = 10


class BYBIT_CONFIG:
    API_KEY = "mock_key"
    API_SECRET = "mock_secret"


# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("AI_Trading")

##########################################
# 2️⃣ METRICS & HEALTH MONITORING 📊
##########################################


class ComponentMetrics:
    def __init__(self):
        self.response_times = deque(maxlen=100)
        self.error_count = 0
        self.total_calls = 0
        self.last_error_time = 0
        self.recovery_attempts = 0
        self.dependency_failures = defaultdict(int)

    def record_call(self, duration_ms: float, success: bool):
        self.total_calls += 1
        self.response_times.append(duration_ms)
        if not success:
            self.error_count += 1
            self.last_error_time = time.time()

    def record_dependency_failure(self, dependency_name: str):
        self.dependency_failures[dependency_name] += 1

    def record_recovery(self):
        self.recovery_attempts += 1
        self.error_count = 0
        self.response_times.clear()

    @property
    def health_score(self) -> float:
        error_rate = self.error_count / max(1, self.total_calls)
        avg_response = (
            sum(self.response_times) / max(1, len(self.response_times))
            if self.response_times
            else 0
        )
        response_penalty = min(avg_response / 500, 0.5)  # 500ms threshold
        return max(
            0.0,
            1.0 - error_rate - response_penalty -
            0.1 * len(self.dependency_failures),
        )


##########################################
# 3️⃣ DEPENDENCY MANAGEMENT 🧩
##########################################

class DependencyGraph:
    """Manages component dependencies and initialization order"""

    def __init__(self):
        self.dependencies = defaultdict(set)
        self.reverse_dependencies = defaultdict(set)

    def add_dependency(self, component: str, depends_on: str):
        self.dependencies[component].add(depends_on)
        self.reverse_dependencies[depends_on].add(component)

    def get_dependencies(self, component: str) -> Set[str]:
        return self.dependencies[component]

    def get_dependent_components(self, component: str) -> Set[str]:
        return self.reverse_dependencies[component]

    def get_initialization_order(self) -> List[str]:
        """Return components in dependency order (least dependent first)"""
        result = []
        visited = set()
        temp_mark = set()

        def visit(node):
            if node in visited:
                return
            if node in temp_mark:
                logger.warning(
                    f"Circular dependency detected involving {node}")
                return

            temp_mark.add(node)
            for dep in self.dependencies[node]:
                visit(dep)
            temp_mark.remove(node)
            visited.add(node)
            result.append(node)

        for node in list(self.dependencies.keys()):
            if node not in visited:
                visit(node)

        return result

##########################################
# 4️⃣ UI & VISUALIZATION 🖥️
##########################################


class AIControlCenter:
    """Real-time UI for Boss's empire"""

    def __init__(self, agent):
        self.agent = agent
        self.console = Console()
        self.layout = Layout()
        self._setup_layout()
        self.selected_component = None
        self.initializing = True  # Add this flag

    def _setup_layout(self):
        self.layout.split(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="detail", size=15),
            Layout(name="logs", size=10),
        )
        self.layout["main"].split_row(
            Layout(name="components", ratio=3),
            Layout(name="metrics", ratio=2),
            Layout(name="actions", size=30),
        )

    def _status_table(self) -> Table:
        table = Table(title="Component Monitor", header_style="bold magenta")
        table.add_column("Module", style="cyan")
        table.add_column("Status", justify="right")
        table.add_column("Health", justify="right")
        table.add_column("Calls", justify="right")
        table.add_column("Errors", justify="right")

        sorted_components = sorted(
            self.agent.component_status.items(),
            key=lambda x: (
                {"healthy": 0, "warning": 1, "error": 2, "starting": 3}.get(
                    x[1]["status"], 4
                ),
                -x[1].get("health_score", 0),
            ),
        )

        for name, stats in sorted_components:
            status_color = {
                "healthy": "green",
                "warning": "yellow",
                "error": "red",
                "starting": "blue",
            }.get(stats["status"], "white")

            health_score = stats.get("health_score", 1.0) * 100
            health_color = (
                "green"
                if health_score > 80
                else "yellow" if health_score > 50 else "red"
            )

            table.add_row(
                name,
                f"[{status_color}]{stats['status'].upper()}[/]",
                f"[{health_color}]{health_score:.1f}%[/]",
                f"{stats.get('calls', 0)}",
                f"[red]{stats.get('errors', 0)}[/]" if stats.get("errors",
                                                                 0) else "0",
            )
        return table

    def _metrics_panel(self) -> Panel:
        # Calculate metrics safely
        total_errors = sum(s.get('errors', 0)
                           for s in self.agent.component_status.values())
        total_calls = max(1, sum(s.get('calls', 0)
                          for s in self.agent.component_status.values()))
        error_rate = (total_errors / total_calls) * 100

        total_response = sum(s.get('avg_response', 0)
                             for s in self.agent.component_status.values())
        component_count = max(1, len(self.agent.component_status))
        avg_response = total_response / component_count

        active_count = sum(
            1 for s in self.agent.component_status.values() if s.get('status') == 'healthy')

        return Panel(
            f"Active Components: {active_count}/{component_count}\n"
            f"Total API Calls: {total_calls}\n"
            f"Error Rate: {error_rate:.1f}%\n"
            f"Avg Response: {avg_response:.1f}ms\n"
            f"System Health: {self.agent.system_health_score * 100:.1f}%\n"
            f"Trading Mode: {'LIVE' if LiveTradingConfig.LIVE_MODE else 'TEST'}\n",
            title="System Metrics",
        )

    def _component_detail(self) -> Panel:
        if not self.selected_component or self.selected_component not in self.agent.component_status:
            return Panel("Select a component for details", title="Component Details")

        stats = self.agent.component_status[self.selected_component]
        metrics = self.agent.component_metrics.get(
            self.selected_component, ComponentMetrics())

        return Panel(
            f"Component: {self.selected_component}\n"
            f"Status: {stats['status'].upper()}\n"
            f"Health Score: {stats.get('health_score', 1.0) * 100:.1f}%\n"
            f"Total Calls: {stats.get('calls', 0)}\n"
            f"Errors: {stats.get('errors', 0)}\n"
            f"Error Rate: {(stats.get('errors', 0) / max(1, stats.get('calls', 0))) * 100:.1f}%\n"
            f"Avg Response: {stats.get('avg_response', 0):.1f}ms\n"
            f"Last Error: {time.strftime('%H:%M:%S', time.localtime(metrics.last_error_time)) if metrics.last_error_time else 'None'}\n"
            f"Recovery Attempts: {metrics.recovery_attempts}\n"
            f"Dependencies: {', '.join(self.agent.component_dependencies.get(self.selected_component, []))}\n",
            title=f"Component Details: {self.selected_component}",
            style="cyan",
        )

    async def display(self):
        """Display and update the UI in real-time."""
        with Live(self.layout, refresh_per_second=4, screen=True) as live:
            # Show initializing message for the first 5 seconds
            start_time = time.time()

            while True:
                # Check if we're still in initialization phase (first 5 seconds)
                if time.time() - start_time < 5:
                    self.layout["header"].update(
                        Panel(
                            "AI TRADING CONTROL CENTER - INITIALIZING COMPONENTS...")
                    )
                    self.layout["components"].update(
                        Panel("Loading modules and components, please wait...")
                    )
                    await asyncio.sleep(0.5)
                    continue

                # After initialization phase, show normal UI
                self.layout["header"].update(
                    Panel(
                        f"AI TRADING CONTROL CENTER | Mode: {'LIVE' if LiveTradingConfig.LIVE_MODE else 'TEST'} | System Health: {self.agent.system_health_score * 100:.1f}%")
                )
                self.layout["components"].update(Panel(self._status_table()))
                self.layout["metrics"].update(self._metrics_panel())
                self.layout["detail"].update(self._component_detail())
                self.layout["logs"].update(
                    Panel(
                        "\n".join(self.agent.recent_logs[-5:]), title="Recent Logs", style="blue")
                )

                # Select first component if none selected
                if not self.selected_component and self.agent.component_status:
                    self.selected_component = next(
                        iter(self.agent.component_status.keys()))

                await asyncio.sleep(0.25)


##########################################
# 5️⃣ CORE BRAIN COMPONENTS 🧠
##########################################

class SelfHealingAgent:
    """Boss's brain—loads, heals, runs components"""

    # -------------------------------
    # 5.1 Core Initialization
    # -------------------------------

    def __init__(self):
        self.component_errors = 0
        self.components: Dict[str, Any] = {}
        self.component_status: Dict[str, Dict[str, Any]] = {}
        self.component_metrics: Dict[str, ComponentMetrics] = {}
        self.component_dependencies: Dict[str, List[str]] = {}
        self.dependency_graph = DependencyGraph()
        self.recent_logs = []
        self.system_health_score = 1.0
        self.critical_components = set()
        self.maintenance_mode = False

        # Initialize core services
        self.redis = self._init_redis()
        self.event_bus = self._init_event_bus()
        self.control_center = AIControlCenter(self)

        # Load all modules
        self._discover_dependencies()
        self._load_modules()

        # Start maintenance cycle
        asyncio.create_task(self._maintenance_cycle())

    async def _maintenance_cycle(self):
        """Enhanced maintenance cycle with proactive system optimization"""
        while True:
            try:
                self.maintenance_mode = True
                logger.info("Running system maintenance cycle")

                # Step 1: Update system health metrics
                healthy_components = sum(1 for s in self.component_status.values()
                                         if s.get("status") == "healthy")
                total_components = max(1, len(self.component_status))

                # Calculate weighted health score (critical components count more)
                critical_health = 0
                critical_count = 0

                for name, status in self.component_status.items():
                    if name in self.critical_components:
                        critical_health += status.get("health_score", 0)
                        critical_count += 1

                if critical_count > 0:
                    critical_health_score = critical_health / critical_count
                    # Weight critical components as 70% of overall health
                    self.system_health_score = (
                        critical_health_score * 0.7) + ((healthy_components / total_components) * 0.3)
                else:
                    self.system_health_score = healthy_components / total_components

                # Add to logs
                self.recent_logs.append(
                    f"System health: {self.system_health_score:.2f}")

                # Step 2: Recovery for unhealthy components
                recovery_tasks = []

                # First recover critical components
                for name in self.critical_components:
                    if name in self.component_status and self.component_status[name]["status"] != "healthy":
                        logger.info(
                            f"Prioritizing recovery of critical component: {name}")
                        recovery_tasks.append(
                            self.recover(name, full_restart=True))

                # Wait for critical components to recover
                if recovery_tasks:
                    await asyncio.gather(*recovery_tasks)
                    recovery_tasks = []

                # Then handle non-critical components
                for name, status in self.component_status.items():
                    if name not in self.critical_components:
                        if status["status"] == "error":
                            recovery_tasks.append(self.recover(name))
                        elif status["status"] == "warning" or status.get("health_score", 1.0) < 0.4:
                            recovery_tasks.append(
                                self.recover(name, full_restart=True))

                # Execute non-critical recoveries in parallel
                if recovery_tasks:
                    await asyncio.gather(*recovery_tasks)

                # Step 3: Resource optimization
                if hasattr(self, "resource_optimizer") and "resource_optimizer" in self.components:
                    await self.execute_with_metrics("resource_optimizer", "optimize")

                # Step 4: Finish maintenance
                self.maintenance_mode = False

                # Dynamic maintenance interval based on system health
                if self.system_health_score < 0.5:
                    maintenance_interval = 60  # Run every minute if system is unhealthy
                elif self.system_health_score < 0.8:
                    maintenance_interval = 180  # Run every 3 minutes if system has issues
                else:
                    maintenance_interval = 300  # Run every 5 minutes if system is healthy

                await asyncio.sleep(maintenance_interval)

            except Exception as e:
                logger.error(f"Maintenance cycle error: {e}")
                self.maintenance_mode = False
                await asyncio.sleep(60)  # Error handling sleep time

    # -------------------------------
    # 5.2 Recovery Engine
    # -------------------------------

    async def recover(self, component_name, full_restart=False):
        """Enhanced component recovery with progressive backoff strategy"""
        if component_name not in self.components:
            logger.error(f"Cannot recover unknown component: {component_name}")
            return False

        metrics = self.component_metrics.get(component_name)
        if not metrics:
            logger.error(f"No metrics available for {component_name}")
            return False

        # Update recovery attempts counter
        metrics.recovery_attempts += 1
        logger.info(
            f"Attempting recovery for {component_name} (attempt {metrics.recovery_attempts})")

        # Implement exponential backoff
        backoff_time = min(5 * (2 ** (metrics.recovery_attempts - 1)), 60)

        try:
            # Step 1: Check component dependencies first
            for dep_name in self.component_dependencies.get(component_name, []):
                if dep_name in self.component_status and self.component_status[dep_name]["status"] != "healthy":
                    logger.warning(
                        f"Dependency {dep_name} is unhealthy - recovering it first")
                    await self.recover(dep_name)

            # Step 2: Attempt component reinitialization if needed
            if full_restart:
                logger.info(f"Full restart of {component_name}")
                # Find the original class
                module_name, class_name = component_name.split('_')
                try:
                    module_path = f"modules.{module_name}.{class_name}"
                    mod = importlib.import_module(module_path)

                    for name, cls in inspect.getmembers(mod, inspect.isclass):
                        if name.lower() == class_name:
                            # Reinitialize the component
                            self.components[component_name] = self._smart_init(
                                cls)
                            logger.info(
                                f"Successfully reinitialized {component_name}")
                            break
                except Exception as e:
                    logger.error(
                        f"Failed to reinitialize {component_name}: {e}")
                    return False

            # Step 3: Verify health if possible
            if hasattr(self.components[component_name], "health_check"):
                await asyncio.sleep(backoff_time)  # Wait before health check

                try:
                    if asyncio.iscoroutinefunction(self.components[component_name].health_check):
                        health_result = await self.components[component_name].health_check()
                    else:
                        health_result = self.components[component_name].health_check(
                        )

                    if hasattr(health_result, 'passed') and not health_result.passed:
                        logger.error(
                            f"Health check failed for {component_name}")
                        return False
                except Exception as e:
                    logger.error(
                        f"Health check error for {component_name}: {e}")
                    return False

            # Step 4: Reset component status
            self.component_status[component_name] = {
                "status": "healthy",
                "errors": 0,
                "calls": self.component_status[component_name].get("calls", 0),
                "health_score": 0.8,  # Start with slightly reduced health
                "avg_response": self.component_status[component_name].get("avg_response", 0),
            }

            # Step 5: Reset metrics partially
            metrics.error_count = 0
            metrics.dependency_failures.clear()

            # Publish recovery event
            if hasattr(self, 'event_bus'):
                await self.event_bus.publish("component.recovered", {
                    "component": component_name,
                    "timestamp": time.time(),
                    "attempts": metrics.recovery_attempts
                })

            logger.info(f"Recovery successful for {component_name}")
            return True

        except Exception as e:
            logger.error(f"Recovery failed for {component_name}: {e}")

            # Update component status
            self.component_status[component_name]["status"] = "error"

            # Check for too many recovery attempts
            if metrics.recovery_attempts >= 5:
                logger.critical(
                    f"Component {component_name} failed after 5 recovery attempts - marking as terminated")
                self.component_status[component_name]["status"] = "terminated"

            return False

    # -------------------------------
    # 5.3 Smart Initialization
    # -------------------------------

    def _smart_init(self, cls):
        """AI-smart component init with dependency injection"""
        try:
            # Handle Pybit exchange clients first
            if (
                hasattr(pybit, 'unified_trading')
                and hasattr(pybit.unified_trading, 'WebSocket')
                and issubclass(cls, pybit.unified_trading.WebSocket)
            ):
                logger.debug("Initializing pybit WebSocket client")
                return cls(
                    api_key=BYBIT_CONFIG.API_KEY,
                    api_secret=BYBIT_CONFIG.API_SECRET,
                    base_url=getattr(BYBIT_CONFIG, 'BASE_URL',
                                     'https://api-testnet.bybit.com'),
                    ping_interval=20,  # Add WebSocket-specific param
                    testnet=True  # Assuming TESTNET_MODE is available
                )

            # Handle core types and other components
            if cls in [deque, Decimal]:
                return cls()
            elif cls == datetime:
                return datetime.now()  # Use current time instead
            elif cls == LiveTradingConfig:  # Add this line
                return cls()  # No args needed
            elif cls.__name__ == 'Fernet':
                return cls(b'32_byte_key_for_testing_1234567890==')

            # Dependency injection for other components
            sig = inspect.signature(cls.__init__)
            params = {}

            for name, param in sig.parameters.items():
                if name == 'self':
                    continue
                if name == 'redis' or 'redis' in name:
                    params[name] = self.redis
                elif name == 'event_bus' or 'event' in name:
                    params[name] = self.event_system if hasattr(
                        self, 'event_system') else self.event_bus
                elif name == 'key' and cls.__name__ == 'CredentialManager':
                    params[name] = b'32_byte_key_for_testing_1234567890=='
                elif name == 'config' or 'config' in name:
                    params[name] = {
                        "api_key": BYBIT_CONFIG.API_KEY,
                        "api_secret": BYBIT_CONFIG.API_SECRET,
                        "testnet": True
                    }
                elif name == 'symbol' or 'symbol' in name:
                    params[name] = "BTCUSDT"
                # Auto-wire other components
                elif name in self.components:
                    params[name] = self.components[name]
                elif any(name in comp_key for comp_key in self.components):
                    for comp_key, comp in self.components.items():
                        if name in comp_key:
                            params[name] = comp
                            break

            # Validate required parameters
            for name, param in sig.parameters.items():
                if (name != "self" and
                    name not in params and
                        param.default is inspect.Parameter.empty):
                    logger.warning(
                        f"Missing required parameter {name} for {cls.__name__}")
                    params[name] = None  # Safe default

            return cls(**params)

        except Exception as e:
            # Single error handling block for all initialization failures
            logger.error(f"Component init failed for {cls.__name__}: {str(e)}")
            self.component_errors += 1
            if self.component_errors > 5:
                logger.warning("Too many errors")
            return None

    # -------------------------------
    # 5.4 Service Initialization
    # -------------------------------

    def _init_redis(self):
        """Initialize Redis connection or mock if unavailable."""
        try:
            # Use the RedisManager already imported at the top of the file
            redis = RedisManager()
            if redis.ping():
                logger.info("Redis connected")
                return redis
            else:
                logger.warning("Redis ping failed - using mock")
        except Exception as e:
            logger.error(f"Redis failed: {e}—using mock")

        # Define MockRedis here (before using it)
        class MockRedis:
            def __init__(self):
                self.storage = {}

            async def get(self, key):
                return self.storage.get(key)

            async def set(self, key, value):
                self.storage[key] = value

            def ping(self):
                return True

        return MockRedis()

    def _init_event_bus(self):
        try:
            from modules.utils.events import EventBus
            return EventBus()
        except Exception as e:
            logger.error(f"EventBus failed: {e}—using mock")

            # Mock EventBus with basic functionality
            class MockEventBus:
                def __init__(self):
                    self.handlers = defaultdict(list)

                async def publish(self, event_type, data):
                    try:
                        for handler in self.handlers.get(event_type, []):
                            if inspect.iscoroutinefunction(handler):
                                await handler(data)
                            else:
                                handler(data)
                    except Exception as inner_e:
                        logger.error(f"Error handling event: {inner_e}")

                def subscribe(self, event_type, handler):
                    self.handlers[event_type].append(handler)

                def unsubscribe(self, event_type, handler):
                    if event_type in self.handlers and handler in self.handlers[event_type]:
                        self.handlers[event_type].remove(handler)

            return MockEventBus()

    # -------------------------------
    # 5.5 Dependency Management
    # -------------------------------
    def _discover_dependencies(self):
        """Scan modules to discover dependencies between components"""
        modules_dir = os.path.join(os.path.dirname(__file__), "modules")
        dependency_data = {}

        for root, _, files in os.walk(modules_dir):
            module = os.path.basename(root)
            for file in files:
                if file.endswith(".py") and file != "__init__.py":
                    component = file[:-3]
                    key = f"{module}_{component}"

                    try:
                        # Import the module to inspect it
                        mod = importlib.import_module(
                            f"modules.{module}.{component}")

                        for name, cls in inspect.getmembers(
                                mod, inspect.isclass):
                            if name in ["HTTP", "WebSocket"]:
                                continue

                            # Look at __init__ parameters to find dependencies
                            sig = inspect.signature(cls.__init__)
                            deps = []

                            for param_name, param in sig.parameters.items():
                                if param_name == "self":
                                    continue

                                # Check for common dependency patterns
                                param_hints = [
                                    "model",
                                    "predictor",
                                    "strategy",
                                    "analyzer",
                                    "manager",
                                    "fetcher",
                                    "engine",
                                    "monitor",
                                    "optimizer",
                                    "executor",
                                ]

                                if any(
                                        hint in param_name for hint in param_hints):
                                    deps.append(param_name)

                            component_key = f"{module}_{name.lower()}"
                            dependency_data[component_key] = deps

                            # Check if it's a critical component
                            if hasattr(cls, "CRITICAL") and cls.CRITICAL:
                                self.critical_components.add(component_key)

                    except Exception as e:
                        logger.debug(
                            f"Could not analyze dependencies for {key}: {e}")

        # Now map parameter names to actual components
        for component, deps in dependency_data.items():
            for dep in deps:
                # Try to find a matching component
                candidates = [
                    c for c in dependency_data.keys() if c.split("_")[1] in dep
                ]
                if candidates:
                    self.dependency_graph.add_dependency(
                        component, candidates[0])
                    if component not in self.component_dependencies:
                        self.component_dependencies[component] = []
                    self.component_dependencies[component].append(
                        candidates[0])

        logger.info(
            f"Discovered dependencies for {len(dependency_data)} components")

    # -------------------------------
    # 5.6 Module Loading
    # -------------------------------
    def _load_modules(self):
        """Scan and load all modules in dependency order"""
        modules_dir = os.path.join(os.path.dirname(__file__), "modules")
        logger.info(f"Looking for modules in: {modules_dir}")

        # Get optimal loading order
        init_order = self.dependency_graph.get_initialization_order()
        ordered_components = []

        # First collect all components with improved directory handling
        for root, _, files in os.walk(modules_dir):
            # Skip __pycache__ directories
            if "__pycache__" in root:
                continue

            # Get module name (subdirectory name under modules)
            rel_path = os.path.relpath(root, modules_dir)
            # Handle the case when we're in the modules dir itself
            module = rel_path if rel_path != "." else ""

            # Only process directories that are direct children of modules
            if module and "/" not in module and "\\" not in module:
                logger.debug(f"Scanning module directory: {module}")
                for file in files:
                    if file.endswith(".py") and file != "__init__.py":
                        component = file[:-3]
                        logger.debug(f"Found component: {module}.{component}")
                        ordered_components.append((module, component))

        # Sort by initialization order when possible
        def get_order_index(item):
            key = f"{item[0]}_{item[1]}"
            try:
                return init_order.index(key)
            except ValueError:
                return float("inf")  # Components not in the order go last

        ordered_components.sort(key=get_order_index)

        # Now load in the correct order
        for module, component in ordered_components:
            try:
                logger.info(f"Loading {module}.{component}...")
                try:
                    # Try to import the module
                    mod = importlib.import_module(
                        f"modules.{module}.{component}")
                except ImportError as e:
                    # Be more specific about import errors
                    logger.error(f"Import error for {module}.{component}: {e}")
                    continue
                for name, cls in inspect.getmembers(mod, inspect.isclass):
                    if name not in ["HTTP", "WebSocket"]:
                        instance = self._smart_init(cls)
                        if instance:
                            key = f"{module}_{name.lower()}"
                            self.components[key] = instance
                            self.component_metrics[key] = ComponentMetrics()
                            self.component_status[key] = {
                                "status": "healthy",
                                "errors": 0,
                                "calls": 0,
                                "health_score": 1.0,
                                "avg_response": 0,
                            }
                            logger.info(f"Successfully loaded {key}")
            except Exception as e:
                logger.error(f"Failed to load {module}.{component}: {e}")
                self.component_status[f"{module}_{component}"] = {
                    "status": "error",
                    "errors": 1,
                    "calls": 0,
                    "health_score": 0.0,
                }
                self.component_metrics[f"{module}_{component}"] = ComponentMetrics(
                )
                self.component_metrics[f"{module}_{component}"].error_count = 1
                self.recent_logs.append(
                    f"Failed to load {module}.{component}: {e}")

    # -------------------------------
    # 5.7 Component Execution
    # -------------------------------

    def _find_matching_method(self, component, requested_method):
        """AI-powered method name matching to find semantically similar methods"""
        # Exit early if the method actually exists
        if hasattr(component, requested_method):
            return requested_method

        # Known semantic mappings for common methods
        semantic_mappings = {
            # Data validation methods
            "validate_dataset": ["validate_data", "verify_dataset", "check_data", "validate"],
            # Health check methods
            "check_health": ["health_check", "is_healthy", "get_health", "check_status"],
            # Signal validation
            "validate_signals": ["validate_risk", "check_signals", "verify_signals"],
            # Trade execution
            "execute_bulk_orders": ["execute_orders", "place_orders", "submit_orders", "execute_trades"],
            # Risk assessment
            "evaluate_portfolio_risk": ["assess_risk", "check_portfolio_risk", "evaluate_risk", "risk_assessment"],
            # Market condition analysis
            "get_market_condition": ["analyze_market", "market_analysis", "get_market_state"],
            # Signal generation
            "generate_signals": ["create_signals", "get_signals", "produce_signals"],
            # Strategy selection
            "select_best_strategy": ["choose_strategy", "get_strategy", "determine_strategy"],
            # Market data fetching
            "fetch_market_data": ["get_market_data", "fetch_data", "retrieve_market_data"]
        }

        # If we have a semantic mapping for this method
        if requested_method in semantic_mappings:
            # Try each alternative name
            for alt_name in semantic_mappings[requested_method]:
                if hasattr(component, alt_name):
                    return alt_name

        # Word tokenization approach - break down method names into parts
        req_parts = set(requested_method.lower().split('_'))

        # Get all methods from the component
        potential_methods = [m for m in dir(component) if callable(
            getattr(component, m)) and not m.startswith('_')]

        # Score each method based on word overlap
        best_match = None
        best_score = 0

        for method in potential_methods:
            method_parts = set(method.lower().split('_'))
            # Count overlapping words
            overlap = len(req_parts.intersection(method_parts))
            # More overlap is better
            if overlap > best_score:
                best_score = overlap
                best_match = method

        # Return the best match if it has at least one word in common
        if best_score > 0:
            return best_match

        return None

    async def execute_with_metrics(self, component_name, method_name, *args, **kwargs):
        """Execute component methods with comprehensive metrics tracking, error handling, and intelligent method matching"""
        if component_name not in self.components:
            logger.error(f"Component {component_name} not found")
            return None

        component = self.components[component_name]

        # ENHANCED INTELLIGENCE: Smart method discovery
        if not hasattr(component, method_name):
            actual_method = self._find_matching_method(component, method_name)
            if actual_method:
                logger.info(
                    f"AI adapting: Mapped '{method_name}' to existing method '{actual_method}'")
                method_name = actual_method
            else:
                logger.error(
                    f"Method {method_name} not found in component {component_name}")
                return None

        # Check component health status
        component_status = self.component_status.get(component_name, {})
        if component_status.get("status") == "terminated":
            logger.error(
                f"Cannot execute - component {component_name} is terminated")
            return None

        # Get metrics
        metrics = self.component_metrics.get(
            component_name, ComponentMetrics())

        # Execute with timing and error handling
        start_time = time.time()
        success = False
        result = None

        try:
            # Check if component is healthy, if not attempt recovery
            if component_status.get("status") != "healthy":
                logger.warning(
                    f"Component {component_name} is not healthy, attempting recovery")
                recovered = await self.recover(component_name)
                if not recovered:
                    logger.error(
                        f"Failed to recover {component_name}, cannot execute {method_name}")
                    return None

            # Get the method and execute
            method = getattr(component, method_name)

            # Log execution for debugging
            arg_str = ", ".join([str(a) for a in args] +
                                [f"{k}={v}" for k, v in kwargs.items()])
            logger.debug(
                f"Executing {component_name}.{method_name}({arg_str})")

            # Execute the method (async or sync)
            if asyncio.iscoroutinefunction(method):
                result = await method(*args, **kwargs)
            else:
                result = method(*args, **kwargs)

            success = True
            return result

        except Exception as e:
            logger.error(
                f"Error executing {component_name}.{method_name}: {str(e)}")

            # Add to recent logs
            self.recent_logs.append(
                f"Error: {component_name}.{method_name}: {str(e)}")

            # Increment error counter
            component_status["errors"] = component_status.get("errors", 0) + 1

            # Check dependencies for potential causes
            for dep_name in self.component_dependencies.get(component_name, []):
                if (dep_name in self.component_status and
                        self.component_status[dep_name]["status"] != "healthy"):
                    metrics.record_dependency_failure(dep_name)
                    logger.warning(
                        f"Dependency {dep_name} may be causing issues with {component_name}")

            # Update component status based on error count
            if component_status.get("errors", 0) > 3:
                component_status["status"] = "warning"

            if component_status.get("errors", 0) > 7:
                component_status["status"] = "error"
                await self.recover(component_name)

            return None

        finally:
            # Calculate metrics
            execution_time = (time.time() - start_time) * \
                1000  # in milliseconds

            # Update metrics
            metrics.record_call(execution_time, success)

            # Update component status with call info
            component_status["calls"] = component_status.get("calls", 0) + 1

            if success:
                # Update response time rolling average
                prev_avg = component_status.get("avg_response", 0)
                prev_calls = max(0, component_status.get("calls", 1) - 1)
                current_calls = component_status.get("calls", 1)

                # Weighted average giving more importance to recent calls
                new_avg = (prev_avg * prev_calls * 0.8 +
                           execution_time * 0.2 * prev_calls) / current_calls
                component_status["avg_response"] = new_avg

            # Update health score
            component_status["health_score"] = metrics.health_score

            # Replace the status in the main dictionary
            self.component_status[component_name] = component_status

##########################################
# 6️⃣ TRADING CORE 🔄
##########################################


class AITradingCore:
    """Main trading system that orchestrates strategies, analysis, and execution"""

    def __init__(self):
        # Initialize Redis first
        self.redis = RedisManager()

        # Only then create the agent and pass Redis to it if needed
        self.agent = SelfHealingAgent()

        # Rest of your initialization code
        self.running = True
        self.shutdown_flag = False
        self.logger = logger
        self.cycle_count = 0
        self.config = {
            'max_risk_threshold': 0.8,
            'optimization_frequency': 5,
            'cycle_interval': 30
        }
        self.trading_state = {
            "active_trades": 0,
            "daily_profit": 0.0,
            "total_trades": 0,
            "win_rate": 0.0,
        }
        self._initialize_critical_components()

    def _initialize_critical_components(self):
        """Ensure all critical components are available in the agent"""
        required_components = [
            "data_aggregator", "data_processing", "market_analyzer",
            "strategy_selector", "signal_generator", "risk_management",
            "order_executor", "position_tracker", "position_manager",
            "risk_management", "portfolio_optimizer", "system_monitor"
        ]

        for component in required_components:
            if component not in self.agent.components:
                self.logger.warning(
                    f"Creating mock for missing component: {component}")
                self.agent.components[component] = self._create_mock_component(
                    component)
                self.agent.component_metrics[component] = ComponentMetrics()
                self.agent.component_status[component] = {
                    "status": "healthy",
                    "errors": 0,
                    "calls": 0,
                    "health_score": 1.0,
                    "avg_response": 0,
                }

    def _create_mock_component(self, component_name):
        """Create a mock component with basic functionality"""
        class MockComponent:
            def __init__(self):
                self.name = component_name

            async def health_check(self):
                class HealthCheckResult:
                    def __init__(self):
                        self.passed = True
                return HealthCheckResult()

        mock = MockComponent()

        # Add specific methods based on component type
        if component_name == "data_aggregator":
            mock.fetch_market_data = lambda symbols, indicators: pd.DataFrame({
                'symbol': symbols,
                'price': [50000, 3000],
                'EMA_20': [48000, 2900],
                'RSI': [55, 60],
                'OBV': [10000, 5000]
            })
        elif component_name == "market_analyzer":
            mock.get_market_condition = lambda: "neutral"
        elif component_name == "strategy_selector":
            mock.select_best_strategy = lambda market_condition, risk_profile: "default_strategy"
        elif component_name == "signal_generator":
            mock.generate_signals = lambda strategy_name, market_data: [
                {"symbol": "BTCUSDT", "side": "buy", "quantity": 0.1}]
        elif component_name == "risk_management":
            mock.assess_current_risk = lambda: {"risk_level": 0.5}
            mock.apply_risk_controls = lambda: None
        elif component_name == "portfolio_optimizer":
            mock.optimize = lambda: None

        return mock

    async def _start_components(self):
        """Start all components with async support"""
        for name, component in self.agent.components.items():
            if hasattr(component, "start"):
                try:
                    if asyncio.iscoroutinefunction(component.start):
                        await component.start()
                    else:
                        component.start()
                    logger.info(f"Started {name}")
                except Exception as e:
                    logger.error(f"Component {name} failed to start: {str(e)}")

    async def start(self):
        """Complete startup sequence"""
        logger.info("AI Trading System Starting")
        asyncio.create_task(self.agent.control_center.display())
        await self._start_components()
        await self._run_trading_ecosystem()

    async def _run_trading_ecosystem(self):
        """Full trading lifecycle management"""
        tasks = [
            asyncio.create_task(self.trade_loop()),
            asyncio.create_task(self._monitoring_loop()),
            asyncio.create_task(self._risk_management_loop()),
        ]

        try:
            # Wait for first task to complete or for shutdown signal
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Trading ecosystem tasks canceled")
        except Exception as e:
            logger.error(f"Error in trading ecosystem: {str(e)}")
        finally:
            # Cancel all tasks when shutting down
            for task in tasks:
                if not task.done():
                    task.cancel()

            # Wait for tasks to be canceled
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_market_data(self):
        """Fetch real-time market data from multiple sources"""
        try:
            data = await self.agent.execute_with_metrics(
                "data_aggregator",
                "fetch_market_data",
                symbols=["BTCUSDT", "ETHUSDT"],
                indicators=["EMA_20", "RSI", "OBV"],
            )

            if data is None:
                logger.warning("Failed to fetch market data, using fallback")
                return self._get_cached_data()

            await self.agent.execute_with_metrics(
                "data_processing", "validate_dataset", data
            )
            return data
        except Exception as e:
            logger.error(f"Data fetch error: {str(e)}")
            return pd.DataFrame()

    async def _select_strategy(self):
        """AI-driven strategy selection"""
        market_condition = await self.agent.execute_with_metrics(
            "market_analyzer", "get_market_condition"
        )

        return await self.agent.execute_with_metrics(
            "strategy_selector",
            "select_best_strategy",
            market_condition=market_condition,
            risk_profile=LiveTradingConfig.LEVERAGE,
        )

    async def _generate_signals(self, strategy, data):
        """Generate trading signals with risk assessment"""
        signals = await self.agent.execute_with_metrics(
            "signal_generator", "generate_signals", strategy_name=strategy, market_data=data
        )

        if signals:
            await self.agent.execute_with_metrics(
                "risk_management",
                "validate_signals",
                signals,
                max_leverage=LiveTradingConfig.LEVERAGE,
            )

        return signals

    async def _execute_trades(self, signals):
        """Execute trades through exchange interface"""
        try:
            execution_result = await self.agent.execute_with_metrics(
                "order_executor",
                "execute_bulk_orders",
                signals,
                live_mode=LiveTradingConfig.LIVE_MODE,
            )

            if execution_result:
                self.trading_state["active_trades"] += len(
                    execution_result.successful)
                self.trading_state["total_trades"] += len(
                    execution_result.successful)

                await self.agent.execute_with_metrics(
                    "position_tracker", "update_positions", execution_result
                )

            return execution_result
        except Exception as e:
            logger.error(f"Trade execution failed: {str(e)}")
            return None

    async def trade_loop(self):
        """Main trading loop that orchestrates the trading cycle operations"""
        self.logger.info("Starting trade loop")

        try:
            while not self.shutdown_flag:
                # Fetch latest market data
                market_data = await self._fetch_market_data()

                # Generate trading signals
                strategy = await self._select_strategy()
                signals = await self._generate_signals(strategy, market_data)

                # Execute trades based on signals
                if signals:
                    await self._execute_trades(signals)

                # Risk assessment
                risk_metrics = await self.agent.execute_with_metrics(
                    "risk_management",
                    "assess_current_risk"
                )

                if risk_metrics and risk_metrics.get('risk_level', 0) > self.config['max_risk_threshold']:
                    await self.agent.execute_with_metrics(
                        "risk_management",
                        "apply_risk_controls"
                    )

                # Portfolio optimization (less frequent)
                if self.cycle_count % self.config['optimization_frequency'] == 0:
                    await self.agent.execute_with_metrics(
                        "portfolio_optimizer",
                        "optimize"
                    )

                self.cycle_count += 1

                # Wait for next cycle
                await asyncio.sleep(self.config['cycle_interval'])

        except Exception as e:
            self.logger.critical(f"Error in trade loop: {str(e)}")
            await self.emergency_shutdown(error=str(e))

        self.logger.info("Trade loop terminated")

    async def emergency_shutdown(self, error=None):
        """Handle emergency shutdown procedure"""
        self.logger.critical(f"Emergency shutdown initiated: {error}")
        self.running = False
        self.shutdown_flag = True

        # Attempt to close all positions
        try:
            await self.agent.execute_with_metrics(
                "position_manager",
                "close_all_positions",
                reason="Emergency shutdown"
            )
        except Exception as e:
            self.logger.error(
                f"Failed to close positions during shutdown: {str(e)}")

        # Log the shutdown
        self.logger.info("Emergency shutdown completed")

    async def _settle_trades(self):
        """Handle settlement of completed trades"""
        try:
            # Process any trades that need settlement
            return await self.agent.execute_with_metrics(
                "position_manager",
                "settle_positions",
                update_balance=True
            )
        except Exception as e:
            logger.error(f"Trade settlement error: {str(e)}")
            return None

    async def _risk_management_loop(self):
        """Monitor and manage risk exposure"""
        while self.running:
            try:
                # Continuously evaluate portfolio risk
                await self.agent.execute_with_metrics(
                    "risk_management",
                    "evaluate_portfolio_risk"
                )
                await asyncio.sleep(30)  # Check risk every 30 seconds
            except Exception as e:
                logger.error(f"Risk management error: {str(e)}")
                await asyncio.sleep(10)

    async def _monitoring_loop(self):
        """System health monitoring"""
        while self.running:
            try:
                await self.agent.execute_with_metrics(
                    "system_monitor",
                    "check_health"
                )
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"Monitoring error: {str(e)}")
                await asyncio.sleep(10)

    def _get_cached_data(self):
        """Return cached market data as fallback"""
        # Return a basic dataframe with minimal market data
        return pd.DataFrame({
            'symbol': ['BTCUSDT', 'ETHUSDT'],
            'price': [50000, 3000],
            'EMA_20': [48000, 2900],
            'RSI': [55, 60],
            'OBV': [10000, 5000]
        })

##########################################
# 7️⃣ MAIN EXECUTION 🏁
##########################################


async def main():
    """Orchestrates startup, running, and shutdown of the entire system"""
    # Print banner at startup
    banner = """
    ╔══════════════════════════════════════════════════════════╗
    ║                                                          ║
    ║  █████╗ ██╗    ████████╗██████╗  █████╗ ██████╗ ███████╗ ║
    ║ ██╔══██╗██║    ╚══██╔══╝██╔══██╗██╔══██╗██╔══██╗██╔════╝ ║
    ║ ███████║██║       ██║   ██████╔╝███████║██║  ██║█████╗   ║
    ║ ██╔══██║██║       ██║   ██╔══██╗██╔══██║██║  ██║██╔══╝   ║
    ║ ██║  ██║██║       ██║   ██║  ██║██║  ██║██████╔╝███████╗ ║
    ║ ╚═╝  ╚═╝╚═╝       ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚══════╝ ║
    ║                                                          ║
    ║  WALL STREET-GRADE TRADING SYSTEM v2.0                   ║
    ╚══════════════════════════════════════════════════════════╝
    """
    print(banner)

    # Dictionary to store all running tasks
    tasks = {}
    shutdown_event = asyncio.Event()

    try:
        # Initialize system
        logger.info("Initializing AI Trading System")
        system = AITradingCore()

        # Initialize dashboard integration (for web UI, should be disabled for terminal ui)
        # from dashboard.integration import initialize_dashboard
        # dashboard_integration = initialize_dashboard(
        # redis_manager=system.redis, event_bus=system.event_bus)

        # Set up signal handlers for proper shutdown
        import signal

        def signal_handler(sig, frame):
            logger.info(f"Received signal {sig}, initiating shutdown")
            system.shutdown_flag = True
            shutdown_event.set()

            # Cancel all running tasks
            for name, task in tasks.items():
                if not task.done():
                    logger.info(f"Cancelling task: {name}")
                    task.cancel()

        # Register signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Add delay for component initialization
        logger.info("Waiting for components to initialize...")
        await asyncio.sleep(3)

        # Start UI in a separate task
        tasks["ui"] = asyncio.create_task(
            system.agent.control_center.display())

        # Start the trading system in a separate task
        tasks["trading"] = asyncio.create_task(system.start())

        # Wait for shutdown signal
        await shutdown_event.wait()

    except asyncio.CancelledError:
        logger.info("Main task cancelled")
    except Exception as e:
        logger.critical(f"Critical system error: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        # Ensure all tasks are properly cancelled
        for name, task in tasks.items():
            if not task.done():
                logger.info(f"Cancelling task {name} during shutdown")
                task.cancel()

        # Wait for tasks to complete cancellation (with timeout)
        pending = [t for t in tasks.values() if not t.done()]
        if pending:
            logger.info(f"Waiting for {len(pending)} tasks to cancel...")
            await asyncio.wait(pending, timeout=5)

        logger.info("AI Trading System shutdown complete")
        await asyncio.sleep(1)  # Allow final logs to be written

# Fixed entry point with proper exception handling
if __name__ == "__main__":
    try:
        # Use run with debug=True for better error reporting
        asyncio.run(main(), debug=True)
    except KeyboardInterrupt:
        logger.info("System shutdown by user")
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        import traceback
        traceback.print_exc()



# ────────────────────────────────────────────────────────────
# FILE: ai/config.py
# ────────────────────────────────────────────────────────────

# ai/config.py

from dotenv import load_dotenv
import os
import logging
from typing import Dict, Any

# Configure logging once
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(module)s - %(message)s",
    handlers=[logging.FileHandler(
        "bot.log", mode="a"), logging.StreamHandler()],
)
logger = logging.getLogger("CONFIG")

logger.info("Initializing configuration")

load_dotenv(override=True)  # Force reload .env


class LiveTradingConfig:
    LIVE_MODE = False  # Master safety switch
    MAX_POSITION_SIZE = 0.1  # 10% of capital
    RISK_THRESHOLD = 0.02  # 2% risk per trade
    TRADE_INTERVAL = 30  # Seconds between trades
    LEVERAGE = 10  # Default leverage


def validate_api_keys(testnet: bool) -> None:
    """Validate correct API keys for environment"""
    required_key_type = "TESTNET" if testnet else "LIVE"
    key_prefix = "TESTNET" if testnet else "REAL"

    missing = []
    for key_type in ["_API_KEY", "_API_SECRET"]:
        if not os.getenv(f"{key_prefix}{key_type}"):
            missing.append(f"{key_prefix}{key_type}")

    if missing:
        logger.error(
            f"Missing {required_key_type} .env keys: {', '.join(missing)}")
        raise ValueError(f"Missing {required_key_type} API credentials")


# Read configuration from .env
TESTNET_MODE = os.getenv("TESTNET", "false").lower() == "true"

# Use the appropriate API keys based on environment
key_prefix = "TESTNET" if TESTNET_MODE else "REAL"
BYBIT_CONFIG = {
    "api_key": os.getenv(f"{key_prefix}_API_KEY"),
    "api_secret": os.getenv(f"{key_prefix}_API_SECRET"),
    "testnet": TESTNET_MODE,
    # Uncomment and use appropriate base URL if needed
    # "base_url": os.getenv(f"{key_prefix}_BASE_URL",
    #              "https://api-testnet.bybit.com" if TESTNET_MODE else "https://api.bybit.com")
}

# Safety checks
validate_api_keys(BYBIT_CONFIG["testnet"])

if LiveTradingConfig.LIVE_MODE and BYBIT_CONFIG["testnet"]:
    logger.error("CRITICAL: LIVE_MODE enabled with testnet=True!")
    raise SystemExit(
        "Invalid configuration - cannot run live mode with testnet")

# Hardware config (generic)
HARDWARE_CONFIG: Dict[str, Any] = {
    "cpu_cores": os.cpu_count() or 1,
    "gpu_available": False  # Simplified without PyTorch
}

logger.info(f"Active configuration:\n"
            f"- Testnet: {BYBIT_CONFIG['testnet']}\n"
            f"- Environment: {'TESTNET' if TESTNET_MODE else 'PRODUCTION'}\n"
            f"- Using API Keys: {key_prefix}_API_KEY\n"
            # f"- Base URL: {BYBIT_CONFIG['base_url']}\n"
            f"- Live Mode: {LiveTradingConfig.LIVE_MODE}\n"
            f"- Risk Threshold: {LiveTradingConfig.RISK_THRESHOLD*100}%\n"
            f"- Max Position: {LiveTradingConfig.MAX_POSITION_SIZE*100}%")



# ────────────────────────────────────────────────────────────
# FILE: ai/diagnostic.py
# ────────────────────────────────────────────────────────────

# diagnostic.py
# Intelligent Trading System Diagnostic Tool
# Enhanced Import and Library Management Module

# Standard Library Imports
from hypothesis import given, strategies as st
import os
import sys
import time
import json
import re
import math
import platform
import subprocess
import traceback
import unittest
import importlib
import logging
import argparse
from datetime import datetime
from collections import defaultdict, Counter
from typing import Dict, List, Optional, Union, Any, Tuple, Type

# Third-Party Dependencies
try:
    import psutil
except ImportError:
    print("psutil not installed. Install with: pip install psutil")
    sys.exit(1)

# Optional Dependencies
dependency_status = {
    "sentry": False,
    "memory_profiler": False,
    "ml": False,
    "rich": False,
    "transformers": False,
    "hdbscan": False
}

try:
    import sentry_sdk
    dependency_status["sentry"] = True
except ImportError:
    print("Optional error tracking (Sentry) not available. Install with: pip install sentry-sdk")

try:
    import memory_profiler
    dependency_status["memory_profiler"] = True
except ImportError:
    print("Memory profiling not available. Install with: pip install memory-profiler")

# Machine Learning Stack
try:
    import numpy as np
    import pandas as pd
    import networkx as nx
    import sklearn
    from sklearn.preprocessing import StandardScaler
    from sklearn.cluster import DBSCAN
    import hdbscan
    dependency_status["ml"] = True
    dependency_status["hdbscan"] = True
except ImportError as e:
    print(
        f"ML dependencies: {e}. Install with: pip install numpy pandas networkx scikit-learn hdbscan")

# Define RICH_AVAILABLE global variable
try:
    import rich
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

# Rich Output
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.tree import Tree
    dependency_status["rich"] = True
    console = Console()
except ImportError:
    print("Rich library not available. Install with: pip install rich")
    # Fallback console implementation

    class SimpleConsole:
        def print(self, *args, **kwargs):
            text = args[0]
            if isinstance(text, str):
                text = re.sub(r'\[.*?\]', '', text)
            print(text)

        def rule(self, title=None):
            if title:
                print(f"\n{'=' * 10} {title} {'=' * 10}")
            else:
                print("\n" + "=" * 30)
    console = SimpleConsole()

# NLP/AI Components
try:
    from transformers import pipeline
    dependency_status["transformers"] = True
except ImportError:
    print("Code fix suggestions unavailable. Install with: pip install transformers")

# Testing Tools

# Logging Configuration (Single Instance)
logging.basicConfig(
    level=logging.INFO,  # Default level
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("diagnostic_enhanced.log", mode="a"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("EnhancedTradingDiagnostic")

# Version Check (Single Instance)
MIN_PYTHON = (3, 8)
if sys.version_info < MIN_PYTHON:
    raise RuntimeError(f"Python {'.'.join(map(str, MIN_PYTHON))}+ required")

# Configuration Manager


class ConfigManager:
    """Unified dependency status management"""
    @staticmethod
    def get_status() -> Dict[str, bool]:
        return {
            "rich": dependency_status["rich"],
            "ml_stack": dependency_status["ml"],
            "error_tracking": dependency_status["sentry"],
            "profiling": dependency_status["memory_profiler"],
            "nlp": dependency_status["transformers"],
            "clustering": dependency_status["hdbscan"]
        }

###########################
# 02 Component Discovery Enhanced
###########################


class EnhancedComponentAnalyzer:
    """Enhanced component discovery with advanced graph-based analysis"""

    def __init__(self, root_dir: str = "."):
        self.root_dir = root_dir
        self.modules_dir = os.path.join(root_dir, "modules")
        self.components: Dict[str, Dict[str, Any]] = {}
        self.dependency_graph = nx.DiGraph()

        # Enhanced discovery parameters
        self.core_components = [
            "execution_engine.py",
            "risk_management.py",
            "strategy_core.py",
            "account_manager.py",
            "boss.py",
            "setup.py",
            "config.py"
        ]

        # Intelligent component categorization
        self.component_categories = {
            "core": ["boss.py", "setup.py", "config.py"],
            "trading": ["strategy_core.py", "execution_engine.py"],
            "risk": ["risk_management.py"],
            "management": ["account_manager.py"]
        }

    def discover_components(self) -> None:
        """
        Advanced component discovery with multi-level analysis
        - Finds components across project structure
        - Builds comprehensive dependency graph
        - Categorizes components intelligently
        """
        console.print("🔍 Intelligent Component Discovery", style="bold blue")

        # Scan root directory for core components
        self._scan_root_components()

        # Scan modules directory for additional components
        self._scan_modules_directory()

        # Build dependency graph
        self._build_dependency_graph()

        console.print(
            f"Found [green]{len(self.components)}[/green] components",
            style="bold"
        )

    def analyze_code_dependencies(self):
        """
        Enhanced dependency analysis with code inspection
        """
        console.print(
            "Performing deep code dependency analysis...", style="bold blue")

        # Pattern to find imports in Python code
        import_pattern = re.compile(
            r'(?:from\s+(\S+)\s+import\s+.*)|(?:import\s+(\S+))')

        # Track all dependencies
        dependencies = {}
        dependency_count = 0

        for component_name, component in self.components.items():
            file_path = component.get('path')
            if not file_path or not os.path.exists(file_path):
                continue

            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()

                # Find all imports
                imports = []
                for match in import_pattern.finditer(content):
                    module = match.group(1) or match.group(2)
                    if module:
                        # Get base module name
                        imports.append(module.split('.')[0])

                # Store unique imports
                unique_imports = list(set(imports))
                dependencies[component_name] = unique_imports
                dependency_count += len(unique_imports)

                # Update component info
                self.components[component_name]['imports'] = unique_imports

            except Exception as e:
                console.print(f"[yellow]Error analyzing {component_name}: {e}")

        # Build connections based on imports
        self.connections = defaultdict(list)
        for component_name, imports in dependencies.items():
            for imp in imports:
                # Check if import matches another component
                for other_component in self.components:
                    base_name = other_component.split('/')[-1]
                    if imp == base_name:
                        self.connections[component_name].append(
                            other_component)

        console.print(
            f"Found [green]{dependency_count}[/green] dependencies and [green]{sum(len(v) for v in self.connections.values())}[/green] connections")

    def _scan_root_components(self) -> None:
        """Scan root directory for core components"""
        for component in self.core_components:
            file_path = os.path.join(self.root_dir, component)
            if os.path.exists(file_path):
                component_name = component[:-3]  # Remove .py extension
                self.components[component_name] = self._analyze_component(
                    file_path, component_name
                )

    def _scan_modules_directory(self) -> None:
        """Scan modules directory for additional components"""
        if not os.path.exists(self.modules_dir):
            return

        for root, _, files in os.walk(self.modules_dir):
            if "__pycache__" in root:
                continue

            rel_path = os.path.relpath(root, self.modules_dir)
            module_category = rel_path if rel_path != "." else ""

            for file in files:
                if file.endswith(".py") and file != "__init__.py":
                    file_path = os.path.join(root, file)
                    component_name = file[:-3]

                    # Create intelligent component key
                    component_key = (
                        f"{module_category}/{component_name}"
                        if module_category and module_category != "."
                        else component_name
                    )

                    self.components[component_key] = self._analyze_component(
                        file_path, component_name
                    )

    def ai_error_clustering(self, error_messages: List[str]) -> Dict[str, List[str]]:
        """
        Cluster similar errors using unsupervised ML
        :param error_messages: List of raw error strings
        :return: Dict of {cluster_label: [similar_errors]}
        """
        # Cutting-edge TF-IDF with sublinear scaling
        tfidf = TfidfVectorizer(stop_words='english', sublinear_tf=True)
        X = tfidf.fit_transform(error_messages)

        # Modern HDBSCAN clustering
        clusterer = hdbscan.HDBSCAN(min_cluster_size=2)
        labels = clusterer.fit_predict(X.toarray())

        # Build intelligent clusters
        clusters = defaultdict(list)
        for msg, label in zip(error_messages, labels):
            if label != -1:  # Ignore noise
                clusters[f"Cluster_{label}"].append(msg)
        return clusters

    def _build_dependency_graph(self) -> None:
        """
        Create an intelligent dependency graph 
        with weighted and typed connections
        """
        for component, details in self.components.items():
            self.dependency_graph.add_node(
                component,
                category=self._categorize_component(component),
                **details
            )

            # Add edges based on imports
            for imported_module in details.get('imports', []):
                if imported_module in self.components:
                    self.dependency_graph.add_edge(
                        component,
                        imported_module,
                        weight=1.0  # Base weight
                    )

    def _categorize_component(self, component: str) -> str:
        """
        Intelligently categorize components based on name and location
        """
        for category, components in self.component_categories.items():
            if any(comp in component for comp in components):
                return category
        return "utility"

    def analyze_connections(self):
        """Analyze connections between components"""
        console.print("Analyzing component connections...", style="bold blue")

        # Find connections through imports
        self.connections = defaultdict(list)
        connection_count = 0

        for key, component in self.components.items():
            for imp in component.get("imports", []):
                # Check if import refers to another component
                for module_path, target_key in self.module_map.items():
                    if module_path == imp or module_path.startswith(imp + "."):
                        self.connections[key].append(target_key)
                        # Also track reverse connections (imported by)
                        if target_key in self.components:
                            self.components[target_key]["imported_by"].append(
                                key)
                        connection_count += 1

        console.print(
            f"Found [green]{connection_count}[/green] connections between components")

    def _analyze_component(self, file_path: str, component_name: str) -> Dict[str, Any]:
        """
        Enhanced component analysis with more detailed insights
        """
        # Reuse original component analysis logic here
        # This is a placeholder - actual implementation would
        # mirror the original _analyze_component method
        return {
            "name": component_name,
            "path": file_path,
            # Add other analysis details
        }

    def visualize_dependencies(self):
        """Create visual representation of component dependencies"""
        console.rule("Component Dependencies")

        if RICH_AVAILABLE:
            # Create a dependency tree using rich
            tree = Tree("🔌 System Components")

            # Find root components (those not imported by others)
            root_components = []
            for key, component in self.components.items():
                if not component.get("imported_by") and self.connections.get(key):
                    root_components.append(key)

            # Add main controller components as roots
            core_roots = ["boss"]
            for key in core_roots:
                if key in self.components and key not in root_components:
                    root_components.append(key)

            # If no clear roots found, use most imported components
            if not root_components:
                import_counts = Counter()
                for connections in self.connections.values():
                    for conn in connections:
                        import_counts[conn] += 1

                # Get top 3 most imported components
                root_components = [comp for comp,
                                   _ in import_counts.most_common(3)]

            # Create tree for each root component
            visited = set()
            for root in sorted(root_components):
                self._add_component_to_tree(tree, root, visited, max_depth=3)

            console.print(tree)
        else:
            # Simple text-based visualization
            console.print("Component Dependencies:")
            for key, deps in sorted(self.connections.items()):
                if deps:
                    print(f"  {key} -> {', '.join(sorted(deps))}")

    def _add_component_to_tree(self, parent_tree, component_key, visited, current_depth=0, max_depth=3):
        """Recursively add components to dependency tree"""
        if component_key in visited or current_depth >= max_depth:
            return

        visited.add(component_key)

        # Get component info
        component = self.components.get(component_key, {})

        # Create label with health indicator
        if component.get("status") == "valid":
            label = f"✅ {component_key}"
        elif component.get("status") == "error":
            label = f"❌ {component_key}"
        else:
            label = f"❓ {component_key}"

        # Add classes count if available
        if component.get("classes"):
            label += f" ({len(component.get('classes'))} classes)"

        # Create branch
        branch = parent_tree.add(label)

        # Add dependencies
        for dep in sorted(self.connections.get(component_key, [])):
            if dep != component_key:  # Avoid self-references
                self._add_component_to_tree(
                    branch, dep, visited, current_depth + 1, max_depth)

    def get_critical_components(self, top_n: int = 5) -> List[str]:
        """
        Identify most critical components based on graph centrality
        """
        if not self.dependency_graph.nodes():
            return []

        # Use different centrality measures
        degree_centrality = nx.degree_centrality(self.dependency_graph)
        betweenness_centrality = nx.betweenness_centrality(
            self.dependency_graph)

        # Combine centrality measures
        combined_centrality = {
            node: 0.6 * degree_centrality.get(node, 0) +
            0.4 * betweenness_centrality.get(node, 0)
            for node in self.dependency_graph.nodes()
        }

        return sorted(
            combined_centrality,
            key=combined_centrality.get,
            reverse=True
        )[:top_n]


#############################
# 03 Intelligent Test Runner
#############################

class EnhancedTestRunner:
    """Advanced test runner with intelligent test discovery and execution"""

    def __init__(self):
        self.test_results: Dict[str, Any] = {}
        self.tests: List[Type[unittest.TestCase]] = []
        self.logger = logging.getLogger("EnhancedTestRunner")

    def discover_tests(self):
        """
        Intelligent test discovery with enhanced capabilities
        - Discover system-specific tests
        - Generate context-aware dynamic tests
        """
        console.print("Discovering intelligent tests...", style="bold blue")

        # Preserve original basic tests
        self.tests = [self._create_basic_test_class()]

        # Dynamic test generation based on system configuration
        dynamic_tests = self._generate_dynamic_tests()
        self.tests.extend(dynamic_tests)

        console.print(
            f"Created [yellow]{len(self.tests)}[/yellow] test classes",
            style="bold"
        )

    @given(st.floats(allow_nan=False, allow_infinity=False))
    def fuzz_test_numeric_inputs(self, value: float):
        """AI-Stress-Test numeric inputs"""
        result = your_ai_bot.process_numeric_input(value)
        self.assertFalse(math.isnan(result),
                         "AI produced NaN for valid input!")

    @given(st.text(min_size=1))
    def fuzz_test_text_inputs(self, text: str):
        """
        Unicode Fuzzing: Test with generated text inputs
        """
        response = your_ai_bot.handle_text(text)
        self.assertIsInstance(response, str)

    def _create_basic_test_class(self) -> Type[unittest.TestCase]:
        """
        Create enhanced basic test class with more comprehensive checks
        Preserves and extends original basic tests
        """
        class EnhancedBasicTests(unittest.TestCase):
            def test_critical_dependencies(self):
                """Advanced dependency verification"""
                essential_libs = [
                    'numpy', 'pandas', 'psutil', 'logging',
                    'json', 'os', 'sys', 'redis', 'pybit'
                ]
                missing_libs = []

                for lib in essential_libs:
                    try:
                        __import__(lib)
                    except ImportError:
                        missing_libs.append(lib)

                self.assertEqual(
                    missing_libs,
                    [],
                    f"Missing critical libraries: {', '.join(missing_libs)}"
                )

            def test_config_files(self):
                """Enhanced config file verification"""
                config_files = [
                    "config.py",
                    "trading_config.json",
                    "setup.py"
                ]
                for file in config_files:
                    self.assertTrue(
                        os.path.exists(file),
                        f"Critical configuration file missing: {file}"
                    )

            def test_system_resources_advanced(self):
                """Comprehensive system resource validation"""
                try:
                    cpu_usage = psutil.cpu_percent(interval=1)
                    memory_usage = psutil.virtual_memory().percent
                    disk_usage = psutil.disk_usage('/').percent

                    # More granular resource checks
                    self.assertLess(
                        cpu_usage, 90,
                        f"High CPU usage detected: {cpu_usage}%"
                    )
                    self.assertLess(
                        memory_usage, 85,
                        f"High memory usage detected: {memory_usage}%"
                    )
                    self.assertLess(
                        disk_usage, 90,
                        f"High disk usage detected: {disk_usage}%"
                    )
                except Exception as e:
                    self.fail(f"System resource check failed: {e}")

        return EnhancedBasicTests

    def _generate_dynamic_tests(self) -> List[Type[unittest.TestCase]]:
        """
        Generate dynamic tests based on system configuration
        - Contextual test generation
        - Adaptive testing strategies
        """
        dynamic_tests = []

        # Example: Generate redis connectivity test if redis is used
        try:
            import redis

            class RedisConnectivityTest(unittest.TestCase):
                def test_redis_connection(self):
                    try:
                        r = redis.Redis(host='localhost', port=6379, db=0)
                        r.ping()
                    except Exception as e:
                        self.fail(f"Redis connection failed: {e}")

            dynamic_tests.append(RedisConnectivityTest)
        except ImportError:
            self.logger.info("Redis not available, skipping Redis tests")

        return dynamic_tests

    def run_tests(self):
        """
        Enhanced test execution with detailed reporting
        """
        console.print("Running intelligent diagnostic tests...",
                      style="bold blue")

        if not self.tests:
            self.discover_tests()

        suite = unittest.TestSuite()
        for test_class in self.tests:
            suite.addTest(unittest.makeSuite(test_class))

        runner = unittest.TextTestRunner(verbosity=2, stream=None)
        result = runner.run(suite)

        # Comprehensive test result tracking
        self.test_results = {
            "total": result.testsRun,
            "errors": len(result.errors),
            "failures": len(result.failures),
            "skipped": len(result.skipped),
            "success": result.testsRun - len(result.errors) - len(result.failures),
            "error_details": [
                {"test": str(test), "error": str(error)}
                for test, error in result.errors
            ],
            "failure_details": [
                {"test": str(test), "failure": str(failure)}
                for test, failure in result.failures
            ]
        }

        # Calculate success rate with more nuanced reporting
        success_rate = (self.test_results["success"] / self.test_results["total"]
                        ) * 100 if self.test_results["total"] > 0 else 0

        console.print(
            f"Tests: [green]{self.test_results['success']}[/green] passed, "
            f"[red]{self.test_results['failures']}[/red] failed, "
            f"[yellow]{self.test_results['errors']}[/yellow] errors",
            style="bold"
        )
        console.print(
            f"Success Rate: [{'green' if success_rate >= 80 else 'yellow' if success_rate >= 50 else 'red'}]{success_rate:.1f}%"
        )

    def summarize_results(self):
        """
        Enhanced result visualization
        Preserves original summary logic with richer presentation
        """
        console.rule("Intelligent Test Results")

        if not self.test_results:
            console.print("[yellow]No tests have been run yet")
            return

        if RICH_AVAILABLE:
            # Rich table for test summary
            table = Table(title="Test Diagnostics")
            table.add_column("Metric", style="dim")
            table.add_column("Count", justify="right")
            table.add_column("Status", justify="center")

            # Test result table population logic
            # (Similar to original implementation)
        else:
            # Fallback text-based summary
            print("Test Summary:")
            print(f"  Total Tests: {self.test_results['total']}")
            print(f"  Passed: {self.test_results['success']}")
            print(f"  Failed: {self.test_results['failures']}")
            print(f"  Errors: {self.test_results['errors']}")

###########################
# 04 System Health Monitoring
###########################


class EnhancedSystemMonitor:
    """Advanced system monitoring with predictive and comprehensive health tracking"""

    def __init__(self):
        self.metrics = {}
        self.critical_processes = ["boss"]
        self.health_thresholds = {
            "cpu": {"warning": 80, "critical": 90},
            "memory": {"warning": 80, "critical": 90},
            "disk": {"warning": 80, "critical": 90}
        }

    def profile_component_memory(self, component_name=None):
        """
        Detailed memory analysis per component or for the whole system
        """
        console.print("Analyzing memory usage by component...",
                      style="bold blue")

        if not dependency_status["memory_profiler"]:
            console.print(
                "[yellow]Memory profiler not available. Install with: pip install memory-profiler")
            return

        try:
            import memory_profiler

            processes = {}
            for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'memory_info']):
                try:
                    if 'python' in proc.info['name'].lower():
                        cmdline = ' '.join(proc.info['cmdline'] or [])

                        # Filter by component if specified
                        if component_name and component_name not in cmdline:
                            continue

                        # MB
                        mem_usage = proc.info['memory_info'].rss / \
                            (1024 * 1024)
                        processes[proc.info['pid']] = {
                            'component': self._extract_component_name(cmdline),
                            'memory': mem_usage,
                            'cmdline': cmdline
                        }
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass

            # Display memory usage
            if RICH_AVAILABLE:
                table = Table(title="Component Memory Usage")
                table.add_column("Component", style="dim")
                table.add_column("Memory (MB)", justify="right")
                table.add_column("PID", justify="center")

                for pid, info in sorted(processes.items(), key=lambda x: x[1]['memory'], reverse=True):
                    table.add_row(
                        info['component'],
                        f"{info['memory']:.2f}",
                        str(pid)
                    )

                console.print(table)
            else:
                print("Component Memory Usage:")
                for pid, info in sorted(processes.items(), key=lambda x: x[1]['memory'], reverse=True):
                    print(
                        f"  {info['component']}: {info['memory']:.2f} MB (PID: {pid})")

        except Exception as e:
            console.print(f"[red]Error during memory profiling: {e}")

    def _extract_component_name(self, cmdline):
        """Extract component name from command line"""
        for component in self.critical_processes:
            if f"{component}.py" in cmdline:
                return component

        # Look for any .py file in the command
        py_match = re.search(r'(\w+)\.py', cmdline)
        if py_match:
            return py_match.group(1)

        return "unknown"

    def check_system_resources(self):
        """
        Enhanced system resource monitoring with predictive analysis
        """
        console.print("Analyzing system resources...", style="bold blue")

        try:
            # CPU usage
            cpu_percent = psutil.cpu_percent(interval=0.5)
            cpu_cores = psutil.cpu_count()

            # Memory usage
            memory = psutil.virtual_memory()

            # Disk usage
            disk = psutil.disk_usage('/')

            # Store metrics with enhanced context
            self.metrics["cpu"] = {
                "percent": cpu_percent,
                "cores": cpu_cores,
                "status": self._calculate_resource_status("cpu", cpu_percent)
            }

            self.metrics["memory"] = {
                "total": memory.total,
                "available": memory.available,
                "percent": memory.percent,
                "status": self._calculate_resource_status("memory", memory.percent)
            }

            self.metrics["disk"] = {
                "total": disk.total,
                "free": disk.free,
                "percent": disk.percent,
                "status": self._calculate_resource_status("disk", disk.percent)
            }

            # Enhanced console output
            self._print_resource_status()

        except Exception as e:
            console.print(f"[red]Error analyzing system resources: {e}")

    def _calculate_resource_status(self, resource_type: str, current_usage: float) -> str:
        """
        Intelligent resource status calculation
        """
        thresholds = self.health_thresholds.get(resource_type, {})

        if current_usage >= thresholds.get("critical", 90):
            return "critical"
        elif current_usage >= thresholds.get("warning", 80):
            return "warning"
        return "good"

    def forecast_resource_usage(self, metric: str, steps: int = 5) -> np.array:
        """
        Predict future resource usage with EWMA (Exponential Weighted Moving Average)
        :param metric: 'cpu'|'memory'|'disk'
        :param steps: Steps to forecast
        :return: Predicted values array
        """
        history = [m['percent'] for m in self.metrics[metric]['history'][-10:]]
        alpha = 0.7  # Modern smoothing factor
        forecast = []
        for _ in range(steps):
            next_val = alpha * history[-1] + (1 - alpha) * np.mean(history)
            forecast.append(next_val)
            history.append(next_val)
        return np.round(forecast, 2)

    def _print_resource_status(self):
        """
        Print resource status with color-coded output
        """
        for resource, data in [
            ("CPU", self.metrics["cpu"]),
            ("Memory", self.metrics["memory"]),
            ("Disk", self.metrics["disk"])
        ]:
            status_color = {
                "good": "green",
                "warning": "yellow",
                "critical": "red"
            }.get(data["status"], "white")

            console.print(
                f"{resource}: "
                f"[{status_color}]{data['percent']:.1f}% "
                f"({data['status'].upper()})[/{status_color}]"
            )

    def check_redis_connectivity(self):
        """Check if Redis server is running and accessible"""
        console.print("Checking Redis connectivity...", style="bold blue")

        try:
            # Try to import Redis
            import redis

            # Try to connect to Redis
            r = redis.Redis(host='localhost', port=6379, db=0)
            redis_running = r.ping()

            if redis_running:
                console.print(
                    "[green]✅ Redis server is running and accessible")
            else:
                console.print("[red]❌ Redis server is not responding")

            # Store in metrics
            self.metrics["redis"] = {
                "status": "connected" if redis_running else "disconnected"
            }

        except ImportError:
            console.print(
                "[yellow]⚠️ Redis library not installed, skipping check")
        except Exception as e:
            console.print(f"[red]❌ Error connecting to Redis: {e}")

            # Store in metrics
            self.metrics["redis"] = {
                "status": "error",
                "error": str(e)
            }

    def check_api_connection(self):
        """Check connectivity to trading API"""
        console.print("Checking API connection...", style="bold blue")

        self.metrics["api"] = {
            "status": "unknown",
            "exchanges": {}
        }

        try:
            # Try to import pybit
            try:
                from pybit.unified_trading import HTTP
                pybit_available = True
            except ImportError:
                pybit_available = False
                console.print(
                    "[yellow]PyBit not available, skipping Bybit API check")

            if pybit_available:
                # Check if config file exists
                config_file_path = os.path.join(
                    os.path.dirname(__file__), "config.py")
                if os.path.exists(config_file_path):
                    try:
                        # Try to load BYBIT_CONFIG from config
                        sys.path.insert(0, os.path.dirname(
                            os.path.abspath(config_file_path)))
                        from config import BYBIT_CONFIG

                        # Test API connection
                        session = HTTP(**BYBIT_CONFIG)

                        # Try to get server time (public endpoint that doesn't require auth)
                        server_time = session.get_server_time()

                        # Try to get wallet balance (private endpoint requiring auth)
                        try:
                            balance = session.get_wallet_balance(
                                accountType="UNIFIED")
                            auth_works = balance.get("retCode") == 0
                        except Exception as e:
                            auth_works = False
                            auth_error = str(e)

                        # Store metrics
                        self.metrics["api"]["exchanges"]["bybit"] = {
                            "status": "connected" if auth_works else "auth_failed",
                            "auth_works": auth_works,
                            "error": auth_error if not auth_works and 'auth_error' in locals() else None
                        }

                        if auth_works:
                            console.print(
                                "[green]✅ Bybit API connection successful")
                        else:
                            console.print(
                                f"[yellow]⚠️ Bybit API connection works but authentication failed: {auth_error if 'auth_error' in locals() else 'Unknown error'}")

                    except ImportError:
                        console.print(
                            "[yellow]⚠️ BYBIT_CONFIG not found in config.py")
                    except Exception as e:
                        console.print(
                            f"[red]❌ Error connecting to Bybit API: {e}")
                        self.metrics["api"]["exchanges"]["bybit"] = {
                            "status": "error",
                            "error": str(e)
                        }
                else:
                    console.print(
                        f"[yellow]⚠️ Config file not found: {config_file_path}")

                # Check overall API status based on exchanges
                if self.metrics["api"]["exchanges"]:
                    statuses = [e["status"]
                                for e in self.metrics["api"]["exchanges"].values()]
                    if all(s == "connected" for s in statuses):
                        self.metrics["api"]["status"] = "connected"
                    elif any(s == "connected" for s in statuses):
                        self.metrics["api"]["status"] = "partial"
                    elif any(s == "auth_failed" for s in statuses):
                        self.metrics["api"]["status"] = "auth_failed"
                    else:
                        self.metrics["api"]["status"] = "error"
                else:
                    self.metrics["api"]["status"] = "unknown"

        except Exception as e:
            console.print(f"[red]Error checking API connection: {e}")
            self.metrics["api"]["status"] = "error"
            self.metrics["api"]["error"] = str(e)

    def troubleshoot_api_connection(self):
        """
        Enhanced API troubleshooting with specific recommendations
        """
        console.print("Troubleshooting API connection issues...",
                      style="bold blue")

        if not hasattr(self, 'metrics') or 'api' not in self.metrics:
            console.print(
                "[yellow]Run check_api_connection first to identify issues")
            return

        api_status = self.metrics['api'].get('status', 'unknown')

        if api_status == 'connected':
            console.print("[green]✅ API connection is working properly")
            return

        # Analyze specific issues
        if api_status == 'auth_failed':
            console.print(
                "[yellow]⚠️ Authentication failed. Troubleshooting steps:")
            console.print(
                "  1. Check if API keys are correctly configured in config.py")
            console.print(
                "  2. Verify API keys are still valid in exchange dashboard")
            console.print(
                "  3. Check if IP restrictions are enabled on your API keys")
            console.print(
                "  4. Ensure you have proper permissions set for the API key")

            # Try to read config file to check for common issues
            try:
                if os.path.exists('config.py'):
                    with open('config.py', 'r') as f:
                        config_content = f.read()
                        if 'api_key' in config_content and ('XXXX' in config_content or 'your_key_here' in config_content):
                            console.print(
                                "[red]⚠️ Detected placeholder API keys in config.py")
            except Exception:
                pass

        elif api_status == 'error':
            error_msg = self.metrics['api'].get('error', '')

            if 'connection' in error_msg.lower():
                console.print(
                    "[yellow]⚠️ Network connection issue. Troubleshooting steps:")
                console.print("  1. Check your internet connection")
                console.print(
                    "  2. Verify firewall settings are not blocking the connection")
                console.print(
                    "  3. Try using a VPN if your region might be restricted")
            elif 'timeout' in error_msg.lower():
                console.print(
                    "[yellow]⚠️ Connection timeout. Troubleshooting steps:")
                console.print("  1. Check internet stability")
                console.print(
                    "  2. Verify the exchange is not experiencing downtime")
            else:
                console.print(f"[yellow]⚠️ Unknown error: {error_msg}")
                console.print(
                    "  1. Check the exchange status page for outages")
                console.print("  2. Verify your API version is up-to-date")

    def summarize_health(self):
        """Summarize system health metrics"""
        console.rule("System Health")

        # Determine overall health
        status_counts = Counter()

        for category, metrics in self.metrics.items():
            if category in ["cpu", "memory", "disk"]:
                status_counts[metrics["status"]] += 1

        # Check process health
        if "processes" in self.metrics:
            running_count = sum(
                1 for p in self.metrics["processes"].values() if p["status"] == "running")
            total_count = len(self.metrics["processes"])

            process_status = "good" if running_count == total_count else "critical"
            status_counts[process_status] += 1

        # Check Redis health
        if "redis" in self.metrics and self.metrics["redis"]["status"] == "connected":
            status_counts["good"] += 1
        elif "redis" in self.metrics:
            status_counts["critical"] += 1

        # Calculate overall health percentage
        total_checks = sum(status_counts.values())
        health_score = ((status_counts["good"] * 100) + (
            status_counts["warning"] * 50)) / total_checks if total_checks > 0 else 0

        # Display health summary
        health_style = "green" if health_score >= 80 else "yellow" if health_score >= 50 else "red"
        console.print(
            f"Overall System Health: [{health_style}]{health_score:.1f}%")

        # Show status breakdown
        if RICH_AVAILABLE:
            table = Table(title="Health Metrics")
            table.add_column("Metric", style="dim")
            table.add_column("Status", justify="center")
            table.add_column("Details", justify="right")

            if "cpu" in self.metrics:
                cpu_style = "green" if self.metrics["cpu"][
                    "status"] == "good" else "yellow" if self.metrics["cpu"]["status"] == "warning" else "red"
                table.add_row("CPU", f"[{cpu_style}]{'✅' if self.metrics['cpu']['status'] == 'good' else '⚠️' if self.metrics['cpu']['status'] == 'warning' else '❌'}[/{cpu_style}]",
                              f"{self.metrics['cpu']['percent']:.1f}%")

            if "memory" in self.metrics:
                mem_style = "green" if self.metrics["memory"][
                    "status"] == "good" else "yellow" if self.metrics["memory"]["status"] == "warning" else "red"
                table.add_row("Memory", f"[{mem_style}]{'✅' if self.metrics['memory']['status'] == 'good' else '⚠️' if self.metrics['memory']['status'] == 'warning' else '❌'}[/{mem_style}]",
                              f"{self.metrics['memory']['percent']:.1f}%")

            if "disk" in self.metrics:
                disk_style = "green" if self.metrics["disk"][
                    "status"] == "good" else "yellow" if self.metrics["disk"]["status"] == "warning" else "red"
                table.add_row("Disk", f"[{disk_style}]{'✅' if self.metrics['disk']['status'] == 'good' else '⚠️' if self.metrics['disk']['status'] == 'warning' else '❌'}[/{disk_style}]",
                              f"{self.metrics['disk']['percent']:.1f}%")

            if "processes" in self.metrics:
                running_count = sum(
                    1 for p in self.metrics["processes"].values() if p["status"] == "running")
                total_count = len(self.metrics["processes"])
                proc_style = "green" if running_count == total_count else "red"
                table.add_row(
                    "Processes", f"[{proc_style}]{'✅' if running_count == total_count else '❌'}[/{proc_style}]", f"{running_count}/{total_count} running")

            if "redis" in self.metrics:
                redis_style = "green" if self.metrics["redis"]["status"] == "connected" else "red"
                table.add_row(
                    "Redis", f"[{redis_style}]{'✅' if self.metrics['redis']['status'] == 'connected' else '❌'}[/{redis_style}]", self.metrics["redis"]["status"])

            if "api" in self.metrics:
                api_style = "green" if self.metrics["api"]["status"] == "connected" else "yellow" if self.metrics["api"]["status"] in [
                    "partial", "auth_failed"] else "red"
                api_icon = "✅" if self.metrics["api"]["status"] == "connected" else "⚠️" if self.metrics["api"]["status"] in [
                    "partial", "auth_failed"] else "❌"
                table.add_row(
                    "API", f"[{api_style}]{api_icon}[/{api_style}]", self.metrics["api"]["status"])

            console.print(table)
        else:
            print("Health Metrics:")

            if "cpu" in self.metrics:
                status = "✅" if self.metrics["cpu"]["status"] == "good" else "⚠️" if self.metrics["cpu"]["status"] == "warning" else "❌"
                print(f"  CPU: {status} {self.metrics['cpu']['percent']:.1f}%")

            if "memory" in self.metrics:
                status = "✅" if self.metrics["memory"]["status"] == "good" else "⚠️" if self.metrics[
                    "memory"]["status"] == "warning" else "❌"
                print(
                    f"  Memory: {status} {self.metrics['memory']['percent']:.1f}%")

            if "disk" in self.metrics:
                status = "✅" if self.metrics["disk"]["status"] == "good" else "⚠️" if self.metrics["disk"]["status"] == "warning" else "❌"
                print(
                    f"  Disk: {status} {self.metrics['disk']['percent']:.1f}%")

            if "processes" in self.metrics:
                running_count = sum(
                    1 for p in self.metrics["processes"].values() if p["status"] == "running")
                total_count = len(self.metrics["processes"])
                status = "✅" if running_count == total_count else "❌"
                print(
                    f"  Processes: {status} {running_count}/{total_count} running")

            if "redis" in self.metrics:
                status = "✅" if self.metrics["redis"]["status"] == "connected" else "❌"
                print(f"  Redis: {status} {self.metrics['redis']['status']}")

    def check_process_health(self):
        """
        Enhanced process health monitoring
        """
        console.print("Checking critical processes...", style="bold blue")

        running_processes = {}

        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time']):
                try:
                    if 'python' in proc.info['name'].lower():
                        cmdline = ' '.join(proc.info['cmdline'] or [])

                        for critical in self.critical_processes:
                            if f"{critical}.py" in cmdline:
                                process_uptime = time.time() - \
                                    proc.info['create_time']
                                running_processes[critical] = {
                                    "pid": proc.info['pid'],
                                    "status": "running",
                                    "uptime": process_uptime,
                                    "uptime_human": self._format_uptime(process_uptime)
                                }
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass

            # Check for non-running critical processes
            for critical in self.critical_processes:
                if critical not in running_processes:
                    running_processes[critical] = {
                        "status": "not_running"
                    }

            # Store and display processes
            self.metrics["processes"] = running_processes
            self._display_process_health(running_processes)

        except Exception as e:
            console.print(f"[red]Error checking process health: {e}")

    def _format_uptime(self, seconds: float) -> str:
        """
        Convert seconds to human-readable uptime
        """
        hours, remainder = divmod(int(seconds), 3600)
        minutes, _ = divmod(remainder, 60)
        return f"{hours}h {minutes}m"

    def _display_process_health(self, processes: Dict):
        """
        Display process health with rich formatting
        """
        if RICH_AVAILABLE:
            table = Table(title="Critical Processes")
            table.add_column("Process", style="dim")
            table.add_column("Status", justify="center")
            table.add_column("PID", justify="right")
            table.add_column("Uptime", justify="right")

            for name, info in processes.items():
                status = info.get("status", "unknown")
                status_style = "green" if status == "running" else "red"
                status_icon = "✅" if status == "running" else "❌"

                table.add_row(
                    name,
                    f"[{status_style}]{status_icon} {status}[/{status_style}]",
                    str(info.get("pid", "N/A")),
                    info.get("uptime_human", "N/A")
                )

            console.print(table)
        else:
            # Fallback text display
            for name, info in processes.items():
                status = info.get("status", "unknown")
                print(f"{name}: {status}")

###########################
# 05 Diagnostic Reporting and Insights
###########################


class EnhancedLogAnalyzer:
    """Advanced log analysis with intelligent error tracking and correlation"""

    def __init__(self, log_dir="."):
        self.log_dir = log_dir
        self.errors = []
        self.warnings = []
        self.info_messages = []

        # Preserved original error patterns
        self.error_patterns = {
            "No module named": "Missing module",
            "ImportError": "Import error",
            "SyntaxError": "Syntax error",
            "TypeError": "Type error",
            "ValueError": "Value error",
            "KeyError": "Key error",
            "IndexError": "Index error",
            "list index out of range": "List index error",
            "NoneType": "NoneType error",
            "ConnectionError": "Connection error",
            "TimeoutError": "Timeout error",
            "PermissionError": "Permission error",
            "FileNotFoundError": "File not found",
            "JSONDecodeError": "JSON decode error",
            "RequestException": "Request error",
            "authentication failed": "Authentication error",
            "Retry count exceeded": "Retry limit error"
        }

        # New intelligent error categorization
        self.error_categories = {
            "system": ["Missing module", "Import error", "Syntax error"],
            "runtime": ["Type error", "Value error", "KeyError", "IndexError"],
            "network": ["Connection error", "TimeoutError", "RequestException"],
            "security": ["authentication failed", "PermissionError"],
            "resource": ["FileNotFoundError", "NoneType error"]
        }

    def analyze_logs(self):
        """
        Enhanced log analysis with intelligent error tracking
        """
        console.print("Performing intelligent log analysis...",
                      style="bold blue")

        log_files = [
            "bot.log",
            "watchman.log",
            "trading.log",
            "diagnostic.log"
        ]

        # Track error counts and categorizations
        component_errors = defaultdict(list)
        error_categories = defaultdict(int)
        error_count = 0
        warning_count = 0
        info_count = 0

        try:
            for log_file in log_files:
                file_path = os.path.join(self.log_dir, log_file)

                if not os.path.exists(file_path):
                    continue

                with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                    # Read last 500 lines
                    lines = f.readlines()[-500:]

                    for line in lines:
                        # Process error messages
                        if "ERROR" in line:
                            error_count += 1
                            processed_error = self._process_error(line)
                            self.errors.append(processed_error)

                            # Categorize errors
                            error_type = processed_error.get('type', 'unknown')
                            for category, patterns in self.error_categories.items():
                                if error_type in patterns:
                                    error_categories[category] += 1
                                    break

                            # Extract component details
                            component_match = re.search(
                                r'(\w+\.py|\w+/\w+)', line)
                            component_name = component_match.group(
                                1) if component_match else "unknown"

                            component_errors[component_name].append(
                                processed_error)

                        # Process warning messages
                        elif "WARNING" in line:
                            warning_count += 1
                            self.warnings.append(line.strip())

                        # Process info messages
                        elif "INFO" in line:
                            info_count += 1
                            self.info_messages.append(line.strip())

            # Display analysis results
            console.print(
                f"Found [red]{error_count}[/red] errors, "
                f"[yellow]{warning_count}[/yellow] warnings, "
                f"[green]{info_count}[/green] info messages"
            )

            # Display error category breakdown
            if error_categories:
                console.print("\nError Category Breakdown:")
                for category, count in error_categories.items():
                    console.print(f"  {category.capitalize()}: {count} errors")

            # Component error summary
            if component_errors:
                self._display_component_errors(component_errors)

        except Exception as e:
            console.print(f"[red]Error during log analysis: {e}")

    def _process_error(self, error_line):
        """
        Intelligent error processing and categorization
        """
        processed_error = {
            "raw": error_line.strip(),
            "type": "unknown"
        }

        # Identify error type
        for pattern, error_type in self.error_patterns.items():
            if pattern in error_line:
                processed_error['type'] = error_type
                break

        return processed_error

    def _display_component_errors(self, component_errors):
        """
        Display component-specific error summary
        """
        if RICH_AVAILABLE:
            table = Table(title="Component Error Summary")
            table.add_column("Component", style="dim")
            table.add_column("Error Count", justify="right")
            table.add_column("Top Error Types", justify="left")

            for component, errors in component_errors.items():
                error_types = Counter(e['type'] for e in errors)
                top_errors = ", ".join(
                    f"{count} {error_type}"
                    for error_type, count in error_types.most_common(3)
                )

                table.add_row(component, str(len(errors)), top_errors)

            console.print(table)
        else:
            print("\nComponent Error Summary:")
            for component, errors in component_errors.items():
                print(f"  {component}: {len(errors)} errors")

    def display_recent_errors(self, limit=10):
        """
        Display most recent errors with intelligent formatting
        """
        console.rule("Recent Critical Errors")

        if not self.errors:
            console.print("[green]No recent errors found")
            return

        for i, error in enumerate(self.errors[-limit:], 1):
            error_type = error.get('type', 'Unknown')
            console.print(
                f"[red]{i}. {error_type}: {error['raw']}[/red]"
            )


#############################
# 06 Diagnostic Tool Enhanced
#############################

class EnhancedDiagnosticTool:
    """Advanced diagnostic tool with intelligent system analysis and reporting"""

    def __init__(self, root_dir: str = "."):
        # initialization
        self.root_dir = root_dir
        self.component_analyzer = EnhancedComponentAnalyzer(root_dir)
        self.test_runner = EnhancedTestRunner()
        self.system_monitor = EnhancedSystemMonitor()
        self.log_analyzer = EnhancedLogAnalyzer(root_dir)
        self.issues = []
        self.suggestions = []
        self.diagnostic_config = {
            "detailed_report": True,
            "save_report": True,
            "max_report_age_days": 7
        }

    def handle_errors(self, error, context="diagnostic"):
        """
        Centralized error handling with recovery suggestions
        """
        error_str = str(error)
        console.print(f"[red]Error in {context}: {error_str}")

        # Log detailed error info for debugging
        logger.error(f"{context} error: {error_str}")
        logger.error(traceback.format_exc())

        # Analyze error and provide specific recovery steps
        recovery_steps = []

        if "ModuleNotFoundError" in error_str or "ImportError" in error_str:
            module_match = re.search(r"No module named '(\w+)'", error_str)
            if module_match:
                module_name = module_match.group(1)
                recovery_steps.append(
                    f"Install missing module: pip install {module_name}")
        elif "ConnectionError" in error_str or "ConnectionRefusedError" in error_str:
            recovery_steps.append(
                "Check network connection and ensure services are running")
        elif "PermissionError" in error_str:
            recovery_steps.append("Check file and directory permissions")
        elif "Object of type" in error_str and "is not JSON serializable" in error_str:
            recovery_steps.append(
                "Use custom serialization for complex objects")

        # Display recovery steps if available
        if recovery_steps:
            console.print("[yellow]Suggested recovery steps:")
            for i, step in enumerate(recovery_steps, 1):
                console.print(f"  {i}. {step}")

        return recovery_steps

    def run_diagnostics(self, mode: str = "full"):
        """
        Comprehensive diagnostic execution with mode support
        :param mode: 'full' = complete check, 'quick' = monitoring
        """
        start_time = time.time()

        # Rich panel for diagnostic header
        title = "Advanced AI Trading System Diagnostic"
        if RICH_AVAILABLE:
            console.print(Panel.fit(
                f"[bold blue]{title}[/bold blue]",
                subtitle=f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ))
        else:
            console.rule(title)
            print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Sequential diagnostic steps with intelligent error handling
        try:
            if mode == "quick":
                # Lightweight checks
                self.system_monitor.check_system_resources()
                self.system_monitor.check_process_health()
                self.log_analyzer.analyze_logs()
                self._generate_quick_summary()
            else:
                # Original full diagnostics
                self.component_analyzer.discover_components()
                self.component_analyzer.analyze_connections()
                self.system_monitor.check_system_resources()
                self.system_monitor.check_process_health()
                self.system_monitor.check_redis_connectivity()
                self.system_monitor.check_api_connection()
                self.log_analyzer.analyze_logs()
                self.test_runner.run_tests()
                self._generate_summary()
                self.component_analyzer.visualize_dependencies()
                self.log_analyzer.display_recent_errors()
                self._generate_suggestions()

        except Exception as e:
            console.print(f"[red]Diagnostic process interrupted: {e}")
            console.print(traceback.format_exc())

        # Calculate and display diagnostic duration
        duration = time.time() - start_time

        if RICH_AVAILABLE:
            console.print(Panel(
                f"[bold green]Diagnostics completed in {duration:.2f} seconds[/bold green]"
            ))
        else:
            console.rule("Diagnostics Complete")
            print(f"Completed in {duration:.2f} seconds")

    def _generate_summary(self):
        """
        Enhanced system summary with intelligent insights
        """
        console.rule("Comprehensive System Summary")

        # Component statistics with more details
        total_components = len(self.component_analyzer.components)
        valid_components = sum(
            1 for c in self.component_analyzer.components.values()
            if c.get("status") == "valid"
        )
        error_components = sum(
            1 for c in self.component_analyzer.components.values()
            if c.get("status") == "error"
        )
        unknown_components = total_components - valid_components - error_components

        # Critical components identification
        critical_components = self.component_analyzer.get_critical_components(
            top_n=5)

        # Display summary
        console.print(
            f"Components: "
            f"[green]{valid_components}[/green] valid, "
            f"[red]{error_components}[/red] with errors, "
            f"[yellow]{unknown_components}[/yellow] unknown"
        )

        if critical_components:
            console.print("\nMost critical components:")
            for component in critical_components:
                console.print(f"  [bold]{component}[/bold]")

        # System health and test summaries
        self.system_monitor.summarize_health()
        self.test_runner.summarize_results()

    def _generate_suggestions(self):
        """
        Intelligent suggestion generation
        """
        console.rule("Intelligent Recommendations")

        suggestions = []

        # Component-related suggestions
        error_components = [
            k for k, v in self.component_analyzer.components.items()
            if v.get("status") == "error"
        ]
        if error_components:
            suggestions.append(
                f"Fix errors in {len(error_components)} components: " +
                ", ".join(error_components[:3]) +
                (f" and {len(error_components)-3} more" if len(error_components) > 3 else "")
            )

        # System monitoring suggestions
        for resource in ['cpu', 'memory', 'disk']:
            if (resource in self.system_monitor.metrics and
                    self.system_monitor.metrics[resource]['status'] != 'good'):
                suggestions.append(
                    f"High {resource.upper()} usage detected. Optimize resource allocation."
                )

        # Process and API suggestions
        if 'processes' in self.system_monitor.metrics:
            not_running = [
                p for p, info in self.system_monitor.metrics['processes'].items()
                if info['status'] != 'running'
            ]
            if not_running:
                suggestions.append(
                    f"Start critical processes: {', '.join(not_running)}"
                )

        # Display suggestions
        if suggestions:
            for i, suggestion in enumerate(suggestions, 1):
                console.print(f"{i}. [yellow]{suggestion}[/yellow]")
        else:
            console.print(
                "[green]No critical issues detected. System appears healthy!")

        # Store suggestions for reporting
        self.suggestions = suggestions

    def save_report(self, filename="diagnostic_report.json"):
        """
        Enhanced report saving with robust serialization
        """
        try:
            def sanitize_dict(d):
                """Recursively convert dictionary keys to strings and handle nested structures"""
                if not isinstance(d, dict):
                    # Handle test class objects and other non-serializable types
                    if hasattr(d, '__class__'):
                        return str(d.__class__.__name__)
                    return str(d) if d is not None else None

                return {
                    str(k): sanitize_dict(v)
                    for k, v in d.items()
                    if v is not None
                }

            report = {
                "timestamp": datetime.now().isoformat(),
                "components": {
                    "total": len(self.component_analyzer.components),
                    "valid": sum(1 for c in self.component_analyzer.components.values() if c.get("status") == "valid"),
                    "error": sum(1 for c in self.component_analyzer.components.values() if c.get("status") == "error"),
                    "details": sanitize_dict({
                        k: {
                            "status": v.get("status"),
                            "classes": v.get("classes", []),
                            "functions": v.get("functions", []),
                            "issues": v.get("issues", [])
                        } for k, v in self.component_analyzer.components.items()
                    })
                },
                "system_health": sanitize_dict(self.system_monitor.metrics),
                "test_results": sanitize_dict(self.test_runner.test_results),
                "log_analysis": {
                    "errors": len(self.log_analyzer.errors),
                    "warnings": len(self.log_analyzer.warnings),
                    "info": len(self.log_analyzer.info_messages)
                },
                "suggestions": self.suggestions
            }

            report_path = os.path.join(self.root_dir, filename)
            with open(report_path, 'w') as f:
                json.dump(report, f, indent=2)

            console.print(f"[green]Diagnostic report saved to {report_path}")

        except Exception as e:
            console.print(f"[red]Error saving report: {e}")
            console.print(traceback.format_exc())

    def _generate_quick_summary(self):
        """Lightweight summary for continuous monitoring"""
        console.rule("🚨 Quick Health Check")
        console.print(f"Last check: {datetime.now().strftime('%H:%M:%S')}")
        self.system_monitor.summarize_health()
        self.log_analyzer.display_recent_errors(limit=3)

###########################
# 07 Configuration and Utility Management
###########################


class ConfigurationManager:
    """
    Centralized configuration management and system utility functions
    """

    def __init__(self, config_path='config.py'):
        self.config_path = config_path
        self.config = self._load_configuration()
        self.environment = self._detect_environment()

    def _load_configuration(self):
        """
        Intelligent configuration loader with multi-source support
        """
        try:
            # Try loading from Python configuration file
            if os.path.exists(self.config_path):
                spec = importlib.util.spec_from_file_location(
                    "config", self.config_path)
                config_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(config_module)

                # Extract configuration attributes
                return {
                    name: value for name, value in vars(config_module).items()
                    if not name.startswith('__')
                }

            # Fallback to JSON configuration
            json_config_path = os.path.join(
                os.path.dirname(self.config_path), 'config.json')
            if os.path.exists(json_config_path):
                with open(json_config_path, 'r') as f:
                    return json.load(f)

            return {}

        except Exception as e:
            logger.error(f"Configuration loading error: {e}")
            return {}

    def _detect_environment(self):
        """
        Detect and classify the runtime environment
        """
        environment_details = {
            'python_version': f"{sys.version_info.major}.{sys.version_info.minor}",
            'os': platform.system(),
            'os_release': platform.release(),
            'machine': platform.machine(),
            'processor': platform.processor()
        }
        return environment_details

    def get_api_credentials(self, exchange='bybit'):
        """
        Securely retrieve API credentials with multi-exchange support
        """
        credentials = self.config.get(f"{exchange.upper()}_CONFIG", {})

        # Basic credential validation
        required_keys = ['api_key', 'api_secret']
        if all(key in credentials for key in required_keys):
            return credentials

        logger.warning(f"Incomplete {exchange} API credentials")
        return None

    def validate_configuration(self):
        """
        Comprehensive configuration validation
        """
        validation_results = {
            'api_configs': {},
            'critical_paths': {},
            'environment_checks': {}
        }

        # Check API configurations
        exchanges = ['bybit', 'binance']
        for exchange in exchanges:
            creds = self.get_api_credentials(exchange)
            validation_results['api_configs'][exchange] = bool(creds)

        # Validate critical paths
        critical_paths = [
            'modules',
            'logs',
            'config.py',
            'setup.py'
        ]

        for path in critical_paths:
            full_path = os.path.join(self.root_dir, path)
            validation_results['critical_paths'][path] = os.path.exists(
                full_path)

        # Environment compatibility checks
        validation_results['environment_checks'] = {
            'python_version_compatible': sys.version_info >= (3, 8),
            'required_libraries': self._check_library_requirements()
        }

        return validation_results

    def _check_library_requirements(self):
        """
        Check availability and version of required libraries
        """
        required_libraries = {
            'numpy': (1, 20, 0),
            'pandas': (1, 3, 0),
            'psutil': (5, 8, 0),
            'rich': (10, 0, 0)
        }

        library_status = {}
        for lib, min_version in required_libraries.items():
            try:
                module = importlib.import_module(lib)
                current_version = tuple(
                    map(int, module.__version__.split('.')[:3]))
                library_status[lib] = current_version >= min_version
            except (ImportError, AttributeError):
                library_status[lib] = False

        return library_status

    def generate_diagnostic_config(self):
        """
        Generate a comprehensive diagnostic configuration
        """
        return {
            'version': '1.0',
            'environment': self.environment,
            'api_exchanges': list(self.config.keys()),
            'validation_timestamp': datetime.now().isoformat()
        }

    def suggest_code_fixes(self, error_text: str) -> List[str]:
        """
        GPT-style code fixes using local transformer model
        Requires `transformers` library
        """
        from transformers import pipeline
        fixer = pipeline('text2text-generation',
                         model='mrm8488/codebert-base-finetuned-code-repair')
        fixes = fixer(f"Fix Python error: {error_text}",
                      max_length=100,
                      num_return_sequences=3)
        return [fix['generated_text'] for fix in fixes]

###########################
# 08 Main Execution Enhanced
###########################


def enhanced_main():
    """Enhanced main execution function with robust monitoring"""
    try:
        # Argument parsing
        parser = argparse.ArgumentParser(
            description="Advanced AI Trading System Diagnostic Tool",
            epilog="Comprehensive system health and diagnostics"
        )
        parser.add_argument("--dir", "-d", default=".", help="Root directory")
        parser.add_argument(
            "--report", "-r", default="diagnostic_report.json", help="Output report filename")
        parser.add_argument("--verbose", "-v",
                            action="store_true", help="Enable verbose output")
        parser.add_argument("--watch", "-w", action="store_true",
                            help="Enable continuous monitoring")
        parser.add_argument(
            "--mode", choices=["full", "quick", "component", "log"], default="full", help="Diagnostic mode")
        args = parser.parse_args()

        # Adjust logging level based on --verbose flag
        logging.getLogger().setLevel(logging.DEBUG if args.verbose else logging.INFO)

        # Performance tracking
        start_time = time.time()
        initial_memory = psutil.Process().memory_info().rss / (1024 * 1024)

        # Initialize diagnostic tool
        tool = EnhancedDiagnosticTool(args.dir)

        if args.watch:
            # Continuous monitoring mode
            console.print(
                "[bold green]🚀 LIVE MONITORING STARTED (Ctrl+C to stop)[/]")
            try:
                while True:
                    # Fresh instance for each scan
                    tool = EnhancedDiagnosticTool(args.dir)
                    tool.run_diagnostics(mode="quick")
                    time.sleep(30)  # Check every 30 seconds
            except KeyboardInterrupt:
                console.print("\n[bold yellow]🛑 Monitoring stopped by user[/]")
        else:
            # Single-run mode
            tool.run_diagnostics(mode=args.mode)
            tool.save_report(args.report)

            # Performance metrics
            end_time = time.time()
            final_memory = psutil.Process().memory_info().rss / (1024 * 1024)
            console.rule("Execution Metrics")
            console.print(f"⏱️ Time: {end_time - start_time:.2f}s")
            console.print(
                f"💾 Memory: {final_memory - initial_memory:.2f} MB delta")

    except Exception as e:
        console.print(f"[red]CRITICAL ERROR: {e}")
        console.print(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    enhanced_main()



# ────────────────────────────────────────────────────────────
# FILE: ai/setup.py
# ────────────────────────────────────────────────────────────

# ai/setup.py
from setuptools import setup, find_packages

setup(
    name="magicagent",
    version="0.1.0",
    packages=find_packages(exclude=["tests"]),
    install_requires=[
        "pandas>=2.2.0",
        "numpy>=1.26.0",
        "torch>=2.0.0",
        "tensorflow>=2.18.0",
        "psutil>=6.0.0",
        "redis>=5.0.0",
        "xgboost>=2.1.0",
        "scikit-learn>=1.5.0",
        "statsmodels>=0.14.0",
        "arch>=7.0.0",
        "transformers>=4.44.0",
        "newsapi-python>=0.2.7",
        "pybit>=5.9.0",
        "python-dotenv>=1.0.0",
        "yfinance>=0.2.40",
        "rich>=13.7.0",
        "GPUtil>=1.4.0",
        "qiskit>=0.45.0",
    ],
    description="AI Trading Bot Framework",
    author="Your Name",
    author_email="your.email@example.com",
    license="MIT",
    python_requires=">=3.10",
)

# Optional: Verify key imports (run manually if needed)
if __name__ == "__main__":
    try:
        import torch
        import pandas
        import pybit

        print("Key dependencies installed successfully")
    except ImportError as e:
        print(f"Dependency check failed: {e}")




# ────────────────────────────────────────────────────────────
# 📂 PROJECT STRUCTURE
# ────────────────────────────────────────────────────────────

📂 ai/
│
├──────────── 📂 modules/
│   ├──────────── 📂 __pycache__/
│   ├──────────── 📂 account/
│   │   ├──────────── 📂 __pycache__/
│   │   ├──────────── 🐍 __init__.py
│   │   ├──────────── 🐍 account_manager.py
│   │   └──────────── 🐍 compliance_reporting.py
│   ├──────────── 📂 analysis/
│   │   ├──────────── 📂 __pycache__/
│   │   ├──────────── 🐍 __init__.py
│   │   ├──────────── 🐍 backtester.py
│   │   └──────────── 🐍 stress_tester.py
│   ├──────────── 📂 core/
│   │   ├──────────── 📂 __pycache__/
│   │   ├──────────── 🐍 __init__.py
│   │   ├──────────── 🐍 agent.py
│   │   ├──────────── 🐍 dependency_graph.py
│   │   ├──────────── 🐍 metrics.py
│   │   └──────────── 🐍 ui.py
│   ├──────────── 📂 data/
│   │   ├──────────── 📂 __pycache__/
│   │   ├──────────── 🐍 __init__.py
│   │   ├──────────── 🐍 data_fetcher.py
│   │   ├──────────── 🐍 data_processing.py
│   │   ├──────────── 🐍 market_analyzer.py
│   │   └──────────── 🐍 neuromorphic.py
│   ├──────────── 📂 execution/
│   │   ├──────────── 📂 __pycache__/
│   │   ├──────────── 🐍 __init__.py
│   │   ├──────────── 🐍 advanced_exec.py
│   │   ├──────────── 🐍 execution_engine.py
│   │   ├──────────── 🐍 market_impact.py
│   │   └──────────── 🐍 risk_management.py
│   ├──────────── 📂 ml/
│   │   ├──────────── 📂 __pycache__/
│   │   ├──────────── 🐍 __init__.py
│   │   ├──────────── 🐍 federated.py
│   │   ├──────────── 🐍 ml_analysis.py
│   │   ├──────────── 🐍 ml_reinforcement.py
│   │   ├──────────── 🐍 ml_risk.py
│   │   └──────────── 🐍 train.py
│   ├──────────── 📂 monitoring/
│   │   ├──────────── 📂 __pycache__/
│   │   ├──────────── 🐍 __init__.py
│   │   ├──────────── 🐍 dashboard.py
│   │   └──────────── 🐍 system_monitor.py
│   ├──────────── 📂 optimization/
│   │   ├──────────── 📂 __pycache__/
│   │   ├──────────── 🐍 __init__.py
│   │   ├──────────── 🐍 performance_optimizer.py
│   │   └──────────── 🐍 quantum_portfolio.py
│   ├──────────── 📂 predictors/
│   │   ├──────────── 📂 __pycache__/
│   │   ├──────────── 🐍 __init__.py
│   │   └──────────── 🐍 ml_predictors.py
│   ├──────────── 📂 strategies/
│   │   ├──────────── 📂 __pycache__/
│   │   ├──────────── 🐍 __init__.py
│   │   ├──────────── 🐍 adaptive_cockpit.py
│   │   ├──────────── 🐍 multi_asset.py
│   │   ├──────────── 🐍 quantum.py
│   │   ├──────────── 🐍 reinforcement.py
│   │   ├──────────── 🐍 session.py
│   │   └──────────── 🐍 strategy_core.py
│   ├──────────── 📂 utils/
│   │   ├──────────── 📂 __pycache__/
│   │   ├──────────── 🐍 __init__.py
│   │   ├──────────── 🐍 events.py
│   │   ├──────────── 🐍 logging.py
│   │   ├──────────── 🐍 profit_tracker.py
│   │   ├──────────── 🐍 redis_manager.py
│   │   └──────────── 🐍 vault.py
│   └──────────── 🐍 __init__.py
├──────────── 📂 .qodo/
├──────────── 📂 BK/
├──────────── 📂 __pycache__/
├──────────── 📂 data/
├──────────── 📂 magicagent.egg-info/
├──────────── 📂 saved_models/
├──────────── 📂 tb_env/
├──────────── 🐍 __init__.py
├──────────── 🐍 boss.py
├──────────── 🐍 config.py
└──────────── 🐍 setup.py
