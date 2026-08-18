import uuid
from datetime import date

from pydantic import BaseModel


class DailyViews(BaseModel):
    date: date
    views: int


class ReferrerStat(BaseModel):
    referrer: str
    views: int


class PostAnalytics(BaseModel):
    post_id: uuid.UUID
    title: str
    slug: str
    total_views: int
    views_last_7_days: list[DailyViews]
    views_last_30_days: int
    unique_visitors_30_days: int
    top_referrers_30_days: list[ReferrerStat]


class TopPost(BaseModel):
    post_id: uuid.UUID
    title: str
    slug: str
    views: int


class TrendingPost(BaseModel):
    id: uuid.UUID
    title: str
    slug: str


class OverviewAnalytics(BaseModel):
    total_posts: int
    published_posts: int
    draft_posts: int
    total_views: int
    views_last_7_days: int
    top_posts_7_days: list[TopPost]
    trending: list[TrendingPost]
