from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src' / 'backend'))
from services.book_commerce.recommendations import evaluate


def inputs():
    start = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    sales = {'eligible': True, 'delta': 25, 'scope': 'account_listing', 'account_id': 'a', 'start': start, 'end': end}
    shares = {'eligible': True, 'delta': 180, 'account_id': 'a', 'start': start, 'end': end}
    link = {'account_id': 'a', 'relation_type': 'attached', 'verification_status': 'confirmed', 'valid_from': start - timedelta(days=1), 'valid_to': None}
    return sales, shares, link, start - timedelta(days=2)


def test_matching_windows_and_confirmed_link_are_candidates():
    assert evaluate(*inputs()) == (True, [])


def test_strict_share_threshold():
    sales, shares, link, published = inputs()
    shares['delta'] = 100
    assert evaluate(sales, shares, link, published)[0] is False


def test_different_account_cannot_be_linked():
    sales, shares, link, published = inputs()
    shares['account_id'] = 'other'
    assert 'account_mismatch' in evaluate(sales, shares, link, published)[1]


def test_link_must_cover_entire_union_of_windows():
    sales, shares, link, published = inputs()
    link['valid_from'] = sales['start'] + timedelta(seconds=1)
    assert 'unverified_link' in evaluate(sales, shares, link, published)[1]


def test_windows_more_than_two_hours_apart_are_excluded():
    sales, shares, link, published = inputs()
    shares['end'] += timedelta(hours=2, seconds=1)
    assert 'window_mismatch' in evaluate(sales, shares, link, published)[1]


def test_unknown_publication_and_platform_sales_are_excluded():
    sales, shares, link, _ = inputs()
    sales['scope'] = 'platform_listing'
    allowed, reasons = evaluate(sales, shares, link, None)
    assert not allowed
    assert 'unknown_scope' in reasons and 'new_video' in reasons


def test_half_open_link_must_extend_past_last_observation():
    sales, shares, link, published = inputs()
    link['valid_to'] = shares['end']
    assert 'unverified_link' in evaluate(sales, shares, link, published)[1]
