"""条件单日记：保存 plan 快照，list / 对照现价。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from emquote.client import EastMoneyClient
from emquote.journal import (
    build_snapshot,
    classify_price,
    diff_snapshot,
    forecast_from_plan,
    list_snapshots,
    resolve_snapshot,
    save_snapshot,
    slim_plan,
)
from emquote.plan import build_condition_plan


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample-603606-5m.json"
CST = timezone(timedelta(hours=8))


def _sample_kline(days: int = 10) -> dict:
    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
    return EastMoneyClient.kline_from_payload(payload, days=days)


def _plan() -> dict:
    return build_condition_plan(
        _sample_kline(),
        channels=["vwreg", "donchian"],
        channel_window=96,
        run_calibration=False,
    )


class JournalTests(unittest.TestCase):
    def test_slim_drops_heavy_keys(self) -> None:
        plan = {"last_close": 1, "calibration_detail": {"x": 1}, "levels_report": {"y": 2}}
        slim = slim_plan(plan)
        self.assertNotIn("calibration_detail", slim)
        self.assertNotIn("levels_report", slim)
        self.assertEqual(slim_plan(plan, full=True)["calibration_detail"], {"x": 1})

    def test_save_list_diff_roundtrip(self) -> None:
        plan = _plan()
        fc = forecast_from_plan(plan)
        self.assertIsNotNone(fc.get("buy_low"))
        self.assertIsNotNone(fc.get("sell"))
        saved_at = datetime(2026, 9, 14, 15, 0, tzinfo=CST)
        snap = build_snapshot(plan=plan, note="观察池复盘", saved_at=saved_at)
        self.assertEqual(snap["kind"], "emquote.journal.v1")
        self.assertIn("603606.SH", snap["id"])
        self.assertNotIn("calibration_detail", snap["plan"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = save_snapshot(snap, root=root)
            self.assertTrue(path.exists())
            rows = list_snapshots("603606.SH", root=root)
            self.assertEqual(len(rows), 1)
            loaded = resolve_snapshot("603606.SH", root=root)
            self.assertEqual(loaded["stamp"], snap["stamp"])

            last = float(fc["last_close"])
            report = diff_snapshot(loaded, last=last, last_time="2026-09-14 15:00:00")
            self.assertEqual(report["change"], 0.0)
            self.assertIn(report["vs_current"]["vs_rails"], {
                "above_buy_zone",
                "inside_buy_zone",
                "below_buy_zone",
                "at_or_above_take_profit",
                "at_or_below_stop",
            })

            cheaper = last * 0.5
            low = diff_snapshot(loaded, last=cheaper, last_time="2026-09-17 15:00:00")
            self.assertLess(low["change"], 0)
            self.assertEqual(low["calendar_days"], 3)

    def test_classify_rails(self) -> None:
        fc = {"buy_low": 10.0, "buy_high": 10.5, "sell": 12.0, "stop": 9.5}
        self.assertEqual(classify_price(10.2, fc)["vs_rails"], "inside_buy_zone")
        self.assertEqual(classify_price(11.0, fc)["vs_rails"], "above_buy_zone")
        self.assertEqual(classify_price(12.1, fc)["vs_rails"], "at_or_above_take_profit")
        self.assertEqual(classify_price(9.4, fc)["vs_rails"], "at_or_below_stop")

    def test_path_replay_unfilled(self) -> None:
        plan = _plan()
        snap = build_snapshot(plan=plan, saved_at=datetime(2026, 9, 10, 15, 0, tzinfo=CST))
        fc = snap["forecast"]
        buy_high = float(fc["buy_high"])
        bars = [
            {
                "time": "2026-09-11 10:00:00",
                "open": buy_high + 1,
                "high": buy_high + 1.2,
                "low": buy_high + 0.8,
                "close": buy_high + 1,
                "volume": 100,
            }
        ]
        report = diff_snapshot(snap, last=buy_high + 1, last_time="2026-09-11 10:00:00", bars=bars)
        self.assertTrue((report.get("path") or {}).get("ok"))
        self.assertEqual(report["path"]["outcome"], "expire_unfilled")


if __name__ == "__main__":
    unittest.main()
