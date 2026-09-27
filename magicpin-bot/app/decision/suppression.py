"""Time-aware suppression engine and frequency capping."""

import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

DEFAULT_SUPPRESSION_WINDOW_SECONDS = 7 * 86400  # 7 days


def _parse_suppression_dt(val: Any) -> Optional[datetime]:
    """Parses various datetime representations into UTC-aware datetime."""
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
        try:
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def infer_frequency_window(key: Optional[str]) -> Optional[int]:
    """
    Infers frequency window in seconds from a suppression key format:
    - ISO Week bucket (:YYYY-Www): 7 days (604,800s)
    - Date bucket (:YYYY-MM-DD): 1 day (86,400s)
    - Month bucket (:YYYY-MM): 30 days (2,592,000s)
    - Quarter bucket (:YYYY-Q#): 90 days (7,776,000s)
    - Year bucket (:YYYY): 365 days (31,536,000s)
    - Cadence (:6mo, :3mo, :1mo, :14d, :7d, :24h): parsed duration
    """
    if not key or not isinstance(key, str):
        return None

    k = key.strip()

    # 1. Week bucket, e.g., 2026-W17
    if re.search(r'\b\d{4}-W\d{1,2}\b', k, re.IGNORECASE):
        return 7 * 86400

    # 2. Date bucket, e.g., 2026-04-26
    if re.search(r'\b\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b', k):
        return 86400

    # 3. Month bucket, e.g., 2026-04 (surrounded by : or boundary)
    if re.search(r'(?::|^)\d{4}-(?:0[1-9]|1[0-2])(?::|$)', k):
        return 30 * 86400

    # 4. Quarter bucket, e.g., 2026-Q2
    if re.search(r'\b\d{4}-Q[1-4]\b', k, re.IGNORECASE):
        return 90 * 86400

    # 5. Cadence patterns, e.g., :6mo, :3mo, :14d, :7d, :24h
    cadence_match = re.search(r':(\d+)(mo|m|d|h)\b', k, re.IGNORECASE)
    if cadence_match:
        val = int(cadence_match.group(1))
        unit = cadence_match.group(2).lower()
        if unit in ("mo", "m"):
            return val * 30 * 86400
        elif unit == "d":
            return val * 86400
        elif unit == "h":
            return val * 3600

    # 6. Year bucket, e.g., :2026
    if re.search(r':(20\d{2})\b', k):
        return 365 * 86400

    return None


class SuppressionEngine:
    """
    Thread-safe, time-aware storage for active suppression keys.
    Tracks suppression key, sent time, and expiry/frequency window.
    """

    def __init__(self) -> None:
        self._records: Dict[str, Dict[str, Any]] = {}
        self._last_now: Optional[datetime] = None
        self._lock = threading.Lock()

    @property
    def _suppressed_keys(self) -> set:
        """Backwards compatibility set view."""
        with self._lock:
            return set(self._records.keys())

    def mark_suppressed(
        self,
        key: Optional[str],
        sent_at: Optional[Any] = None,
        expires_at: Optional[Any] = None,
        window_seconds: Optional[int] = None,
    ) -> None:
        """
        Marks key as suppressed with sent time and expiry/frequency window.
        """
        if not key:
            return

        with self._lock:
            sent_dt = _parse_suppression_dt(sent_at)
            has_explicit_time = (
                sent_dt is not None or expires_at is not None or window_seconds is not None
            )

            if sent_dt is None:
                sent_dt = self._last_now or datetime.now(timezone.utc)
            self._last_now = sent_dt

            # Determine frequency window
            win_s = None
            if window_seconds is not None:
                try:
                    win_s = int(window_seconds)
                except (ValueError, TypeError):
                    win_s = None

            if win_s is None:
                win_s = infer_frequency_window(key)

            # Determine expiry timestamp
            exp_dt = _parse_suppression_dt(expires_at)
            if exp_dt is None:
                if has_explicit_time:
                    if win_s is not None:
                        exp_dt = sent_dt + timedelta(seconds=win_s)
                    else:
                        win_s = DEFAULT_SUPPRESSION_WINDOW_SECONDS
                        exp_dt = sent_dt + timedelta(seconds=win_s)
                else:
                    # Legacy mark_suppressed(key) without time arguments
                    exp_dt = None

            if exp_dt and win_s is None and exp_dt >= sent_dt:
                win_s = int((exp_dt - sent_dt).total_seconds())

            self._records[key] = {
                "key": key,
                "sent_at": sent_dt,
                "expires_at": exp_dt,
                "window_seconds": win_s,
                "has_explicit_time": has_explicit_time,
            }

    def check_suppressed(self, key: Optional[str], now: Optional[Any] = None) -> bool:
        """
        Returns True if the key is currently active and suppressed at timestamp `now`.
        Returns False if unsuppressed, expired, or at/after boundary timestamp.
        """
        if not key:
            return False

        with self._lock:
            rec = self._records.get(key)
            if rec is None:
                return False

            exp_dt = rec.get("expires_at")
            if exp_dt is None:
                # Permanent / until cleared legacy suppression
                return True

            now_dt = _parse_suppression_dt(now)
            if now_dt is None:
                now_dt = self._last_now or rec["sent_at"]

            return now_dt < exp_dt

    def get_record(self, key: Optional[str]) -> Optional[Dict[str, Any]]:
        """Retrieve stored suppression record copy."""
        if not key:
            return None
        with self._lock:
            rec = self._records.get(key)
            return dict(rec) if rec else None

    def clear(self) -> None:
        """Wipes all suppression records."""
        with self._lock:
            self._records.clear()
            self._last_now = None

    def count(self) -> int:
        """Return count of stored suppression keys."""
        with self._lock:
            return len(self._records)


suppression_engine = SuppressionEngine()


def check_suppressed(key: Optional[str], now: Optional[Any] = None) -> bool:
    """Check if a suppression key is currently active at timestamp `now`."""
    return suppression_engine.check_suppressed(key, now=now)


def mark_suppressed(
    key: Optional[str],
    sent_at: Optional[Any] = None,
    expires_at: Optional[Any] = None,
    window_seconds: Optional[int] = None,
) -> None:
    """Mark a suppression key as active with sent time and frequency window."""
    suppression_engine.mark_suppressed(
        key=key,
        sent_at=sent_at,
        expires_at=expires_at,
        window_seconds=window_seconds,
    )


def get_suppression_record(key: Optional[str]) -> Optional[Dict[str, Any]]:
    """Retrieve copy of stored suppression record for key."""
    return suppression_engine.get_record(key)


__all__ = [
    "DEFAULT_SUPPRESSION_WINDOW_SECONDS",
    "SuppressionEngine",
    "suppression_engine",
    "check_suppressed",
    "mark_suppressed",
    "get_suppression_record",
    "infer_frequency_window",
    "_parse_suppression_dt",
]
