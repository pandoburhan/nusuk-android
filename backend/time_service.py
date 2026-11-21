"""
Internet-synchronized UTC time service & timezone helpers (WIB UTC+7 vs KSA/Riyadh UTC+3).

Design:
- Synchronizes UTC time from reliable internet sources on startup and periodically in a
  background thread (every 20 minutes).
- Anchors synchronized UTC time to `time.monotonic()` so all runtime calls (`utc_now_ts`,
  `utc_now_naive`) are O(1), lock-light, and immune to PC host clock drift or manual changes.
- Safe offline fallback: if internet is unreachable at startup, falls back to local system UTC
  with a warning log so the backend never fails to boot.
"""
import datetime
import email.utils
import logging
import threading
import time
from typing import Dict, Any, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

TIMEZONE_MAP: Dict[str, Dict[str, Any]] = {
    "wib": {
        "key": "wib",
        "label": "WIB (UTC+7)",
        "full_label": "WIB (UTC+7 — Indonesia / Asia/Jakarta)",
        "iana": "Asia/Jakarta",
        "offset_hours": 7,
        "offset_seconds": 7 * 3600,
    },
    "ksa": {
        "key": "ksa",
        "label": "Arab Saudi (UTC+3)",
        "full_label": "Arab Saudi (UTC+3 — Riyadh / Asia/Riyadh)",
        "iana": "Asia/Riyadh",
        "offset_hours": 3,
        "offset_seconds": 3 * 3600,
    },
}

DEFAULT_TIMEZONE = "wib"


def normalize_tz(tz: Optional[str]) -> str:
    if not tz:
        return DEFAULT_TIMEZONE
    k = tz.strip().lower()
    if k in ("ksa", "sa", "riyadh", "asia/riyadh", "ast", "utc+3", "saudi"):
        return "ksa"
    return "wib"


def get_tz_meta(tz: Optional[str]) -> Dict[str, Any]:
    return TIMEZONE_MAP[normalize_tz(tz)]


class TimeService:
    def __init__(self, refresh_interval_sec: int = 1200):
        self._lock = threading.RLock()
        self._refresh_interval = max(300, refresh_interval_sec)
        self._synced: bool = False
        self._source: str = "system_local"
        self._offset_sec: float = 0.0
        self._anchor_unix: float = time.time()
        self._anchor_mono: float = time.monotonic()
        self._last_sync_unix: Optional[float] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ internet sync
    def _fetch_http_date(self, url: str, name: str, timeout: float = 3.5) -> Optional[Tuple[float, str]]:
        t0 = time.monotonic()
        resp = requests.head(url, timeout=timeout, allow_redirects=False)
        rtt = time.monotonic() - t0
        date_hdr = resp.headers.get("Date")
        if not date_hdr:
            return None
        parsed = email.utils.parsedate_to_datetime(date_hdr)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        # Compensate half round-trip latency + ~0.5s HTTP Date second truncation midpoint
        return parsed.timestamp() + (rtt / 2.0), name

    def _fetch_timeapi_io(self, timeout: float = 4.0) -> Optional[Tuple[float, str]]:
        t0 = time.monotonic()
        resp = requests.get(
            "https://timeapi.io/api/Time/current/zone?timeZone=UTC",
            timeout=timeout,
        )
        rtt = time.monotonic() - t0
        if resp.status_code != 200:
            return None
        data = resp.json()
        dt_str = data.get("dateTime")
        if not dt_str:
            return None
        # Handle variable fractional seconds returned by timeapi.io
        if "." in dt_str:
            base, frac = dt_str.split(".", 1)
            frac = "".join(ch for ch in frac if ch.isdigit())[:6].ljust(6, "0")
            dt_str = f"{base}.{frac}"
        dt = datetime.datetime.fromisoformat(dt_str).replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp() + (rtt / 2.0), "timeapi.io"

    def sync_now(self) -> bool:
        """Attempts to synchronize UTC clock from reliable internet sources."""
        providers = [
            ("cloudflare", lambda: self._fetch_http_date("https://www.cloudflare.com", "cloudflare")),
            ("google", lambda: self._fetch_http_date("https://www.google.com", "google")),
            ("timeapi.io", self._fetch_timeapi_io),
        ]
        for name, fn in providers:
            try:
                res = fn()
                if res is not None:
                    net_unix, source_name = res
                    now_mono = time.monotonic()
                    now_sys = time.time()
                    offset = net_unix - now_sys
                    with self._lock:
                        self._anchor_unix = net_unix
                        self._anchor_mono = now_mono
                        self._offset_sec = offset
                        self._synced = True
                        self._source = source_name
                        self._last_sync_unix = net_unix
                    logger.info(
                        f"Internet time synchronized via {source_name}: "
                        f"UTC={datetime.datetime.fromtimestamp(net_unix, tz=datetime.timezone.utc).isoformat()} "
                        f"(host offset: {offset:+.2f}s)"
                    )
                    return True
            except Exception as e:
                logger.debug(f"Time sync source '{name}' failed: {e}")

        logger.warning("Internet time sync unavailable; using local system UTC clock as fallback.")
        return False

    def start_background_sync(self):
        """Perform an initial sync (best-effort) and spawn periodic background refresher."""
        self.sync_now()
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._sync_loop, name="time-sync", daemon=True)
        self._thread.start()

    def stop_background_sync(self):
        self._stop_event.set()

    def _sync_loop(self):
        while not self._stop_event.wait(timeout=self._refresh_interval):
            try:
                self.sync_now()
            except Exception as e:
                logger.debug(f"Periodic time sync error: {e}")

    # ------------------------------------------------------------------ O(1) clock getters
    def utc_now_ts(self) -> float:
        """Current internet-synced UNIX timestamp (seconds since epoch, UTC+0)."""
        with self._lock:
            if self._synced:
                return self._anchor_unix + (time.monotonic() - self._anchor_mono)
        return time.time()

    def utc_now_dt(self) -> datetime.datetime:
        """Timezone-aware UTC datetime."""
        return datetime.datetime.fromtimestamp(self.utc_now_ts(), tz=datetime.timezone.utc)

    def utc_now_naive(self) -> datetime.datetime:
        """Naive UTC datetime (matches MySQL DATETIME storage convention in `nusuk_key`)."""
        return self.utc_now_dt().replace(tzinfo=None)

    def tz_now_dt(self, tz: Optional[str] = None) -> datetime.datetime:
        """Current datetime in the chosen timezone (WIB or KSA)."""
        meta = get_tz_meta(tz)
        tzinfo = datetime.timezone(datetime.timedelta(hours=meta["offset_hours"]))
        return datetime.datetime.fromtimestamp(self.utc_now_ts(), tz=tzinfo)

    def tz_day_start_utc_naive(self, tz: Optional[str] = None, days_ago: int = 0) -> datetime.datetime:
        """
        Start of calendar day (00:00:00) in `tz`, shifted back by `days_ago` days,
        returned as naive UTC datetime for index-friendly SQL queries.
        Example: days_ago=0 -> today 00:00 in tz; days_ago=6 -> 7 calendar days incl. today.
        """
        local_now = self.tz_now_dt(tz)
        local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(days=days_ago)
        return local_midnight.astimezone(datetime.timezone.utc).replace(tzinfo=None)

    def next_ksa_reset_unix_ms(self) -> int:
        """Next 00:00:00 Asia/Riyadh (UTC+3) daily Nusuk limit reset as UNIX ms."""
        ksa_now = self.tz_now_dt("ksa")
        next_mid = (ksa_now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return int(next_mid.timestamp() * 1000)

    def status_payload(self) -> Dict[str, Any]:
        ts = self.utc_now_ts()
        with self._lock:
            synced = self._synced
            source = self._source
            offset_sec = round(self._offset_sec, 3)
            last_sync = (
                datetime.datetime.fromtimestamp(self._last_sync_unix, tz=datetime.timezone.utc).isoformat()
                if self._last_sync_unix
                else None
            )
        return {
            "unix_ts": ts,
            "unix_ms": int(ts * 1000),
            "utc_iso": datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat(),
            "synced": synced,
            "source": source,
            "offset_sec": offset_sec,
            "last_sync_at": last_sync,
            "next_ksa_reset_unix_ms": self.next_ksa_reset_unix_ms(),
        }


time_service = TimeService()


def utc_now_naive() -> datetime.datetime:
    """Module-level callable suitable for SQLAlchemy Column(default=..., onupdate=...)."""
    return time_service.utc_now_naive()
