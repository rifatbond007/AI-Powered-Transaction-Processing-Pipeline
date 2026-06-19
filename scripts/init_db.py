"""One-shot script: load transactions.csv -> ETL -> Postgres.

Idempotent — wipes the ``transactions`` table first so re-running produces
the same end state. Safe to invoke from a Docker entrypoint or a Makefile
target.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Make the project root importable when run as ``python scripts/init_db.py``.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import models  # noqa: F401  (imports register ORM models)
from app.config import get_settings
from app.database import get_engine, get_session_factory
from app.etl import run_etl
from app.store_sql import SqlTransactionStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger("init_db")


def main() -> int:
    settings = get_settings()
    csv_path = Path(settings.csv_path)
    if not csv_path.exists():
        logger.error("CSV %s not found", csv_path)
        return 1

    logger.info("Dropping and recreating tables on %s", settings.database_url.split("@")[-1])
    engine = get_engine()
    models.Base.metadata.drop_all(bind=engine)
    models.Base.metadata.create_all(bind=engine)

    logger.info("Running ETL on %s", csv_path)
    result = run_etl(csv_path)
    logger.info("ETL: %d clean, %d quarantined", len(result.clean_df), len(result.quarantine))

    store = SqlTransactionStore(get_session_factory())
    store.insert_many(result.clean_df.to_dict(orient="records"))
    logger.info("Inserted %d rows", len(result.clean_df))

    # Persist a summary JSON next to the DB for ops visibility.
    summary_path = Path("data/summary.json")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(result.summary, indent=2, sort_keys=True))
    logger.info("Wrote summary to %s", summary_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
