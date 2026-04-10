"""
Seed the coaching_bullets table with pre-written tips from data/seed_tips.json.

Usage:
    python -m backend.seed_tips              # load all tips for user "local-user"
    python -m backend.seed_tips --user-id X  # load for a specific user

Idempotent: uses dedup_key to avoid creating duplicates on re-run.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from backend.coaching_bullets import compute_dedup_key
from backend.database import init_db, get_db_session
from backend.models import CoachingBullet

logger = logging.getLogger(__name__)

_SEED_FILE = Path(__file__).resolve().parent.parent / "data" / "seed_tips.json"


async def seed_tips(user_id: str = "local-user") -> int:
    """
    Load seed tips into the coaching_bullets table.

    Returns the number of new tips inserted (skips duplicates).
    """
    await init_db()

    tips = json.loads(_SEED_FILE.read_text(encoding="utf-8"))
    inserted = 0
    now = datetime.now(timezone.utc)

    async with get_db_session() as db:
        for tip in tips:
            content = tip["content"]
            dedup = compute_dedup_key(content)

            # Check for existing bullet with same dedup key
            existing = await db.execute(
                select(CoachingBullet).where(
                    CoachingBullet.user_id == user_id,
                    CoachingBullet.dedup_key == dedup,
                    CoachingBullet.is_active.is_(True),
                ).limit(1)
            )
            if existing.scalar_one_or_none() is not None:
                continue

            bullet = CoachingBullet(
                user_id=user_id,
                content=content,
                category=tip.get("category", "tactic"),
                helpful_count=1,  # warm start
                harmful_count=0,
                counterpart_archetype=tip.get("counterpart_archetype"),
                elm_state=tip.get("elm_state"),
                context=tip.get("context"),
                user_archetype=tip.get("user_archetype"),
                layer=tip.get("layer"),
                source_session_id="seed",
                last_evidence_session_id="seed",
                evidence_count=1,
                dedup_key=dedup,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            db.add(bullet)
            inserted += 1

    logger.info("Seeded %d tips for user %s (%d skipped as duplicates)",
                inserted, user_id, len(tips) - inserted)
    return inserted


def main() -> None:
    user_id = "local-user"
    if "--user-id" in sys.argv:
        idx = sys.argv.index("--user-id")
        if idx + 1 < len(sys.argv):
            user_id = sys.argv[idx + 1]

    logging.basicConfig(level=logging.INFO)
    count = asyncio.run(seed_tips(user_id))
    print(f"Seeded {count} tips for user {user_id}")


if __name__ == "__main__":
    main()
