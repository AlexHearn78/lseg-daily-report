from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

from lseg_quant.mdu.features import FeatureVector
from lseg_quant.mdu.decision import LLMDecision, FinalTrade
from lseg_quant.mdu.execution import ExecutionResult

logger = logging.getLogger(__name__)


class AuditTrail:
    """Structured audit log for every MDU run.

    Writes JSON records and a human-readable log file.
    """

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.run_ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.records: list[dict] = []

    def record_data_snapshot(self, label: str, data: object) -> None:
        self.records.append({
            "type": "data_snapshot",
            "label": label,
            "timestamp": dt.datetime.now().isoformat(),
            "data": data if isinstance(data, (dict, list)) else str(data),
        })

    def record_feature_vector(self, features: FeatureVector) -> None:
        self.records.append({
            "type": "feature_vector",
            "timestamp": dt.datetime.now().isoformat(),
            "features": features,
        })

    def record_llm_decision(self, decision: LLMDecision, raw_prompt: str, raw_response: str) -> None:
        self.records.append({
            "type": "llm_decision",
            "timestamp": dt.datetime.now().isoformat(),
            "prompt": raw_prompt,
            "raw_response": raw_response,
            "parsed": {
                "action": decision.action,
                "confidence": decision.confidence,
                "allocation": decision.allocation,
                "reasoning": decision.reasoning,
                "risk_flags": decision.risk_flags,
            },
        })

    def record_final_trade(self, trade: FinalTrade) -> None:
        self.records.append({
            "type": "final_trade",
            "timestamp": dt.datetime.now().isoformat(),
            "action": trade.action,
            "confidence": trade.confidence,
            "allocation": trade.allocation,
            "reasoning": trade.reasoning,
            "risk_flags": trade.risk_flags,
            "risk_overrides": trade.risk_overrides,
        })

    def record_execution(self, result: ExecutionResult) -> None:
        self.records.append({
            "type": "execution",
            "timestamp": dt.datetime.now().isoformat(),
            "success": result.success,
            "message": result.message,
            "simulated_value": result.simulated_value,
            "order_id": result.order_id,
        })

    def write(self) -> Path:
        """Write all records to JSON and return path."""
        path = self.run_dir / f"audit_{self.run_ts}.jsonl"
        with open(path, "w") as f:
            for record in self.records:
                f.write(json.dumps(record) + "\n")
        logger.info("Audit trail written to %s", path)
        return path

    def write_summary(self) -> Path:
        """Write a human-readable summary."""
        path = self.run_dir / f"summary_{self.run_ts}.txt"
        lines: list[str] = [
            "=" * 60,
            f"MDU Run — {self.run_ts}",
            "=" * 60,
            "",
        ]
        for r in self.records:
            t = r.get("type", "?")
            if t == "feature_vector":
                lines.append("FEATURE VECTOR:")
                for k, v in r.get("features", {}).items():
                    lines.append(f"  {k}: {v}")
                lines.append("")
            elif t == "final_trade":
                lines.append("FINAL TRADE DECISION:")
                lines.append(f"  Action:     {r.get('action')}")
                lines.append(f"  Confidence: {r.get('confidence'):.2f}")
                lines.append(f"  Allocation: {r.get('allocation'):.2%}")
                lines.append(f"  Overrides:  {', '.join(r.get('risk_overrides', [])) or 'none'}")
                lines.append("  Reasoning:")
                for b in r.get("reasoning", []):
                    lines.append(f"    • {b}")
                lines.append("")
            elif t == "execution":
                lines.append("EXECUTION:")
                lines.append(f"  Success: {r.get('success')}")
                lines.append(f"  Message: {r.get('message')}")
                lines.append("")

        with open(path, "w") as f:
            f.write("\n".join(lines))
        logger.info("Summary written to %s", path)
        return path
