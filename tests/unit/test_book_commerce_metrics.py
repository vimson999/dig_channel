from datetime import datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src' / 'backend'))
from services.book_commerce.metrics import daily_delta


def observation(day, value, quality='exact'):
    return {'observed_at': datetime(2026, 11, day, 12, tzinfo=timezone.utc), 'value': value, 'quality': quality, 'series': 'a'}


def test_consecutive_exact_observations_produce_delta():
    result = daily_delta(observation(17, 310), observation(18, 335))
    assert result['delta'] == 25
    assert result['eligible'] is True


def test_missing_baseline_is_unknown_not_zero():
    assert daily_delta(None, observation(18, 335))['reason'] == 'no_baseline'
    assert daily_delta(None, observation(18, 335))['delta'] is None


def test_gap_is_not_daily_growth():
    result = daily_delta(observation(16, 310), observation(18, 335))
    assert result['delta'] is None
    assert result['reason'] == 'missing_previous_day'


def test_counter_decrease_is_not_eligible_growth():
    result = daily_delta(observation(17, 335), observation(18, 310))
    assert result['signed_delta'] == -25
    assert result['delta'] is None
    assert result['reason'] == 'counter_decrease'


def test_rounded_values_are_not_used_as_exact():
    assert daily_delta(observation(17, 100), observation(18, 120, 'rounded'))['reason'] == 'imprecise_metric'


def test_short_cross_midnight_window_is_not_recommendation_eligible():
    previous = {'observed_at': datetime(2026, 11, 17, 15, 59, tzinfo=timezone.utc), 'value': 10, 'quality': 'exact', 'series': 'a'}
    current = {'observed_at': datetime(2026, 11, 17, 16, 1, tzinfo=timezone.utc), 'value': 20, 'quality': 'exact', 'series': 'a'}
    result = daily_delta(previous, current)
    assert result['delta'] == 10
    assert result['eligible'] is False
    assert result['reason'] == 'irregular_window'


def test_different_series_do_not_produce_growth():
    current = observation(18, 120)
    current['series'] = 'b'
    assert daily_delta(observation(17, 100), current)['reason'] == 'different_series'
