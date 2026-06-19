"""Schema-only init script.

Drops and recreates the full ORM schema. Safe to run repeatedly; the
worker is the source of truth for job data and runs after the API starts.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Make the project root importable when run as `python scripts/init_db.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import models
from app.config import get_settings
from app.database import get_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("init_db")


def main() -> int:
    settings = get_settings()
    engine = get_engine()
    logger.info("Dropping and recreating schema on %s", settings.database_url.split("@")[-1])
    models.Base.metadata.drop_all(bind=engine)
    models.Base.metadata.create_all(bind=engine)
    logger.info("Schema ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
