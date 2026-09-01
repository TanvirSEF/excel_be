"""Remove test-run artifacts (leaked categories, posts, and users) from the database.

Test suites that once ran against the shared database left rows named after
their fixtures ("Cache category …", "Matrix renamed category", "Probe …").
Exact names/slugs are matched so real content is never touched.
"""
import argparse
import asyncio

from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal, engine
from app.models import Category, Media, Post, User

TEST_CATEGORY_SLUG_PREFIX = "cache-category-"
TEST_CATEGORY_SLUGS = ("p9-beta",)

TEST_POST_TITLES = (
    "P11 Analytics Probe",
    "P6 Path One",
    "P6 Path Two",
    "P8 Comments Test",
    "P9 Cat Block Test",
    "Probe A",
    "Probe B",
    "Probe C",
    "Probe D",
    "Test",
    "XLOOKUP XZQYUW deep dive",
)


async def collect(db) -> tuple[list[Category], list[Post], list[User]]:
    test_user_ids = select(User.id).where(User.email.like("%@test.com"))

    categories = (
        await db.scalars(
            select(Category).where(
                Category.slug.like(f"{TEST_CATEGORY_SLUG_PREFIX}%") | Category.slug.in_(TEST_CATEGORY_SLUGS)
            )
        )
    ).all()
    posts = (
        await db.scalars(
            select(Post).where(Post.title.in_(TEST_POST_TITLES) | Post.author_id.in_(test_user_ids))
        )
    ).all()
    users = (await db.scalars(select(User).where(User.email.like("%@test.com")))).all()
    return categories, posts, users


async def main():
    parser = argparse.ArgumentParser(description="Delete leaked test categories, posts, and users")
    parser.add_argument("--dry-run", action="store_true", help="list what would be deleted, no writes")
    args = parser.parse_args()

    async with AsyncSessionLocal() as db:
        categories, posts, users = await collect(db)

        print(f"test categories: {len(categories)}")
        for category in categories:
            print(f"  {category.name!r} slug={category.slug!r}")
        print(f"test posts: {len(posts)}")
        for post in posts:
            print(f"  {post.title!r} slug={post.slug!r}")
        print(f"test users: {[user.email for user in users]}")

        if args.dry_run:
            print("\ndry run complete, nothing deleted")
            return

        for post in posts:
            await db.delete(post)
        if posts:
            await db.flush()
            print(f"\ndeleted {len(posts)} posts (comments/tags/views cascade)")

        deleted_categories = 0
        for category in categories:
            linked = await db.scalar(select(func.count()).select_from(Post).where(Post.category_id == category.id))
            if linked:
                print(f"  skipped category {category.slug!r}: still has {linked} posts")
                continue
            await db.delete(category)
            deleted_categories += 1
        print(f"deleted {deleted_categories}/{len(categories)} categories")

        deleted_users = 0
        for user in users:
            has_posts = await db.scalar(select(Post.id).where(Post.author_id == user.id).limit(1))
            has_media = await db.scalar(select(Media.id).where(Media.uploader_id == user.id).limit(1))
            if has_posts or has_media:
                print(f"  skipped user {user.email!r}: still referenced by posts/media")
                continue
            await db.delete(user)
            deleted_users += 1
        print(f"deleted {deleted_users}/{len(users)} users")

        await db.commit()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
