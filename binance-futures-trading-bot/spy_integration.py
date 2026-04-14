import json
import logging
import os
import requests
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from runtime_config import get_spy_predictor_api_url

LOGGER = logging.getLogger(__name__)
SPY_RESULT_MARKER = "__SPY_REGIME_JSON__="
SPY_MARKET_OPEN_HOUR_NY = 9
SPY_MARKET_OPEN_MINUTE_NY = 30
SPY_STALE_RETRY_COOLDOWN_SECONDS = 15 * 60


class SpyRegimeFilter:
    """Fetch and cache the daily SPY LSTM regime from API or local sibling project."""

    def __init__(self, project_root: Path, cache_ttl_seconds: int = 24 * 60 * 60):
        self.project_root = Path(project_root).resolve()
        self.cache_ttl_seconds = cache_ttl_seconds
        self.cache_path = self.project_root / "spy_regime_cache.json"
        self._memory_cache = None
        self.file_cache_enabled = not any(
            os.getenv(name)
            for name in ("RENDER", "RENDER_SERVICE_ID", "RENDER_EXTERNAL_URL")
        )

    def _find_spy_project_root(self) -> Path:
        candidates = []
        for parent in [self.project_root, *self.project_root.parents]:
            candidates.append(parent / "spy-predictor-remote")
            candidates.append(parent / "spy-predictor")

        for candidate in candidates:
            api_main = candidate / "backend" / "api" / "main.py"
            if not api_main.exists():
                continue
            try:
                api_main_text = api_main.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "build_active_session_regime_payload" in api_main_text:
                return candidate

        raise FileNotFoundError(
            "Could not locate sibling spy-predictor project with session-regime support."
        )

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
        if not self.file_cache_enabled:
            return None
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
        self._memory_cache = cache
        if not self.file_cache_enabled:
            return
        self.cache_path.write_text(json.dumps(cache, indent=2))

    def _is_fresh(self, cache: Dict[str, Any]) -> bool:
        updated_at_epoch = cache.get("updated_at_epoch")
        if updated_at_epoch is None:
            return False
        return (time.time() - updated_at_epoch) < self.cache_ttl_seconds

    def _previous_business_day(self, day: date) -> date:
        candidate = day - timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
        return candidate

    def _ny_now(self, now: Optional[datetime] = None) -> datetime:
        """Return the current market clock in New York."""
        return (
            now.astimezone(ZoneInfo("America/New_York"))
            if now
            else datetime.now(ZoneInfo("America/New_York"))
        )

    def _session_open_gate(self, current_time: datetime) -> datetime:
        """Return the New York time when the current regular US session opens."""
        return current_time.replace(
            hour=SPY_MARKET_OPEN_HOUR_NY,
            minute=SPY_MARKET_OPEN_MINUTE_NY,
            second=0,
            microsecond=0,
        )

    def _expected_active_session_date(self, now: Optional[datetime] = None) -> date:
        ny_now = self._ny_now(now)
        current_day = ny_now.date()

        if current_day.weekday() >= 5:
            return self._previous_business_day(current_day)

        if ny_now < self._session_open_gate(ny_now):
            return self._previous_business_day(current_day)

        return current_day

    def _parse_predicting_for(self, cache: Dict[str, Any]) -> Optional[date]:
        raw_value = cache.get("predicting_for") or cache.get("active_session_date")
        if not raw_value:
            return None
        try:
            return date.fromisoformat(str(raw_value)[:10])
        except ValueError:
            return None

    def _matches_session_freshness(self, cache: Dict[str, Any], now: Optional[datetime] = None) -> bool:
        session_date = self._parse_predicting_for(cache)
        if session_date is None:
            return False

        expected_session_date = self._expected_active_session_date(now)
        return session_date == expected_session_date

    def _retry_due_for_market_staleness(self, cache: Dict[str, Any]) -> bool:
        retry_after_epoch = cache.get("market_stale_retry_after_epoch")
        if retry_after_epoch is None:
            return True
        return time.time() >= retry_after_epoch

    def _clear_market_staleness(self, cache: Dict[str, Any]) -> Dict[str, Any]:
        fresh_cache = dict(cache)
        fresh_cache.pop("market_stale_retry_after_epoch", None)
        fresh_cache.pop("market_stale_retry_after_utc", None)
        fresh_cache.pop("market_stale_warning", None)
        return fresh_cache

    def _mark_market_stale(self, cache: Dict[str, Any], warning: str) -> Dict[str, Any]:
        stale_cache = dict(cache)
        retry_after_epoch = time.time() + SPY_STALE_RETRY_COOLDOWN_SECONDS
        stale_cache["cache_status"] = "stale"
        stale_cache["market_stale_warning"] = warning
        stale_cache["market_stale_retry_after_epoch"] = retry_after_epoch
        stale_cache["market_stale_retry_after_utc"] = datetime.fromtimestamp(
            retry_after_epoch,
            tz=timezone.utc,
        ).isoformat()
        self._save_cache(stale_cache)
        return stale_cache

    def _refresh_regime_from_api(self, api_url: str) -> Dict[str, Any]:
        response = requests.get(
            f"{api_url}/session-regime",
            timeout=120,
            headers={"Connection": "close"},
        )
        response.raise_for_status()
        result = response.json()

        regime = {
            "regime": "LONG_ONLY" if result["prediction"] == 1 else "SHORT_ONLY",
            "prediction": result["prediction"],
            "direction": result["direction"],
            "confidence": result["confidence"],
            "prob_up": result["prob_up"],
            "prob_down": result["prob_down"],
            "as_of_date": result.get("as_of_date"),
            "predicting_for": result.get("predicting_for"),
            "market_data_source": result.get("market_data_source", "unknown"),
            "market_data_last_refreshed": result.get("market_data_last_refreshed"),
            "market_data_status": result.get("market_data_status"),
            "market_data_warning": result.get("market_data_warning"),
            "active_session_date": result.get("active_session_date"),
            "latest_available_as_of_date": result.get("latest_available_as_of_date"),
            "latest_available_predicting_for": result.get("latest_available_predicting_for"),
            "effective_from_ny": result.get("effective_from_ny"),
            "effective_until_ny": result.get("effective_until_ny"),
            "effective_until_utc": result.get("effective_until_utc"),
            "session_mode": result.get("session_mode", "active"),
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": "api",
            "api_url": api_url,
        }
        regime["updated_at_epoch"] = time.time()
        regime["cache_status"] = "fresh"
        self._save_cache(regime)
        return regime

    def _refresh_regime_from_local_project(self) -> Dict[str, Any]:
        spy_root = self._find_spy_project_root()
        python_exec = self._get_spy_python(spy_root)

        inline_script = """
import json
from datetime import datetime, timezone

import pandas as pd

from backend.api.main import keep_completed_daily_bars
from backend.api.main import build_active_session_regime_payload
from backend.data.fetch import fetch_spy_data
from backend.features.engineer import build_features_for_inference

raw = fetch_spy_data(period="1y", source_preference="latest")
raw = keep_completed_daily_bars(raw)
df = build_features_for_inference(raw, period="1y")
payload = build_active_session_regime_payload(raw, df)
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

    def _refresh_regime(self) -> Dict[str, Any]:
        api_url = get_spy_predictor_api_url()
        if api_url:
            return self._refresh_regime_from_api(api_url)
        return self._refresh_regime_from_local_project()

    def get_regime(self, force_refresh: bool = False) -> Dict[str, Any]:
        cache = self._load_cache()
        expected_session_date = self._expected_active_session_date()
        if cache and not force_refresh:
            if self._is_fresh(cache) and self._matches_session_freshness(cache):
                fresh_cache = self._clear_market_staleness(cache)
                fresh_cache["cache_status"] = "cached"
                fresh_cache["expected_active_session_date"] = str(expected_session_date)
                self._memory_cache = fresh_cache
                return fresh_cache

            if (
                self._is_fresh(cache)
                and not self._matches_session_freshness(cache)
                and not self._retry_due_for_market_staleness(cache)
            ):
                stale_cache = dict(cache)
                stale_cache["cache_status"] = "stale"
                stale_cache["expected_active_session_date"] = str(expected_session_date)
                return stale_cache

        try:
            refreshed = self._refresh_regime()
            refreshed["expected_active_session_date"] = str(expected_session_date)
            if self._matches_session_freshness(refreshed):
                fresh_cache = self._clear_market_staleness(refreshed)
                fresh_cache["cache_status"] = "fresh"
                self._save_cache(fresh_cache)
                return fresh_cache

            warning = (
                f"Expected active SPY session {expected_session_date}, "
                f"but source returned {refreshed.get('predicting_for')}. "
                f"Retrying after {SPY_STALE_RETRY_COOLDOWN_SECONDS // 60} minutes."
            )
            LOGGER.warning(warning)
            return self._mark_market_stale(refreshed, warning)
        except Exception as exc:
            if cache:
                stale_cache = dict(cache)
                stale_cache["cache_status"] = "stale"
                stale_cache["refresh_error"] = str(exc)
                stale_cache["expected_active_session_date"] = str(expected_session_date)
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
