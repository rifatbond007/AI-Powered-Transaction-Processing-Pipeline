"""CSV upload lifecycle.

Saves uploads to ``UPLOAD_DIR/<job_id>.csv`` and removes them after the
worker finishes (success or failure). API and worker share the same
directory via a docker-compose named volume in production.
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = (
    "text/csv",
    "application/csv",
    "application/vnd.ms-excel",  # common Windows browser behaviour
    "text/plain",  # some clients send this for .csv
)


async def save_upload(upload: UploadFile, *, job_id: str, upload_dir: str, max_bytes: int) -> Path:
    """Stream the upload to ``upload_dir/<job_id>.csv`` atomically.

    Returns the final path. Raises 415/400/413 on validation failures.
    """
    if upload.content_type and upload.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"unsupported content type: {upload.content_type!r}",
        )

    dest_dir = Path(upload_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    final_path = dest_dir / f"{job_id}.csv"
    tmp_path = dest_dir / f"{job_id}.{uuid.uuid4().hex}.part"

    written = 0
    try:
        with tmp_path.open("wb") as fh:
            while True:
                chunk = await upload.read(64 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="file too large",
                    )
                fh.write(chunk)
        if written == 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty file")
        os.replace(tmp_path, final_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()

    logger.info("Saved upload for job %s -> %s (%d bytes)", job_id, final_path, written)
    return final_path


def cleanup(job_id: str, upload_dir: str) -> None:
    """Remove ``upload_dir/<job_id>.csv`` if it exists. Idempotent."""
    p = Path(upload_dir) / f"{job_id}.csv"
    try:
        p.unlink(missing_ok=True)
        logger.info("Cleaned up upload for job %s", job_id)
    except OSError as e:
        logger.warning("Cleanup failed for job %s: %s", job_id, e)
