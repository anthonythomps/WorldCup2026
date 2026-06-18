from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def make_cache_key(endpoint: str, params: dict[str, Any] | None = None) -> str:
    params_json = json.dumps(params or {}, sort_keys=True, separators=(",", ":"), default=str)
    return f"{endpoint}?{params_json}"


@dataclass
class CachedResponse:
    cache_key: str
    endpoint: str
    params: dict[str, Any]
    payload: Any
    etag: str | None
    fetched_at: str
    status_code: int | None = None
    headers: dict[str, str] | None = None


class CacheStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialise()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialise(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS endpoint_cache (
                    cache_key TEXT PRIMARY KEY,
                    endpoint TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    etag TEXT,
                    payload_json TEXT NOT NULL,
                    status_code INTEGER,
                    headers_json TEXT,
                    fetched_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def get_response(self, endpoint: str, params: dict[str, Any] | None = None) -> CachedResponse | None:
        cache_key = make_cache_key(endpoint, params)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM endpoint_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()

        if row is None:
            return None

        return CachedResponse(
            cache_key=row["cache_key"],
            endpoint=row["endpoint"],
            params=json.loads(row["params_json"] or "{}"),
            payload=json.loads(row["payload_json"]),
            etag=row["etag"],
            fetched_at=row["fetched_at"],
            status_code=row["status_code"],
            headers=json.loads(row["headers_json"] or "{}"),
        )

    def get_payload(self, endpoint: str, params: dict[str, Any] | None = None) -> Any | None:
        response = self.get_response(endpoint, params)
        return response.payload if response else None

    def upsert_response(
        self,
        endpoint: str,
        params: dict[str, Any] | None,
        payload: Any,
        *,
        etag: str | None,
        status_code: int | None,
        headers: dict[str, str] | None = None,
    ) -> None:
        now = utc_now_iso()
        params_json = json.dumps(params or {}, sort_keys=True, separators=(",", ":"), default=str)
        payload_json = json.dumps(payload, sort_keys=True, default=str)
        headers_json = json.dumps(headers or {}, sort_keys=True, default=str)
        cache_key = make_cache_key(endpoint, params)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO endpoint_cache (
                    cache_key, endpoint, params_json, etag, payload_json,
                    status_code, headers_json, fetched_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    etag = excluded.etag,
                    payload_json = excluded.payload_json,
                    status_code = excluded.status_code,
                    headers_json = excluded.headers_json,
                    fetched_at = excluded.fetched_at,
                    updated_at = excluded.updated_at
                """,
                (
                    cache_key,
                    endpoint,
                    params_json,
                    etag,
                    payload_json,
                    status_code,
                    headers_json,
                    now,
                    now,
                ),
            )

    def touch_response(self, endpoint: str, params: dict[str, Any] | None = None) -> None:
        now = utc_now_iso()
        cache_key = make_cache_key(endpoint, params)
        with self._connect() as conn:
            conn.execute(
                "UPDATE endpoint_cache SET fetched_at = ?, updated_at = ? WHERE cache_key = ?",
                (now, now, cache_key),
            )

    def get_metadata(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_metadata(self, key: str, value: str | None) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO metadata (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now),
            )

    def metadata_is_stale(self, key: str, max_age_seconds: int) -> bool:
        refreshed_at = parse_iso_datetime(self.get_metadata(key))
        if refreshed_at is None:
            return True
        age = datetime.now(timezone.utc) - refreshed_at.astimezone(timezone.utc)
        return age.total_seconds() >= max_age_seconds
