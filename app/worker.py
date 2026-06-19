"""RQ worker entrypoint.

When an upload lands, the API enqueues :func:`process_job` on the
configured RQ queue. This module orchestrates the full pipeline:

    ETL (cleaning) -> anomaly detection -> LLM classify (batched, retried)
        -> LLM narrative (retried) -> persist

LLM batch failures do NOT fail the job (PDF §5(e)). Only ETL/DB/IO
errors mark the job ``failed``.
"""

from __future__ import annotations

import logging
from datetime import date as _date
from typing import Any

from app.anomaly import flag_anomalies
from app.etl import run_etl
from app.fx import to_inr
from app.storage import _aggregate_by_currency, _build_top_merchants

logger = logging.getLogger(__name__)

# Subset of categories the PDF allows for LLM classification (§5(c)).
PDF_CATEGORIES: tuple[str, ...] = (
    "Food",
    "Shopping",
    "Travel",
    "Transport",
    "Utilities",
    "Cash Withdrawal",
    "Entertainment",
    "Other",
)


def _persist_transactions(store, job_id: str, rows: list[dict[str, Any]]) -> None:
    """Re-coerce dict dates for the SQL backend and persist."""
    objs: list[dict[str, Any]] = []
    for r in rows:
        out = dict(r)
        if isinstance(out.get("date"), str):
            out["date"] = _date.fromisoformat(out["date"])
        objs.append(out)
    store.attach_transactions(job_id, objs)


def _build_summary_payload(rows: list[dict[str, Any]], anomaly_count: int) -> dict[str, Any]:
    """Compute the deterministic parts of the summary (everything except narrative + risk)."""
    by_currency = _aggregate_by_currency(rows)
    top3 = _build_top_merchants(rows, limit=3)
    total_inr = round(sum(to_inr(r["amount"], r["currency"]) for r in rows), 2)
    total_usd = round(sum(r["amount"] for r in rows if r["currency"] == "USD"), 2)
    return {
        "total_spend_by_currency": by_currency,
        "top_3_merchants": top3,
        "anomaly_count": anomaly_count,
        "total_spend_inr": total_inr,
        "total_spend_usd": total_usd,
    }


def process_job(job_id: str, csv_path: str) -> dict[str, Any]:
    """RQ task. Returns a small dict for the RQ log; persistent state lives in the DB."""
    from app import llm
    from app.config import get_settings
    from app.dependencies import get_job_store
    from app.upload import cleanup

    settings = get_settings()
    store = get_job_store()
    out: dict[str, Any] = {"job_id": job_id, "rows": 0, "llm_failures": 0}

    try:
        store.set_job_status(job_id, "processing")

        # ---- 1. ETL (clean only) ----
        result = run_etl(csv_path)
        rows = result.rows
        out["quarantined"] = len(result.quarantine)

        # ---- 2. Anomaly detection ----
        rows = flag_anomalies(rows)
        out["anomalies"] = sum(1 for r in rows if r["is_anomaly"])

        # Default llm_failed=False for all rows; only batches that fail set it.
        for r in rows:
            r.setdefault("llm_failed", False)
            r.setdefault("llm_category", None)
            r.setdefault("llm_raw_response", None)

        # ---- 3. LLM classify (only rows still "Uncategorised") ----
        uncategorised_idx = [i for i, r in enumerate(rows) if r["category"] == "Uncategorised"]
        if uncategorised_idx:
            subset = [rows[i] for i in uncategorised_idx]
            classified = llm.classify_categories(subset)
            for i, new in zip(uncategorised_idx, classified, strict=True):
                rows[i]["llm_category"] = new.get("llm_category")
                rows[i]["llm_raw_response"] = new.get("llm_raw_response")
                rows[i]["llm_failed"] = bool(new.get("llm_failed", False))
            out["llm_failures"] = sum(1 for r in rows if r["llm_failed"])

        # ---- 4. Persist transactions ----
        _persist_transactions(store, job_id, rows)
        out["rows"] = len(rows)

        # ---- 5. LLM narrative + persist summary ----
        anomaly_count = sum(1 for r in rows if r["is_anomaly"])
        payload = _build_summary_payload(rows, anomaly_count)
        narrative = llm.generate_summary(payload)
        if narrative.get("llm_failed"):
            # Use a safe placeholder; job still completes.
            narrative_out = {
                "narrative": "LLM narrative unavailable.",
                "risk_level": "low",
                "top_3_merchants": payload["top_3_merchants"],
            }
        else:
            narrative_out = {
                "narrative": narrative.get("narrative", ""),
                "risk_level": narrative.get("risk_level", "low"),
                "top_3_merchants": narrative.get("top_3_merchants", payload["top_3_merchants"]),
            }
        store.attach_summary(
            {
                "job_id": job_id,
                "total_spend_inr": payload["total_spend_inr"],
                "total_spend_usd": payload["total_spend_usd"],
                "top_merchants": narrative_out["top_3_merchants"],
                "anomaly_count": anomaly_count,
                "narrative": narrative_out["narrative"],
                "risk_level": narrative_out["risk_level"],
            }
        )

        store.set_job_status(job_id, "completed", row_count_clean=len(rows))
        return out
    except Exception as e:
        logger.exception("process_job(%s) failed", job_id)
        store.set_job_status(job_id, "failed", error_message=str(e))
        raise
    finally:
        cleanup(job_id, settings.upload_dir)
