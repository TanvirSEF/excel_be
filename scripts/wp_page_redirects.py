"""Add redirects for the 63 legacy WordPress pages.

The WXR import only migrated posts; the old site's static pages (about,
legal, services, calculator hubs and the 45 calculator landing pages) have
new equivalents on the Next.js frontend. This inserts ``Redirect`` rows so
those URLs resolve once the domain switches off WordPress. Existing rows
are never overwritten. ``nehadulfat`` (an author page) is intentionally
unmapped.
"""
import argparse
import asyncio
import os

from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.models import Redirect

PAGE_REDIRECTS: dict[str, str] = {
    "about-us": "/about",
    "accounting-calculator": "/calculators/accounting",
    "accounting-rate-of-return-calculator": "/calculators/finance/accounting-rate-of-return-calculator",
    "amazon-seller-commission-calculator": "/calculators/accounting/amazon-seller-commission-calculator",
    "b2b-roi-calculator": "/calculators/finance/b2b-roi-calculator",
    "blog": "/blog",
    "calculator": "/calculators",
    "cash-conversion-cycle-calculator": "/calculators/accounting/cash-conversion-cycle-calculator",
    "cash-on-cash-roi-calculator": "/calculators/finance/cash-on-cash-roi-calculator",
    "coefficient-of-variance-calculator": "/calculators/statistics/coefficient-of-variance-calculator",
    "contact-us": "/contact",
    "cookie-policy": "/cookie-policy",
    "cost-of-equity-calculator": "/calculators/finance/cost-of-equity-calculator",
    "critical-z-value-calculator": "/calculators/statistics/critical-z-value-calculator",
    "custom-spreadsheet-tools-order": "/services/custom-tools",
    "custom-tools-services": "/services/custom-tools",
    "debt-payoff-calculator-with-extra-payments": "/calculators/finance/debt-payoff-extra-payments-calculator",
    "debt-snowball-vs-avalanche-calculator": "/calculators/accounting/debt-snowball-vs-avalanche-calculator",
    "dividend-reinvestment-plan-calculator": "/calculators/finance/dividend-reinvestment-plan-calculator",
    "dividend-snowball-calculator": "/calculators/finance/dividend-snowball-calculator",
    "enterprise-seo-roi-calculator": "/calculators/finance/enterprise-seo-roi-calculator",
    "excelinsider-terms-and-conditions": "/terms",
    "financila-calculaors": "/calculators/finance",
    "geometric-mean-calculator": "/calculators/statistics/geometric-mean-calculator",
    "gross-up-payroll-calculator": "/calculators/accounting/gross-up-payroll-calculator",
    "harmonic-mean-calculator": "/calculators/statistics/harmonic-mean-calculator",
    "holding-period-return-calculator": "/calculators/finance/holding-period-return-calculator",
    "home": "/",
    "internal-rate-of-return-calculator": "/calculators/finance/irr-calculator",
    "living-off-dividends-calculator": "/calculators/finance/living-off-dividends-calculator",
    "marginal-propensity-to-consume-calculator": "/calculators/accounting/marginal-propensity-to-consume-calculator",
    "marketing-roi-calculator": "/calculators/finance/marketing-roi-calculator",
    "one-way-analysis-of-variance-calculator": "/calculators/statistics/one-way-anova-calculator",
    "order-plan": "/pricing",
    "order-plan-for-custom-templates": "/services/custom-templates",
    "p-value-from-z-score-calculator": "/calculators/statistics/p-value-from-z-score-calculator",
    "payroll-calculator-with-overtime": "/calculators/accounting/payroll-overtime-calculator",
    "payroll-conversion-calculator": "/calculators/accounting/payroll-conversion-calculator",
    "pooled-variance-calculator": "/calculators/statistics/pooled-variance-calculator",
    "privacy-policy": "/privacy",
    "prorated-bonus-calculator": "/calculators/accounting/prorated-bonus-calculator",
    "rental-property-roi-calculator": "/calculators/finance/rental-property-roi-calculator",
    "retail-profit-margin-calculator": "/calculators/accounting/retail-profit-margin-calculator",
    "retained-earnings-calculator": "/calculators/accounting/retained-earnings-calculator",
    "retirement-rate-of-return-calculator": "/calculators/finance/retirement-rate-of-return-calculator",
    "reverse-margin-calculator": "/calculators/accounting/reverse-margin-calculator",
    "sales-commission-calculator": "/calculators/accounting/sales-commission-calculator",
    "salesperson-profitability-calculator": "/calculators/accounting/salesperson-profitability-calculator",
    "savings-withdrawal-calculator": "/calculators/finance/savings-withdrawal-calculator",
    "share-profit-calculator": "/calculators/finance/share-profit-calculator",
    "solar-roi-calculator": "/calculators/finance/solar-roi-calculator",
    "spreadsheet-services": "/pricing",
    "statistics-calculator": "/calculators/statistics",
    "template-services": "/services/custom-templates",
    "time-weighted-average-calculator": "/calculators/statistics/time-weighted-average-calculator",
    "two-way-analysis-of-variance-calculator": "/calculators/statistics/two-way-anova-calculator",
    "venture-capital-calculator": "/calculators/finance/venture-capital-calculator",
    "volume-weighted-average-price-calculator": "/calculators/statistics/vwap-calculator",
    "weighted-average-grade-calculator": "/calculators/statistics/weighted-average-grade-calculator",
    "weighted-average-overtime-calculator": "/calculators/statistics/weighted-average-overtime-calculator",
    "wholesale-margin-calculator": "/calculators/accounting/wholesale-margin-calculator",
    "z-score-to-percentile-calculator": "/calculators/statistics/z-score-to-percentile-calculator",
}


def _is_safe_test_database() -> bool:
    parsed = make_url(settings.database_url)
    return parsed.host in {"localhost", "127.0.0.1", "::1"} or "test" in (parsed.database or "")


async def main():
    parser = argparse.ArgumentParser(description="Insert legacy page redirects")
    parser.add_argument("--dry-run", action="store_true", help="report planned inserts, no writes")
    args = parser.parse_args()

    if os.environ.get("ALLOW_REMOTE_DB_TESTS") != "1" and not _is_safe_test_database():
        raise SystemExit(
            f"Refusing to run against remote database host {make_url(settings.database_url).host!r}. "
            "Point DATABASE_URL at a local database or set ALLOW_REMOTE_DB_TESTS=1 to override."
        )

    stats = {"created": 0, "already_present": 0}

    async with AsyncSessionLocal() as db:
        for old_path, new_path in PAGE_REDIRECTS.items():
            exists = await db.scalar(select(Redirect).where(Redirect.old_path == old_path))
            if exists is not None:
                stats["already_present"] += 1
                continue
            db.add(Redirect(old_path=old_path, new_path=new_path))
            stats["created"] += 1
            print(f"  + {old_path} -> {new_path}", flush=True)

        if args.dry_run:
            await db.rollback()
        else:
            await db.commit()

        print(f"\n{'dry run' if args.dry_run else 'done'}: {stats}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
