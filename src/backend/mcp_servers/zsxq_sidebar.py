"""Narrow MCP-facing client for the local ZSXQ browser import API."""

from __future__ import annotations

import re
import os
import time
import warnings
from typing import Any, Literal
from urllib.parse import urlsplit

import requests
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic_settings.exceptions import IncompleteFieldDefinitionWarning


DEFAULT_BASE_URL = "http://127.0.0.1:5001"
DEFAULT_GROUP_ID = "28888112822211"
TAB_SCOPES = {
    "mp3": {
        "tab_id": "88844545452542",
        "tab_label": "调研纪要",
    },
    "pdf": {
        "tab_id": "51184248544214",
        "tab_label": "外资研报",
    },
}

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_IDEMPOTENCY_COMPONENT = re.compile(r"^[A-Za-z0-9._:+-]{1,180}$")
_TRANSIENT_STATUS_CODES = {502, 503, 504}


class LocalApiError(RuntimeError):
    """The local API returned an unusable response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Any = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def _validated_base_url(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in _LOOPBACK_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("ZSXQ local API URL must be a loopback HTTP(S) origin")
    return str(value).rstrip("/")


class ZsxqSidebarApiClient:
    """Call only the three local endpoints required by the sidebar workflow."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = 15.0,
        max_attempts: int = 2,
        retry_delay: float = 0.25,
        session: requests.Session | None = None,
    ):
        self.base_url = _validated_base_url(base_url)
        self.timeout = float(timeout)
        self.max_attempts = max(1, int(max_attempts))
        self.retry_delay = max(0.0, float(retry_delay))
        self.session = session or requests.Session()
        # Local calls must never inherit HTTP(S)_PROXY from the host shell.
        self.session.trust_env = False

    def health(self) -> dict[str, Any]:
        payload = self._request("GET", "/health")
        if payload.get("status") != "healthy":
            raise LocalApiError(
                "local backend health check did not report healthy",
                status_code=200,
                payload=payload,
            )
        return payload

    def get_history(self, kind: str, *, days: int = 3) -> dict[str, Any]:
        normalized_kind = str(kind or "").strip().lower()
        if normalized_kind not in TAB_SCOPES:
            raise ValueError("kind must be mp3 or pdf")
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 7:
            raise ValueError("days must be an integer from 1 to 7")
        scope = TAB_SCOPES[normalized_kind]
        payload = self._request(
            "POST",
            "/api/v1/zsxq/browser-import/history",
            json_payload={
                "kind": normalized_kind,
                "days": days,
                "group_id": DEFAULT_GROUP_ID,
                "tab_id": scope["tab_id"],
            },
        )
        data = payload.get("data")
        if payload.get("success") is not True or not isinstance(data, dict):
            raise LocalApiError(
                "local history API returned an invalid response",
                status_code=200,
                payload=payload,
            )
        return data

    def submit_manifest(self, manifest: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be an object")
        kind = str(manifest.get("kind") or "").strip().lower()
        if kind not in TAB_SCOPES:
            raise ValueError("manifest.kind must be mp3 or pdf")
        run_id = str(manifest.get("run_id") or "").strip()
        if not _IDEMPOTENCY_COMPONENT.fullmatch(run_id):
            raise ValueError("manifest.run_id must be a stable ASCII identifier")

        scope = TAB_SCOPES[kind]
        expected_scope = {
            "group_id": DEFAULT_GROUP_ID,
            "tab_id": scope["tab_id"],
            "tab_label": scope["tab_label"],
        }
        for field, expected in expected_scope.items():
            if str(manifest.get(field) or "").strip() != expected:
                raise ValueError(f"manifest.{field} does not match the fixed {kind} scope")

        idempotency_key = f"zsxq-sidebar:{kind}:{run_id}"
        payload = self._request(
            "POST",
            "/api/v1/zsxq/browser-import",
            json_payload={
                "manifest": manifest,
                "dispatch_clean": True,
                "auto_publish": {"enabled": True},
            },
            headers={"Idempotency-Key": idempotency_key},
        )
        if payload.get("success") is not True:
            raise LocalApiError(
                "local browser import API returned an invalid response",
                status_code=200,
                payload=payload,
            )
        return payload

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_transport_error: requests.RequestException | None = None
        for attempt in range(self.max_attempts):
            try:
                response = self.session.request(
                    method,
                    url,
                    json=json_payload,
                    headers=headers,
                    timeout=self.timeout,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_transport_error = exc
                if attempt + 1 < self.max_attempts:
                    self._wait_before_retry()
                    continue
                raise LocalApiError(
                    f"local API unavailable: {exc}",
                    payload={"code": "local_api_unavailable"},
                ) from exc

            try:
                payload = response.json()
            except ValueError as exc:
                raise LocalApiError(
                    "local API returned non-JSON content",
                    status_code=response.status_code,
                ) from exc
            if not isinstance(payload, dict):
                raise LocalApiError(
                    "local API returned a non-object JSON response",
                    status_code=response.status_code,
                    payload=payload,
                )

            if (
                response.status_code in _TRANSIENT_STATUS_CODES
                and attempt + 1 < self.max_attempts
            ):
                self._wait_before_retry()
                continue
            if not 200 <= response.status_code < 300:
                raise LocalApiError(
                    str(payload.get("error") or "local API request failed"),
                    status_code=response.status_code,
                    payload=payload,
                )
            return payload

        raise LocalApiError(
            f"local API unavailable: {last_transport_error}",
            payload={"code": "local_api_unavailable"},
        )

    def _wait_before_retry(self) -> None:
        if self.retry_delay:
            time.sleep(self.retry_delay)


def create_mcp_server(client: ZsxqSidebarApiClient | None = None) -> FastMCP:
    """Create the stdio MCP server used by Secure MCP Tunnel."""

    local_api = client or ZsxqSidebarApiClient(
        os.environ.get("ZSXQ_LOCAL_API_URL", DEFAULT_BASE_URL)
    )
    # mcp 1.29.0 currently leaves the generic lifespan annotation unresolved.
    # Suppress only that SDK warning so it cannot pollute the stdio MCP stream.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            category=IncompleteFieldDefinitionWarning,
            message="Field 'lifespan' has an incomplete definition.*",
        )
        server = FastMCP(
            "zsxq-sidebar-sync",
            instructions=(
                "Call zsxq_get_history before any Knowledge Planet download. "
                "After scanning, call zsxq_submit_manifest once even when files is empty. "
                "Retry submission only with the identical run_id and manifest."
            ),
            log_level="WARNING",
        )

    @server.tool(
        title="Check ZSXQ local workflow health",
        description=(
            "Use this to verify that the local Knowledge Planet import service is "
            "reachable before querying history or submitting a run."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    def zsxq_health() -> dict[str, Any]:
        return {"reachable": True, "backend": local_api.health()}

    @server.tool(
        title="Get recent ZSXQ unified submission history",
        description=(
            "Use this before downloading MP3 or PDF files to obtain the complete "
            "server-owned deduplication history across scheduled, sidebar, and "
            "manual Knowledge Planet submissions for the fixed tab."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    def zsxq_get_history(
        kind: Literal["mp3", "pdf"], days: int = 3
    ) -> dict[str, Any]:
        return local_api.get_history(kind, days=days)

    @server.tool(
        title="Submit a ZSXQ sidebar download manifest",
        description=(
            "Use this after a completed MP3 or PDF scan, including a zero-file scan. "
            "It persists the manifest and queues the existing clean/publish workflow."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    def zsxq_submit_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
        return local_api.submit_manifest(manifest)

    return server


def main() -> None:
    create_mcp_server().run(transport="stdio")


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_GROUP_ID",
    "LocalApiError",
    "TAB_SCOPES",
    "ZsxqSidebarApiClient",
    "create_mcp_server",
    "main",
]


if __name__ == "__main__":
    main()
