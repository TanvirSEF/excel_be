from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.jobs.maintenance import cleanup_expired_tokens, prune_old_post_views
from app.jobs.scheduled_publisher import publish_scheduled_posts
from app.jobs.sitemap_regenerator import regenerate_sitemap
from app.jobs.trending_calculator import calculate_trending
from app.jobs.view_count_flusher import flush_view_counts


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(
        timezone="UTC",
        job_defaults={"max_instances": 1, "coalesce": True},
    )
    scheduler.add_job(flush_view_counts, "interval", seconds=60, id="flush_view_counts")
    scheduler.add_job(publish_scheduled_posts, "interval", seconds=60, id="publish_scheduled_posts")
    scheduler.add_job(calculate_trending, "interval", minutes=30, id="calculate_trending")
    scheduler.add_job(regenerate_sitemap, "interval", hours=6, id="regenerate_sitemap")
    scheduler.add_job(cleanup_expired_tokens, "cron", hour=3, minute=15, id="cleanup_expired_tokens")
    scheduler.add_job(prune_old_post_views, "cron", day_of_week="sun", hour=4, id="prune_old_post_views")
    return scheduler
