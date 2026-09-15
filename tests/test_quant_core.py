"""核心单元测试：风险间距、校准事件、因果分位。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from emquote.calibrate import calibrate_condition_orders, calibrate_multi
from emquote.client import EastMoneyClient
from emquote.energy import compute_kinetic_energy
from emquote.levels import compute_channel_width, relative_width_pct
from emquote.plan import build_condition_plan
from emquote.risk import atr, min_stop_gap, net_risk_reward


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample-603606-5m.json"


def _sample_kline(days: int = 10) -> dict:
    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
    return EastMoneyClient.kline_from_payload(payload, days=days)


class RiskTests(unittest.TestCase):
    def test_atr_positive(self) -> None:
        k = _sample_kline()
        self.assertGreater(atr(k["bars"]), 0)

    def test_min_gap_uses_atr_floor(self) -> None:
        k = _sample_kline()
        last = float(k["bars"][-1]["close"])
        info = min_stop_gap(last, k["bars"])
        self.assertGreaterEqual(info["gap"], 0.05)
        self.assertTrue(info["heuristic"])

    def test_net_rr_below_gross(self) -> None:
        rr = net_risk_reward(10.0, 11.0, 9.5)
        self.assertIsNotNone(rr["ratio_gross"])
        self.assertIsNotNone(rr["ratio_net"])
        assert rr["ratio_gross"] is not None and rr["ratio_net"] is not None
        self.assertLess(rr["ratio_net"], rr["ratio_gross"])


class LevelsEnergyTests(unittest.TestCase):
    def test_relative_width(self) -> None:
        self.assertAlmostEqual(relative_width_pct(12, 8, 10), 40.0)

    def test_width_causal_fields(self) -> None:
        k = _sample_kline()
        w = compute_channel_width(k["bars"], kind="vwreg", window=96, interval="5m")
        self.assertIn("certainty", w)
        self.assertTrue((w["certainty"] or {}).get("causal"))

    def test_energy_causal(self) -> None:
        k = _sample_kline()
        e = compute_kinetic_energy(k["bars"])
        self.assertTrue((e.get("assessment") or {}).get("causal"))


class CalibrateTests(unittest.TestCase):
    def test_calibrate_vwreg_runs(self) -> None:
        k = _sample_kline()
        out = calibrate_condition_orders(
            k, kind="vwreg", channel_window=96, validity_days=5
        )
        self.assertTrue(out.get("ok"))
        self.assertGreater(int(out.get("decisions") or 0), 0)
        self.assertIn("tp_first", out.get("counts") or {})
        rates = out.get("rates") or {}
        self.assertIn("mean", (rates.get("tp_given_fill") or {}))

    def test_calibrate_multi_suggests(self) -> None:
        k = _sample_kline()
        out = calibrate_multi(k, kinds=["vwreg", "donchian"], channel_window=96)
        self.assertIn(out.get("suggested_primary"), {"vwreg", "donchian", None})


class PlanTests(unittest.TestCase):
    def test_plan_includes_calibration(self) -> None:
        k = _sample_kline()
        plan = build_condition_plan(k, channels=["vwreg", "donchian"], channel_window=96)
        self.assertIn("risk_reward", plan)
        self.assertIn("ratio_net", plan["risk_reward"])
        self.assertIn("gap", plan["stop_loss"])
        # 样本足够时应有校准摘要
        self.assertTrue(plan.get("calibration") or plan.get("calibration_detail"))


if __name__ == "__main__":
    unittest.main()
