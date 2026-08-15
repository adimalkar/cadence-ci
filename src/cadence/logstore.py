"""Content-addressed, gzip-compressed log storage.

Local filesystem for now. The storage_key format (two-char shard / sha256 / .log.gz) is
shaped like an S3 key on purpose, so moving to R2 or B2 later is a backend swap, not a
schema change -- the same discipline as the `CIProvider` seam.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import psycopg

from cadence.models import Repo
from cadence.providers.base import CIProvider


@dataclass(slots=True, frozen=True)
class LogPutResult:
    sha256: str
    storage_key: str
    raw_size: int
    compressed_size: int
    already_stored: bool


class LocalLogStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes) -> LogPutResult:
        """Write once, by content. A retry's log is usually byte-identical to a prior
        attempt's, and a re-fetch of the same job is a guaranteed no-op here rather
        than a second copy on disk."""
        digest = hashlib.sha256(data).hexdigest()
        key = f"{digest[:2]}/{digest}.log.gz"
        path = self.root / key
        if path.exists():
            return LogPutResult(digest, key, len(data), path.stat().st_size, already_stored=True)

        path.parent.mkdir(parents=True, exist_ok=True)
        # The temp name must be unique per writer, not per content: identical logs are
        # exactly the case content-addressing invites (retries, matrix legs with the same
        # output), so a digest-derived temp path guarantees a collision precisely when
        # two workers race. They would interleave writes into one file and each rename it
        # out from under the other, publishing a corrupt object.
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            with gzip.open(tmp, "wb", compresslevel=6) as f:
                f.write(data)
            tmp.replace(path)  # atomic within a filesystem
        finally:
            tmp.unlink(missing_ok=True)
        return LogPutResult(digest, key, len(data), path.stat().st_size, already_stored=False)

    def get(self, storage_key: str) -> bytes:
        with gzip.open(self.root / storage_key, "rb") as f:
            return f.read()


async def store_job_log(
    provider: CIProvider,
    conn: psycopg.Connection,
    log_store: LocalLogStore,
    repo: Repo,
    job_id: int,
) -> str:
    """Fetch and store one job's log, never twice.

    Log download is the rate-limit hog by a wide margin, so `log_chunk` existing at all
    is treated as proof the work is already done -- this check runs before any network
    call, not after.
    """
    if conn.execute("SELECT 1 FROM log_chunk WHERE job_id = %s", (job_id,)).fetchone():
        return "cached"

    data = await provider.fetch_logs(repo, job_id)
    if data is None:
        # Past GitHub's 90-day retention, or never existed. Terminal -- the caller
        # records this as done rather than failed, since no future attempt can succeed.
        return "expired"

    result = log_store.put(data)
    conn.execute(
        "INSERT INTO log_chunk (job_id, sha256, storage_key, byte_size, compressed_size)"
        " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (job_id) DO NOTHING",
        (job_id, result.sha256, result.storage_key, result.raw_size, result.compressed_size),
    )
    return "fetched"
