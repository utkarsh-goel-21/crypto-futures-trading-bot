import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


LOGGER = logging.getLogger(__name__)
SPY_RESULT_MARKER = "__SPY_REGIME_JSON__="


class SpyRegimeFilter:
    """Fetch and cache the daily SPY LSTM regime from the sibling spy-predictor project."""

    def __init__(self, project_root: Path, cache_ttl_seconds: int = 24 * 60 * 60):
        self.project_root = Path(project_root).resolve()
        self.cache_ttl_seconds = cache_ttl_seconds
        self.cache_path = self.project_root / "spy_regime_cache.json"
        self._memory_cache = None

    def _find_spy_project_root(self) -> Path:
        candidates = []
        for parent in [self.project_root, *self.project_root.parents]:
            candidates.append(parent / "spy-predictor")

        for candidate in candidates:
            if (candidate / "backend" / "api" / "predict.py").exists():
                return candidate

        raise FileNotFoundError("Could not locate sibling spy-predictor project.")

    def _get_spy_python(self, spy_root: Path) -> str:
        candidates = [
            spy_root / ".venv" / "bin" / "python",
            spy_root / ".venv" / "Scripts" / "python.exe",
            Path(sys.executable),
        ]
        for candidate in candidates:
            if Path(candidate).exists():
                return str(candidate)
        raise FileNotFoundError("No Python executable found for spy-predictor integration.")

    def _load_cache(self) -> Optional[Dict[str, Any]]:
        if self._memory_cache:
            return self._memory_cache
        if not self.cache_path.exists():
            return None
        try:
            cache = json.loads(self.cache_path.read_text())
            self._memory_cache = cache
            return cache
        except Exception as exc:
            LOGGER.warning(f"Failed to read SPY regime cache: {exc}")
            return None

    def _save_cache(self, cache: Dict[str, Any]) -> None:
        self.cache_path.write_text(json.dumps(cache, indent=2))
        self._memory_cache = cache

    def _is_fresh(self, cache: Dict[str, Any]) -> bool:
        updated_at_epoch = cache.get("updated_at_epoch")
        if updated_at_epoch is None:
            return False
        return (time.time() - updated_at_epoch) < self.cache_ttl_seconds

    def _refresh_regime(self) -> Dict[str, Any]:
        spy_root = self._find_spy_project_root()
        python_exec = self._get_spy_python(spy_root)

        inline_script = """
import json
from datetime import datetime, timezone

import pandas as pd

from backend.api.main import keep_completed_daily_bars
from backend.api.predict import predict_next_day
from backend.data.fetch import fetch_spy_data
from backend.features.engineer import build_features_for_inference

raw = fetch_spy_data(period="1y", source_preference="latest")
raw = keep_completed_daily_bars(raw)
df = build_features_for_inference(raw, period="1y")
result = predict_next_day(df)
last_date = pd.Timestamp(df.index[-1])
payload = {
    "regime": "LONG_ONLY" if result["prediction"] == 1 else "SHORT_ONLY",
    "prediction": result["prediction"],
    "direction": result["direction"],
    "confidence": result["confidence"],
    "prob_up": result["prob_up"],
    "prob_down": result["prob_down"],
    "as_of_date": str(last_date.date()),
    "predicting_for": str((last_date + pd.offsets.BDay(1)).date()),
    "market_data_source": raw.attrs.get("source", "unknown"),
    "market_data_last_refreshed": raw.attrs.get("last_refreshed"),
    "latest_missing_external": df.attrs.get("latest_missing_external", []),
    "updated_at_utc": datetime.now(timezone.utc).isoformat(),
}
print("__SPY_REGIME_JSON__=" + json.dumps(payload))
"""

        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{spy_root}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(spy_root)

        completed = subprocess.run(
            [python_exec, "-c", inline_script],
            cwd=spy_root,
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )

        if completed.returncode != 0:
            raise RuntimeError(
                "SPY regime subprocess failed: "
                + (completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}")
            )

        for line in reversed(completed.stdout.splitlines()):
            if line.startswith(SPY_RESULT_MARKER):
                regime = json.loads(line[len(SPY_RESULT_MARKER):])
                regime["updated_at_epoch"] = time.time()
                regime["cache_status"] = "fresh"
                self._save_cache(regime)
                return regime

        raise RuntimeError("SPY regime subprocess did not produce a parsable result.")

    def get_regime(self, force_refresh: bool = False) -> Dict[str, Any]:
        cache = self._load_cache()
        if cache and not force_refresh and self._is_fresh(cache):
            fresh_cache = dict(cache)
            fresh_cache["cache_status"] = "cached"
            return fresh_cache

        try:
            return self._refresh_regime()
        except Exception as exc:
            if cache:
                stale_cache = dict(cache)
                stale_cache["cache_status"] = "stale"
                stale_cache["refresh_error"] = str(exc)
                LOGGER.warning(f"Using stale SPY regime cache after refresh failure: {exc}")
                self._memory_cache = stale_cache
                return stale_cache
            raise

    def is_signal_allowed(self, signal: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        try:
            regime = self.get_regime()
        except Exception as exc:
            LOGGER.error(f"SPY regime unavailable: {exc}")
            return False, None

        allowed = (
            (regime["regime"] == "LONG_ONLY" and signal == "LONG")
            or (regime["regime"] == "SHORT_ONLY" and signal == "SHORT")
        )
        return allowed, regime
