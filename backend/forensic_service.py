"""
Forensic Logging Service for Nusuk Automation Hub.
Automatically dumps UI hierarchy XML and screenshots when system dialogs, ANR,
crashes, or unexpected screen states occur, with throttling and rotation.
"""
import glob
import os
import re
import time
import logging
from typing import Optional, Tuple
from backend.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

FORENSICS_DIR = os.path.join(PROJECT_ROOT, "logs", "forensics")
MAX_FORENSIC_FILES = 80
MAX_FORENSIC_TOTAL_BYTES = 50 * 1024 * 1024  # 50 MB
DEFAULT_THROTTLE_SECONDS = 25.0


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", text or "").strip().lower()
    return re.sub(r"[-\s]+", "_", cleaned)[:40] or "unspecified"


class ForensicService:
    def __init__(self, directory: str = FORENSICS_DIR, max_files: int = MAX_FORENSIC_FILES):
        self.directory = directory
        self.max_files = max_files
        self._last_dump_time = {}  # (device_uuid, category) -> timestamp
        self._ensure_dir()

    def _ensure_dir(self):
        try:
            os.makedirs(self.directory, exist_ok=True)
        except Exception as e:
            logger.warning(f"Could not create forensics directory '{self.directory}': {e}")

    def should_dump(self, device_uuid: str, category: str, throttle_sec: float = DEFAULT_THROTTLE_SECONDS) -> bool:
        now = time.monotonic()
        key = (device_uuid or "global", category)
        last_t = self._last_dump_time.get(key, 0.0)
        if (now - last_t) < throttle_sec:
            return False
        self._last_dump_time[key] = now
        return True

    def rotate_if_needed(self):
        """Removes oldest files if file count or directory size exceeds limits."""
        try:
            entries = []
            total_size = 0
            for fname in os.listdir(self.directory):
                full_p = os.path.join(self.directory, fname)
                if os.path.isfile(full_p):
                    st = os.stat(full_p)
                    entries.append((st.st_mtime, full_p, st.st_size))
                    total_size += st.st_size

            # Sort by mtime ascending (oldest first)
            entries.sort(key=lambda x: x[0])

            # Evict if count exceeds max_files or size exceeds max_bytes
            while len(entries) > self.max_files or total_size > MAX_FORENSIC_TOTAL_BYTES:
                oldest = entries.pop(0)
                try:
                    os.remove(oldest[1])
                    total_size -= oldest[2]
                except OSError:
                    pass
        except Exception as e:
            logger.debug(f"Forensic rotation check failed: {e}")

    def dump(
        self,
        device_uuid: str,
        device_name: str,
        category: str,
        reason: str,
        xml_content: Optional[str] = None,
        screenshot_bytes: Optional[bytes] = None,
        force: bool = False,
        throttle_sec: float = DEFAULT_THROTTLE_SECONDS,
    ) -> Optional[Tuple[Optional[str], Optional[str]]]:
        """
        Dumps XML and/or screenshot PNG to logs/forensics/.
        Returns (xml_path, png_path) if dumped, or None if throttled.
        """
        if not force and not self.should_dump(device_uuid, category, throttle_sec=throttle_sec):
            return None

        self._ensure_dir()
        ts_str = time.strftime("%Y%m%d_%H%M%S")
        dev_short = (device_name or device_uuid or "dev")[:8].replace(" ", "_")
        slug_cat = _slugify(category)
        slug_reas = _slugify(reason)
        base_name = f"{ts_str}_{dev_short}_{slug_cat}_{slug_reas}"

        xml_path = None
        png_path = None

        try:
            if xml_content:
                xml_path = os.path.join(self.directory, f"{base_name}.xml")
                with open(xml_path, "w", encoding="utf-8", errors="replace") as f:
                    f.write(xml_content)

            if screenshot_bytes:
                png_path = os.path.join(self.directory, f"{base_name}.png")
                with open(png_path, "wb") as f:
                    f.write(screenshot_bytes)

            self.rotate_if_needed()
            return xml_path, png_path
        except Exception as e:
            logger.warning(f"Failed to write forensic dump for {device_name} ({reason}): {e}")
            return None


forensic_service = ForensicService()
