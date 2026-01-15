"""Channel category default CPM configuration."""
from __future__ import annotations

import json
import os
from decimal import Decimal, InvalidOperation
from typing import Dict

_DEFAULT_RAW = {
    'default': '2.5',
    '科技': '9.0',
    '转发': '6.0',
    '教育': '4.0',
    '健康管理': '4.0',
    '娱乐': '3.0',
    '反贼': '6.0',
    '野路子': '6.0',
}

_ENV_KEY = 'CHANNEL_DEFAULT_CPM_JSON'


def _load_env_overrides() -> Dict[str, str]:
    raw = os.getenv(_ENV_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        overrides: Dict[str, str] = {}
        for key, value in data.items():
            if key is None:
                continue
            overrides[str(key).strip()] = str(value)
        return overrides
    except json.JSONDecodeError:
        # Ignore invalid JSON to keep application running
        return {}


def _to_decimal_map(values: Dict[str, str]) -> Dict[str, Decimal]:
    converted: Dict[str, Decimal] = {}
    for key, raw_value in values.items():
        normalized_key = key.strip() if key else 'default'
        if not normalized_key:
            normalized_key = 'default'
        try:
            converted[normalized_key] = Decimal(str(raw_value)).quantize(Decimal('0.01'))
        except (InvalidOperation, ValueError, TypeError):
            # Skip invalid values; fallback will be applied later
            continue
    return converted


_raw_defaults = {**_DEFAULT_RAW, **_load_env_overrides()}
CHANNEL_CATEGORY_DEFAULTS: Dict[str, Decimal] = _to_decimal_map(_raw_defaults)

DEFAULT_CATEGORY_KEY = 'default'
DEFAULT_CPM = CHANNEL_CATEGORY_DEFAULTS.get(DEFAULT_CATEGORY_KEY, Decimal('2.50'))
CHANNEL_CATEGORY_DEFAULTS.setdefault(DEFAULT_CATEGORY_KEY, DEFAULT_CPM)


def get_default_cpm(category: str | None) -> Decimal:
    """Return the default CPM for a given category."""
    key = (category or '').strip()
    if not key:
        key = DEFAULT_CATEGORY_KEY
    return CHANNEL_CATEGORY_DEFAULTS.get(key, DEFAULT_CPM)


def get_all_category_defaults() -> Dict[str, Decimal]:
    """Return a copy of the category default map."""
    return dict(CHANNEL_CATEGORY_DEFAULTS)


def serialize_category_defaults() -> Dict[str, float]:
    """Return defaults as plain floats for JSON serialization."""
    return {key: float(value) for key, value in CHANNEL_CATEGORY_DEFAULTS.items()}
