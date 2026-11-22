from datetime import timedelta


def evaluate(sales, shares, link, published_at, *, share_threshold=100):
    reasons = []
    if sales.get('scope') != 'account_listing':
        reasons.append('unknown_scope')
    if not sales.get('eligible') or not shares.get('eligible'):
        reasons.append('insufficient_metrics')
    if sales.get('delta') is None or sales['delta'] <= 0:
        reasons.append('no_sales_growth')
    if shares.get('delta') is None or shares['delta'] <= share_threshold:
        reasons.append('shares_below_threshold')
    if not sales.get('account_id') or sales.get('account_id') != shares.get('account_id') or sales.get('account_id') != link.get('account_id'):
        reasons.append('account_mismatch')
    times = [sales.get('start'), sales.get('end'), shares.get('start'), shares.get('end')]
    if any(t is None or t.tzinfo is None or t.utcoffset() is None for t in times):
        reasons.append('window_mismatch')
        return False, reasons
    start, end, video_start, video_end = times
    if (abs(start - video_start) > timedelta(hours=2) or abs(end - video_end) > timedelta(hours=2)
            or not timedelta(hours=18) <= end - start <= timedelta(hours=30)
            or not timedelta(hours=18) <= video_end - video_start <= timedelta(hours=30)):
        reasons.append('window_mismatch')
    interval_start, interval_end = min(start, video_start), max(end, video_end)
    if (link.get('relation_type') != 'attached' or link.get('verification_status') != 'confirmed'
            or link.get('valid_from') is None or link['valid_from'] > interval_start
            or (link.get('valid_to') is not None and link['valid_to'] <= interval_end)):
        reasons.append('unverified_link')
    if published_at is None or published_at > interval_start:
        reasons.append('new_video')
    return not reasons, reasons
