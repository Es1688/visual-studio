"""fal.ai queue API adapter: submit -> poll -> download, with transient retries.

Library for gen_image.py (P1) and later edit_image.py / upscale.py (P3);
no CLI. Auth and base/queue URLs come from config/providers.yaml
(providers.fal); the key is read from os.environ["FAL_KEY"] only
(AGENTS.md, «Секреты»). Endpoints per model live in config/capabilities.yaml.

Usage:
    from fal_adapter import FalClient, to_data_uri

    client = FalClient()                  # FAL_KEY from the environment
    images = client.generate(             # submit + poll + download
        "fal-ai/flux/dev",
        {"prompt": "cozy morning", "image_size": {"width": 1080, "height": 1080}},
    )
    Path("out.png").write_bytes(images[0])

    # reference image for image_to_image payloads (P3):
    data_uri = to_data_uri("projects/demo/references/item.png")

Tests inject httpx.MockTransport via the `transport` parameter; no real
network calls happen there. Retries cover transient failures only
(HTTP 429 / 5xx, network errors), at most budget.yaml max_retries_per_asset,
with exponential backoff (pass backoff=0.0 in tests).
"""

from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

import studio_common as sc  # noqa: E402

DEFAULT_QUEUE_URL = "https://queue.fal.run"
DEFAULT_BASE_URL = "https://fal.run"

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


class FalError(RuntimeError):
    """Non-retryable fal.ai failure (bad request, auth, exhausted retries)."""


_PROXY_ENV_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def _normalize_proxy_env() -> None:
    """Rewrite socks:// proxy URLs to socks5h:// in the process environment.

    Desktop VPN clients often export ALL_PROXY=socks://127.0.0.1:<port>,
    which httpx rejects at Client() construction ("Unknown scheme for proxy
    URL"). httpx only accepts socks5:// and socks5h:// (and needs the
    httpx[socks] extra); socks5h resolves DNS on the proxy side, which also
    avoids poisoned local DNS for blocked hosts. Must run before
    httpx.Client() reads the environment.
    """
    for var in _PROXY_ENV_VARS:
        value = os.environ.get(var)
        if value and value.lower().startswith("socks://"):
            os.environ[var] = "socks5h://" + value[len("socks://") :]


def to_data_uri(path: str | Path) -> str:
    """Local image file -> base64 data URI (reference images in fal payloads).

    Relative paths are resolved against the repo root (sc.REPO_ROOT).
    """
    file_path = Path(path)
    if not file_path.is_absolute():
        file_path = sc.REPO_ROOT / file_path
    mime = _MIME_BY_SUFFIX.get(file_path.suffix.lower(), "application/octet-stream")
    encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


class FalClient:
    """Thin client over the fal.ai queue API (queue.fal.run).

    Flow (docs.fal.ai): POST {queue_url}/{endpoint} -> {request_id, status_url,
    response_url}; poll status_url until COMPLETED; GET response_url; download
    images[*].url.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        queue_url: str | None = None,
        base_url: str | None = None,
        poll_timeout_s: float = 180.0,
        poll_interval_s: float = 1.0,
        http_timeout_s: float = 30.0,
        backoff: float = 0.5,
        max_retries: int | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get("FAL_KEY", "")
        if not key:
            raise RuntimeError("FAL_KEY is not set")
        self.api_key = key

        provider = (
            sc.load_yaml(sc.config_dir() / "providers.yaml")
            .get("providers", {})
            .get("fal", {})
        )
        self.queue_url = (queue_url or provider.get("queue_url", DEFAULT_QUEUE_URL)).rstrip("/")
        self.base_url = (base_url or provider.get("base_url", DEFAULT_BASE_URL)).rstrip("/")

        self.poll_timeout_s = poll_timeout_s
        self.poll_interval_s = poll_interval_s
        self.backoff = backoff
        self.max_retries = (
            max_retries if max_retries is not None else sc.max_retries()
        )
        _normalize_proxy_env()
        self._http = httpx.Client(
            timeout=http_timeout_s,
            transport=transport,
            headers={"Authorization": f"Key {self.api_key}"},
        )

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> FalClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- queue API steps ----------------------------------------------------

    def submit(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Enqueue a job; returns {request_id, status_url, response_url, ...}."""
        url = f"{self.queue_url}/{endpoint.lstrip('/')}"
        data = self._request("POST", url, json=payload).json()
        if not isinstance(data, dict) or not data.get("request_id"):
            raise FalError(f"submit {url}: no request_id in response: {data!r:.200}")
        return data

    def poll(self, status_url: str, timeout_s: float | None = None) -> dict[str, Any]:
        """Poll the status URL until the request COMPLETED; returns final status."""
        deadline = time.monotonic() + (self.poll_timeout_s if timeout_s is None else timeout_s)
        while True:
            status = self._request("GET", status_url).json()
            state = str(status.get("status", "")).upper()
            if state == "COMPLETED":
                return status
            if state in {"FAILED", "ERROR"}:
                raise FalError(f"fal.ai request failed: {status!r:.400}")
            if time.monotonic() >= deadline:
                waited = self.poll_timeout_s if timeout_s is None else timeout_s
                raise FalError(f"poll timeout after {waited:.0f}s: {status_url}")
            time.sleep(self.poll_interval_s)

    def result(self, response_url: str) -> dict[str, Any]:
        """Fetch the finished request result (images[*].url live here)."""
        return self._request("GET", response_url).json()

    def download(self, url: str) -> bytes:
        """Download one produced image to bytes."""
        return self._request("GET", url).content

    def generate(
        self, endpoint: str, payload: dict[str, Any], timeout_s: float | None = None
    ) -> list[bytes]:
        """Full queue flow: submit -> poll -> result -> download every image."""
        submitted = self.submit(endpoint, payload)
        request_id = submitted["request_id"]
        status_url = submitted.get("status_url") or (
            f"{self.queue_url}/{endpoint.lstrip('/')}/requests/{request_id}/status"
        )
        response_url = submitted.get("response_url") or (
            f"{self.queue_url}/{endpoint.lstrip('/')}/requests/{request_id}"
        )
        self.poll(status_url, timeout_s)
        result = self.result(response_url)
        urls = [image.get("url") for image in result.get("images", [])]
        if not urls or any(not url for url in urls):
            raise FalError(f"no images in fal.ai response: {sorted(result)!r:.400}")
        return [self.download(url) for url in urls]

    # -- HTTP with transient retries ---------------------------------------

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """One request with retries for transient failures only.

        Transient: network/timeout errors and HTTP 429/5xx. Anything else
        (4xx) fails immediately as FalError. Retries <= self.max_retries with
        exponential backoff (backoff * 2**attempt).
        """
        attempts = self.max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                response = self._http.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                if attempt == attempts:
                    raise FalError(
                        f"{method} {url}: network error after {attempt} attempts: {exc}"
                    ) from exc
                self._sleep_before_retry(attempt)
                continue
            if self._is_transient(response):
                if attempt == attempts:
                    raise FalError(
                        f"{method} {url}: HTTP {response.status_code}"
                        f" after {attempt} attempts"
                    )
                self._sleep_before_retry(attempt)
                continue
            if response.is_error:
                raise FalError(
                    f"{method} {url}: HTTP {response.status_code}: {response.text[:200]}"
                )
            return response
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _is_transient(response: httpx.Response) -> bool:
        return response.status_code == 429 or response.status_code >= 500

    def _sleep_before_retry(self, attempt: int) -> None:
        time.sleep(self.backoff * 2 ** (attempt - 1))
