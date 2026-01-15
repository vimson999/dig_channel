# Google API key rotation utilities

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from typing import Iterable, List, Optional, Dict, Any


class GoogleApiKeyManager:
    """Manage multiple Google API keys with rotation and failure switching."""

    DEFAULT_ENV_VAR = "GOOGLE_API_KEYS"
    FALLBACK_ENV_VAR = "GOOGLE_API_KEY"
    DEFAULT_DELIMITER = ","

    def __init__(self, keys: Iterable[str], *, logger: Optional[logging.Logger] = None) -> None:
        self._keys = self._normalize_keys(keys)
        if not self._keys:
            raise ValueError("No Google API keys provided.")

        self._index = 0
        self._exhausted = set()
        self._lock = threading.Lock()
        self._logger = logger or logging.getLogger("google_key_manager")

        self._last_switch_reason: Optional[str] = None
        self._last_switch_time: Optional[datetime] = None

    @classmethod
    def from_env(
        cls,
        env_var: Optional[str] = None,
        *,
        fallback_var: Optional[str] = None,
        delimiter: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ) -> "GoogleApiKeyManager":
        env_var = env_var or cls.DEFAULT_ENV_VAR
        fallback_var = fallback_var or cls.FALLBACK_ENV_VAR
        delimiter = delimiter or cls.DEFAULT_DELIMITER

        raw = os.environ.get(env_var)
        if not raw:
            raw = os.environ.get(fallback_var, "")

        keys = [item.strip() for item in raw.split(delimiter)] if raw else []
        return cls(keys, logger=logger)

    @staticmethod
    def _normalize_keys(keys: Iterable[str]) -> List[str]:
        normalized: List[str] = []
        seen = set()
        for key in keys:
            if not key:
                continue
            stripped = str(key).strip()
            if not stripped or stripped in seen:
                continue
            seen.add(stripped)
            normalized.append(stripped)
        return normalized

    def get_key(self) -> str:
        """Return the current usable key."""
        with self._lock:
            next_index = self._next_available_index(self._index)
            if next_index is None:
                self._logger.error("No available Google API keys; all keys are exhausted.")
                raise RuntimeError("No available Google API keys.")

            if next_index != self._index:
                old_index = self._index
                self._index = next_index
                self._log_switch("current key exhausted", old_index, next_index, exhausted=True)

            return self._keys[self._index]

    def report_failure(self, reason: str, *, exhausted: bool = False) -> bool:
        """
        Report a failure for the current key and switch to the next available key.

        Returns True if a switch was performed, False otherwise.
        """
        with self._lock:
            old_index = self._index
            old_key = self._keys[old_index]

            if exhausted:
                self._exhausted.add(old_key)

            next_index = self._next_available_index(old_index + 1)
            if next_index is None:
                self._logger.error(
                    "Google API key switch failed: no available keys. reason=%s exhausted=%s",
                    reason,
                    exhausted,
                )
                return False

            if next_index == old_index:
                self._logger.warning(
                    "Google API key switch skipped: no alternative keys. reason=%s exhausted=%s",
                    reason,
                    exhausted,
                )
                return False

            self._index = next_index
            self._log_switch(reason, old_index, next_index, exhausted=exhausted)
            return True

    def report_quota_exceeded(self, reason: str = "quota exceeded") -> bool:
        """Mark the current key as exhausted and switch to the next available key."""
        return self.report_failure(reason, exhausted=True)

    def get_status(self) -> Dict[str, Any]:
        """Return a non-sensitive snapshot of the key manager state."""
        with self._lock:
            exhausted_indices = [
                index for index, key in enumerate(self._keys) if key in self._exhausted
            ]
            return {
                "total_keys": len(self._keys),
                "current_index": self._index,
                "exhausted_count": len(exhausted_indices),
                "exhausted_indices": exhausted_indices,
                "last_switch_reason": self._last_switch_reason,
                "last_switch_time": self._last_switch_time.isoformat()
                if self._last_switch_time
                else None,
            }

    def _next_available_index(self, start_index: int) -> Optional[int]:
        total = len(self._keys)
        if total == 0:
            return None

        for offset in range(total):
            index = (start_index + offset) % total
            if self._keys[index] not in self._exhausted:
                return index
        return None

    def _log_switch(self, reason: str, old_index: int, new_index: int, *, exhausted: bool) -> None:
        self._last_switch_reason = reason
        self._last_switch_time = datetime.utcnow()
        self._logger.warning(
            "Switching Google API key: old_index=%s new_index=%s exhausted=%s reason=%s",
            old_index,
            new_index,
            exhausted,
            reason,
        )
