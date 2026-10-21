from datetime import date, datetime, timezone

import pytest

from app.core.config import settings
from app.core.exceptions import ValidationException
from app.services import report_service, tracking_service
from app.utils import datacenter
from app.utils.bot_detect import BOT_THRESHOLD, bot_score
from app.utils.traffic_quality import (
    INTERNAL,
    KNOWN_BOT,
    LIKELY_HUMAN,
    SUSPICIOUS,
    UNKNOWN,
    classify_session,
)
from app.utils.traffic_source import classify_source
from app.utils.user_agent import parse_user_agent

CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def human(**overrides) -> dict:
    values = dict(ua_bot=False, internal=False, bot_score=0, interacted=False, engaged_ms=0, page_count=1)
    values.update(overrides)
    return values


def test_bot_score_flags_headless_and_known_bots():
    assert bot_score(CHROME) == 0
    assert bot_score("Googlebot/2.1") == 100
    assert bot_score(None) == 100
    assert bot_score(CHROME, webdriver=True) >= BOT_THRESHOLD
    assert bot_score(CHROME, has_languages=False) < BOT_THRESHOLD
    assert bot_score(CHROME, has_languages=False, screen_width=0, screen_height=0) >= BOT_THRESHOLD
    assert bot_score(CHROME, burst=True) >= BOT_THRESHOLD


def test_classification_never_claims_human_without_evidence():
    assert classify_session(**human()).traffic_class == UNKNOWN
    assert classify_session(**human(), ).confidence == "low"
    assert classify_session(**human(interacted=True)).traffic_class == LIKELY_HUMAN
    assert classify_session(**human(engaged_ms=15_000)).traffic_class == LIKELY_HUMAN
    assert classify_session(**human(page_count=3)).traffic_class == LIKELY_HUMAN
    assert classify_session(**human(bot_score=40, interacted=True)).traffic_class == UNKNOWN


def test_classification_priority_and_reasons():
    assert classify_session(**human(ua_bot=True, interacted=True)).traffic_class == KNOWN_BOT
    assert classify_session(**human(bot_score=70, interacted=True)).traffic_class == SUSPICIOUS
    assert classify_session(**human(internal=True, ua_bot=True)).traffic_class == INTERNAL
    for verdict in (classify_session(**human(ua_bot=True)), classify_session(**human(bot_score=60))):
        assert verdict.reason


def test_parse_user_agent():
    assert parse_user_agent(CHROME) == ("desktop", "Chrome", "Windows 10/11")
    assert parse_user_agent(IPHONE) == ("mobile", "Safari", "iOS")


def test_classify_source():
    own = "excelinsider.com"
    assert classify_source(None, None, None, own) == ("(direct)", "(none)", None)
    assert classify_source("https://excelinsider.com/blog/", None, None, own)[0] == "(direct)"
    assert classify_source("https://www.google.com/", None, None, own)[:2] == ("google", "organic")
    assert classify_source("https://t.co/abc", None, None, own)[:2] == ("t", "social")
    assert classify_source("https://www.netflix.com/", None, None, own)[1] == "referral"
    assert classify_source(None, "Newsletter", "email", own)[:2] == ("newsletter", "email")


def test_internal_ip_matching(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "analytics_internal_ips", "198.51.100.0/24, 203.0.113.9, not-an-ip")
    assert tracking_service.is_internal_ip("198.51.100.77")
    assert tracking_service.is_internal_ip("203.0.113.9")
    assert not tracking_service.is_internal_ip("203.0.113.10")
    assert not tracking_service.is_internal_ip(None)


def test_normalize_path_ignores_admin_and_strips_query():
    assert tracking_service.normalize_path("/blog/post/?utm_source=x") == "/blog/post/"
    assert tracking_service.normalize_path("/dashboard/traffic") is None
    assert tracking_service.normalize_path("/api/auth") is None


def test_period_resolution_and_previous_period():
    period = report_service.build_period(date(2020, 1, 6), date(2020, 1, 8), "UTC")
    assert period.days == 3
    previous = period.previous()
    assert (previous.from_date, previous.to_date) == (date(2020, 1, 3), date(2020, 1, 5))
    assert previous.end == period.start

    dhaka = report_service.build_period(date(2020, 1, 6), date(2020, 1, 6), "Asia/Dhaka")
    assert dhaka.start == datetime(2020, 1, 5, 18, 0, tzinfo=timezone.utc)
    assert dhaka.granularity == "hour"
    assert report_service.build_period(date(2020, 1, 1), date(2020, 1, 7), "UTC").granularity == "day"


def test_period_validation():
    with pytest.raises(ValidationException):
        report_service.build_period(date(2020, 1, 8), date(2020, 1, 6), "UTC")
    with pytest.raises(ValidationException):
        report_service.build_period(date(2018, 1, 1), date(2020, 1, 1), "UTC")
    with pytest.raises(ValidationException):
        report_service.build_period(date(2020, 1, 1), date(2020, 1, 2), "Not/AZone")


def test_datacenter_ranges_are_parsed_merged_and_matched():
    aws = {"prefixes": [{"ip_prefix": "3.5.140.0/22", "service": "EC2"}, {"ip_prefix": "9.9.9.0/24", "service": "ROUTE53"}]}
    gcp = {"prefixes": [{"ipv4Prefix": "34.80.0.0/15"}, {"ipv6Prefix": "2600:1900::/35"}]}
    prefixes = datacenter.parse_aws(aws) + datacenter.parse_gcp(gcp) + ["3.5.142.0/24", "not-a-prefix"]
    ranges = datacenter.merge_ranges(prefixes)
    assert len(ranges) == 2

    datacenter.load_ranges(ranges)
    assert datacenter.contains("3.5.141.200")
    assert datacenter.contains("34.81.0.1")
    assert not datacenter.contains("9.9.9.9")
    assert not datacenter.contains("203.0.113.5")
    assert not datacenter.contains("2600:1900::1")
    assert not datacenter.contains("garbage")

    datacenter.load_ranges([])
    assert not datacenter.contains("3.5.141.200")


def test_datacenter_ip_lowers_confidence_but_is_not_proof_alone():
    assert bot_score(CHROME, datacenter=True) == 40
    assert bot_score(CHROME, datacenter=True) < BOT_THRESHOLD
    assert bot_score(CHROME, datacenter=True, has_languages=False) >= BOT_THRESHOLD

    verdict = classify_session(**human(bot_score=40, interacted=True))
    assert verdict.traffic_class == UNKNOWN
    assert "datacenter" in verdict.reason
