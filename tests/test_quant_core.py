"""核心单元测试：风险间距、校准事件、因果分位、共享触达、契约 plan。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from emquote.calibrate import calibrate_condition_orders, calibrate_multi, scan_parameter_stability
from emquote.client import EastMoneyClient
from emquote.energy import compute_kinetic_energy
from emquote.levels import compute_channel_width, relative_width_pct
from emquote.plan import SECTION_ORDER, build_condition_plan
from emquote.risk import (
    atr,
    check_limit_constraints,
    min_stop_gap,
    net_risk_reward,
    position_size,
)
from emquote.touch import build_rails, recent_decision_preview, simulate_touch


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

    def test_position_lots(self) -> None:
        pos = position_size(capital=100_000, risk_pct=0.01, buy=10.0, stop=9.5)
        self.assertGreaterEqual(pos["shares"], 100)
        self.assertEqual(pos["shares"] % 100, 0)
        self.assertTrue(pos["feasible"])

    def test_limit_flags_structure(self) -> None:
        lim = check_limit_constraints(
            buy=10.0, sell=10.5, stop=9.5, ref_close=10.0, symbol="603606.SH", name="东方电缆"
        )
        self.assertIn("limit_up", lim)
        self.assertTrue(lim["ok"])
        bad = check_limit_constraints(
            buy=10.95, sell=11.0, stop=9.0, ref_close=10.0, symbol="603606.SH"
        )
        self.assertFalse(bad["ok"])
        self.assertTrue(any("涨停" in f for f in bad["flags"]))


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


class TouchEngineTests(unittest.TestCase):
    def test_build_rails_and_simulate(self) -> None:
        k = _sample_kline()
        bars = k["bars"]
        rails = build_rails(bars[-96:], kind="vwreg", channel_width=2.0, interval="5m")
        self.assertGreater(rails["buy"], rails["stop"])
        self.assertGreater(rails["sell"], rails["buy"])
        t = len(bars) - 50
        out = simulate_touch(bars, t, rails, horizon=48)
        self.assertIn(out, {"tp_first", "stop_first", "expire_unfilled", "timeout_after_fill"})

    def test_same_bar_stop_priority(self) -> None:
        bars = [
            {"open": 10, "high": 10, "low": 10, "close": 10},
            {"open": 10, "high": 10.2, "low": 9.8, "close": 10},
            {"open": 10, "high": 11.0, "low": 9.0, "close": 10},
        ]
        rails = {"buy_low": 9.9, "buy_high": 10.1, "sell": 10.5, "stop": 9.5}
        self.assertEqual(simulate_touch(bars, 0, rails, horizon=5), "stop_first")

    def test_recent_preview(self) -> None:
        k = _sample_kline()
        prev = recent_decision_preview(
            k["bars"],
            kind="vwreg",
            channel_width=2.0,
            interval="5m",
            lookback=96,
            horizon=48,
        )
        self.assertTrue(prev.get("ok"))
        self.assertEqual(prev.get("engine"), "shared_touch")


class CalibrateTests(unittest.TestCase):
    def test_calibrate_vwreg_runs(self) -> None:
        k = _sample_kline()
        out = calibrate_condition_orders(
            k, kind="vwreg", channel_window=96, validity_days=5
        )
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("engine"), "shared_touch")
        self.assertGreater(int(out.get("decisions") or 0), 0)
        self.assertIn("tp_first", out.get("counts") or {})
        rates = out.get("rates") or {}
        self.assertIn("mean", (rates.get("tp_given_fill") or {}))
        folds = out.get("folds") or {}
        self.assertIn(folds.get("badge"), {"stable", "moderate", "fragile", "insufficient", None})

    def test_calibrate_multi_suggests(self) -> None:
        k = _sample_kline()
        out = calibrate_multi(k, kinds=["vwreg", "donchian"], channel_window=96)
        self.assertIn(out.get("suggested_primary"), {"vwreg", "donchian", None})
        self.assertEqual(out.get("engine"), "shared_touch")

    def test_param_scan(self) -> None:
        k = _sample_kline()
        sc = scan_parameter_stability(k, kind="vwreg", windows=[48, 96], widths=[2.0])
        self.assertIn(sc.get("stability"), {"stable", "moderate", "fragile", "insufficient"})
        self.assertTrue(sc.get("heatmap_ascii"))
        self.assertIn("scorecard", sc)


class PlanTests(unittest.TestCase):
    def test_plan_contract_sections(self) -> None:
        k = _sample_kline()
        plan = build_condition_plan(
            k,
            channels=["vwreg", "donchian"],
            channel_window=96,
            capital=100_000,
            risk_pct=0.01,
            with_param_scan=True,
        )
        self.assertEqual(plan.get("contract_version"), "plan.v2")
        self.assertEqual(plan.get("engine"), "shared_touch")
        self.assertEqual(list(plan.get("section_order") or []), list(SECTION_ORDER))
        sections = plan.get("sections") or {}
        for key in SECTION_ORDER:
            self.assertIn(key, sections)
            self.assertIn("available", sections[key])
            self.assertIn("kind", sections[key])
        self.assertIn("risk_reward", plan)
        self.assertIn("ratio_net", plan["risk_reward"])
        self.assertIn("gap", plan["stop_loss"])
        self.assertTrue(plan.get("calibration") or plan.get("calibration_detail"))
        self.assertIn("limits", plan)
        self.assertIsNotNone(plan.get("position"))
        self.assertIn(
            "empirical_bayes",
            (plan.get("calibration_detail") or {}).get("by_kind", {}).get("vwreg") or {},
        )
        self.assertEqual(sections["rails"].get("engine"), "shared_touch")
        preview = plan.get("touch_preview") or {}
        self.assertTrue(preview.get("ok") or preview.get("reason"))


if __name__ == "__main__":
    unittest.main()
