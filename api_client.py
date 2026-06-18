from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

from storage import CacheStore


DEFAULT_BASE_URL = "https://api.zafronix.com/fifa/worldcup/v1"


class APIClientError(RuntimeError):
    pass


@dataclass
class APIResult:
    endpoint: str
    params: dict[str, Any]
    payload: Any
    from_cache: bool
    not_modified: bool = False
    stale: bool = False
    etag: str | None = None
    warning: str | None = None


class ZafronixAPIClient:
    def __init__(
        self,
        api_key: str,
        cache_store: CacheStore,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: int = 12,
        max_retries: int = 3,
    ) -> None:
        if not api_key:
            raise APIClientError("Missing Zafronix API key. Set ZAFRONIX_API_KEY or .streamlit/secrets.toml.")

        self.api_key = api_key
        self.cache_store = cache_store
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries)
        self.session = requests.Session()

    def get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        *,
        allow_stale: bool = True,
    ) -> APIResult:
        endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        params = params or {}
        cached = self.cache_store.get_response(endpoint, params)
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Accept": "application/json",
            "X-API-Key": self.api_key,
        }

        if cached and cached.etag:
            headers["If-None-Match"] = cached.etag

        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )

                if response.status_code == 304 and cached:
                    self.cache_store.touch_response(endpoint, params)
                    return APIResult(
                        endpoint=endpoint,
                        params=params,
                        payload=cached.payload,
                        from_cache=True,
                        not_modified=True,
                        etag=cached.etag,
                    )

                if response.status_code == 200:
                    payload = response.json()
                    etag = response.headers.get("ETag") or response.headers.get("Etag")
                    response_headers = {
                        key: value
                        for key, value in response.headers.items()
                        if key.lower() in {"etag", "cache-control", "last-modified"}
                    }
                    self.cache_store.upsert_response(
                        endpoint,
                        params,
                        payload,
                        etag=etag,
                        status_code=response.status_code,
                        headers=response_headers,
                    )
                    return APIResult(
                        endpoint=endpoint,
                        params=params,
                        payload=payload,
                        from_cache=False,
                        etag=etag,
                    )

                if response.status_code in {429, 500, 502, 503, 504} and attempt < self.max_retries - 1:
                    time.sleep(0.5 * (2**attempt))
                    continue

                message = f"{endpoint} returned HTTP {response.status_code}: {response.text[:240]}"
                raise APIClientError(message)

            except (requests.RequestException, ValueError, APIClientError) as exc:
                last_error = exc
                if attempt < self.max_retries - 1:
                    time.sleep(0.5 * (2**attempt))
                    continue

        if allow_stale and cached:
            return APIResult(
                endpoint=endpoint,
                params=params,
                payload=cached.payload,
                from_cache=True,
                stale=True,
                etag=cached.etag,
                warning=f"Using stale cache for {endpoint}: {last_error}",
            )

        raise APIClientError(str(last_error) if last_error else f"Failed to fetch {endpoint}")

    def refresh_tournament_bundle(self, year: int) -> dict[str, APIResult]:
        return {
            "tournaments": self.get("/tournaments"),
            "matches": self.get("/matches", {"year": year}),
            "standings": self.get("/standings", {"year": year}),
        }
