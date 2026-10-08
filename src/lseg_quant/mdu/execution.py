"""Paper execution: turns the final decision into a simulated position.

The template never places orders. Each name starts from a notional 100,000
in cash and the target allocation is filled in whole shares, so the report
can show what the signal would have done.
"""
from __future__ import annotations

import logging

from lseg_quant.mdu.decision import FinalTrade

logger = logging.getLogger(__name__)

NOTIONAL = 100_000.0


class ExecutionResult:
    def __init__(self, success: bool, trade: FinalTrade, simulated_value: float,
                 message: str) -> None:
        self.success = success
        self.trade = trade
        self.simulated_value = simulated_value
        self.message = message
        self.order_id: str | None = None
        self.api_response: dict | None = None


class ExecutionEngine:
    """Simulated single-name position sized by the final allocation."""

    def __init__(self, notional: float = NOTIONAL) -> None:
        self._cash = notional
        self._shares = 0
        self._last_price = 0.0

    def get_position(self) -> dict:
        value = self._shares * self._last_price if self._last_price > 0 else 0.0
        total = self._cash + value
        return {
            "shares": self._shares,
            "cash": round(self._cash, 2),
            "position_value": round(value, 2),
            "portfolio_value": round(total, 2),
            "allocation_pct": round(value / total, 4) if total > 0 else 0.0,
        }

    def update_price(self, price: float) -> None:
        self._last_price = price

    def execute(self, trade: FinalTrade, price: float) -> ExecutionResult:
        self._last_price = price
        pos = self.get_position()
        delta_value = pos["portfolio_value"] * trade.allocation - pos["position_value"]
        delta_shares = int(delta_value / price) if price > 0 else 0
        if abs(delta_value) < 1.0 or delta_shares == 0:
            return ExecutionResult(
                success=True, trade=trade, simulated_value=pos["portfolio_value"],
                message="No action needed — delta below one share",
            )

        self._shares += delta_shares
        self._cash -= delta_shares * price
        new_pos = self.get_position()
        side = "BUY" if delta_shares > 0 else "SELL"
        msg = (
            f"[PAPER] {side} {abs(delta_shares)} shares @ {price:.2f} "
            f"| cash: {new_pos['cash']:.2f} "
            f"| alloc: {new_pos['allocation_pct']:.1%} "
            f"| total: {new_pos['portfolio_value']:.2f}"
        )
        logger.info(msg)
        return ExecutionResult(success=True, trade=trade,
                               simulated_value=new_pos["portfolio_value"], message=msg)
