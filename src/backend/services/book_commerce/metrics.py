from datetime import timedelta
from zoneinfo import ZoneInfo


BUSINESS_TIMEZONE = ZoneInfo('Asia/Shanghai')


def daily_delta(previous, current):
    result = {'delta': None, 'signed_delta': None, 'eligible': False, 'reason': None}
    if previous is None:
        result['reason'] = 'no_baseline'
        return result
    if previous['series'] != current['series']:
        result['reason'] = 'different_series'
        return result
    before = previous['observed_at']
    after = current['observed_at']
    if before.tzinfo is None or after.tzinfo is None:
        raise ValueError('observation timestamps must include a timezone')
    if after.astimezone(BUSINESS_TIMEZONE).date() - before.astimezone(BUSINESS_TIMEZONE).date() != timedelta(days=1):
        result['reason'] = 'missing_previous_day'
        return result
    if previous.get('quality') != 'exact' or current.get('quality') != 'exact':
        result['reason'] = 'imprecise_metric'
        return result
    a, b = previous.get('value'), current.get('value')
    if type(a) is not int or type(b) is not int or a < 0 or b < 0:
        result['reason'] = 'missing_metric'
        return result
    result['signed_delta'] = b - a
    if b < a:
        result['reason'] = 'counter_decrease'
        return result
    result['delta'] = b - a
    if not timedelta(hours=18) <= after - before <= timedelta(hours=30):
        result['reason'] = 'irregular_window'
        return result
    result['eligible'] = True
    return result
