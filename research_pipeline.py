"""
research_pipeline.py — the daily after-close research automation.

  Download → Validate → Update Features → Update Factors → Run Backtests
    → Tear sheets + Walk-forward → Generate Reports → Update Dashboard

Each stage is timed and its status recorded; a failure in one stage is captured
(not fatal) so the rest still run and the dashboard shows exactly what happened.
The run record is written to results/pipeline_run.json (the Automation page reads
it). Reuses every existing runner — this is the orchestrator, not new analytics.

RESEARCH ONLY — reads cached/fetched data, writes results/*; places no orders.

Usage:
  python research_pipeline.py             # full chain incl. data download
  python research_pipeline.py --no-fetch  # skip download (data already fresh)
"""

from __future__ import annotations

import contextlib
import io
import json
import time
from datetime import date, datetime
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"
HISTORY_KEEP = 30   # rolling window of past runs shown on the Overview page


def _append_history(record: dict, path: Path | None = None) -> list:
    """Append a compact summary of a run to results/pipeline_history.json,
    keeping the last HISTORY_KEEP. Powers the Overview 'Research History' panel."""
    path = path or (RESULTS_DIR / "pipeline_history.json")
    hist = []
    if path.exists():
        try:
            hist = json.loads(path.read_text())
        except (ValueError, OSError):
            hist = []
    hist.append({
        "finished": record["finished"],
        "ok": record["ok"],
        "n_ok": record["n_ok"],
        "n_failed": record["n_failed"],
        "duration_s": record["duration_s"],
        "failed": [s["name"] for s in record["stages"] if s["status"] == "failed"],
    })
    hist = hist[-HISTORY_KEEP:]
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(hist, indent=2))
    return hist


def _run_stage(name: str, fn, skip: bool = False) -> dict:
    if skip:
        return {"name": name, "status": "skipped", "duration_s": 0.0}
    t0 = time.time()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            fn()
        return {"name": name, "status": "ok", "duration_s": round(time.time() - t0, 2)}
    except Exception as e:  # captured, not fatal — the chain continues
        return {"name": name, "status": "failed", "duration_s": round(time.time() - t0, 2),
                "message": f"{type(e).__name__}: {e}"}


def _build_plan(fetch: bool) -> list[tuple[str, object, bool]]:
    import shutil

    import attribution_report, backtest_lowvol, backtest_momentum, data_quality
    import factor_report, feature_store, fetch_data, market_intel, multifactor
    import portfolio_analyzer, portfolio_optimizer, research_assistant
    import research_summary, risk_engine, risk_report, tearsheet

    def backtests():
        backtest_lowvol.main()
        backtest_momentum.main()

    def reports():
        for m in (portfolio_analyzer, risk_report, attribution_report,
                  multifactor, portfolio_optimizer, risk_engine, market_intel):
            m.main()

    def update_dashboard():
        # the dashboard reads results/*.json — confirm the key ones were produced
        need = ["tearsheets.json", "factors.json", "feature_store.json",
                "data_quality.json", "risk_engine.json", "market_intel.json"]
        missing = [f for f in need if not (RESULTS_DIR / f).exists()]
        if missing:
            raise FileNotFoundError(f"dashboard artifacts missing: {missing}")

    def archive():
        # snapshot the day's analytics (JSONs + the summary) under results/archive/<date>/
        dest = RESULTS_DIR / "archive" / date.today().isoformat()
        dest.mkdir(parents=True, exist_ok=True)
        briefs = [RESULTS_DIR / "research_summary.md", RESULTS_DIR / "research_assistant.md"]
        for fp in list(RESULTS_DIR.glob("*.json")) + briefs:
            if fp.exists() and fp.name != "pipeline_history.json":
                shutil.copy2(fp, dest / fp.name)

    # Walk-forward AND Monte Carlo are both computed inside tearsheet.main() (each
    # equity strategy carries walk_forward + monte_carlo), so that one stage covers
    # both — named explicitly here.
    return [
        ("Download data",                    lambda: fetch_data.main(refresh=True), not fetch),
        ("Validate data",                    data_quality.main, False),
        ("Update features",                  feature_store.main, False),
        ("Update factors",                   factor_report.main, False),
        ("Run backtests",                    backtests, False),
        ("Tear sheets · walk-forward · Monte Carlo", tearsheet.main, False),
        ("Generate reports",                 reports, False),
        ("AI research review",               research_assistant.main, False),
        ("Generate research summary",        research_summary.main, False),
        ("Update dashboard",                 update_dashboard, False),
        ("Archive results",                  archive, False),
    ]


def run(fetch: bool = True, plan=None) -> dict:
    started = datetime.now()
    is_real = plan is None            # real chain runs are logged to history; test plans aren't
    plan = plan if plan is not None else _build_plan(fetch)
    stages = [_run_stage(name, fn, skip) for name, fn, skip in plan]
    finished = datetime.now()
    record = {
        "generated": date.today().isoformat(),
        "started": started.isoformat(timespec="seconds"),
        "finished": finished.isoformat(timespec="seconds"),
        "duration_s": round((finished - started).total_seconds(), 1),
        "stages": stages,
        "n_ok": sum(s["status"] == "ok" for s in stages),
        "n_failed": sum(s["status"] == "failed" for s in stages),
        "ok": all(s["status"] != "failed" for s in stages),
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "pipeline_run.json").write_text(json.dumps(record, indent=2))
    if is_real:
        _append_history(record)
    return record


def main(fetch: bool = True):
    print("Research pipeline — daily after-close automation\n")
    rec = run(fetch=fetch)
    for s in rec["stages"]:
        mark = {"ok": "✓", "failed": "✗", "skipped": "·"}[s["status"]]
        extra = f"  {s.get('message','')}" if s["status"] == "failed" else ""
        print(f"  {mark} {s['name']:<28} {s['status']:<8} {s['duration_s']:>5.1f}s{extra}")
    print(f"\n{'OK' if rec['ok'] else 'COMPLETED WITH FAILURES'} — "
          f"{rec['n_ok']} ok, {rec['n_failed']} failed in {rec['duration_s']}s "
          f"→ results/pipeline_run.json")
    return 0 if rec["ok"] else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(main(fetch="--no-fetch" not in sys.argv))
