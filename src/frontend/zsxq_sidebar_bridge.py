from dataclasses import dataclass
import html
import json
import urllib.error
import urllib.request


TAB_SCOPES = {
    "mp3": {
        "group_id": "28888112822211",
        "tab_id": "88844545452542",
        "tab_label": "调研纪要",
    },
    "pdf": {
        "group_id": "28888112822211",
        "tab_id": "51184248544214",
        "tab_label": "外资研报",
    },
}


@dataclass(frozen=True)
class BridgePage:
    status_code: int
    body: str


class ZsxqSidebarHtmlBridge:
    def __init__(self, backend_url: str, csrf_secret: bytes, timeout: float = 3.0):
        self.backend_url = backend_url.rstrip("/")
        self.csrf_secret = csrf_secret
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def render_history(self, kind: str) -> BridgePage:
        scope = TAB_SCOPES.get(kind)
        if scope is None:
            return self._error_page(400, "invalid_kind", "kind 只允许 mp3 或 pdf")
        status, payload = self._request_json(
            "POST",
            "/api/v1/zsxq/browser-import/history",
            {
                "kind": kind,
                "days": 3,
                "group_id": scope["group_id"],
                "tab_id": scope["tab_id"],
            },
        )
        if not 200 <= status < 300 or payload.get("success") is not True:
            return self._json_page(status, "历史查询失败", payload, pre_id="history-json")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        complete = "true" if data.get("complete") is True else "false"
        return self._json_page(
            200,
            f"知识星球 {scope['tab_label']} 最近 3 天历史",
            payload,
            summary=f"历史完整：{complete}",
            pre_id="history-json",
        )

    def _request_json(self, method: str, path: str, payload: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            f"{self.backend_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return response.status, self._decode_object(response.read())
        except urllib.error.HTTPError as error:
            try:
                return error.code, self._decode_object(error.read())
            except (UnicodeDecodeError, ValueError):
                return 502, self._invalid_response_payload()
        except (OSError, urllib.error.URLError):
            return 502, {"success": False, "code": "zsxq_backend_unavailable"}
        except (UnicodeDecodeError, ValueError):
            return 502, self._invalid_response_payload()

    def _error_page(self, status_code: int, code: str, message: str) -> BridgePage:
        return self._json_page(
            status_code,
            "历史查询失败",
            {"success": False, "code": code, "message": message},
            pre_id="history-json",
        )

    def _json_page(
        self,
        status_code: int,
        title: str,
        payload: dict,
        summary: str = "",
        pre_id: str = "bridge-json",
    ) -> BridgePage:
        escaped_summary = f"<p>{html.escape(summary)}</p>" if summary else ""
        body = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
</head>
<body>
  <main>
    <h1>{html.escape(title)}</h1>
    {escaped_summary}
    <pre id="{html.escape(pre_id, quote=True)}">{html.escape(json.dumps(payload, ensure_ascii=False, indent=2))}</pre>
  </main>
</body>
</html>
"""
        return BridgePage(status_code, body)

    @staticmethod
    def _decode_object(response_body: bytes) -> dict:
        payload = json.loads(response_body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("backend response must be an object")
        return payload

    @staticmethod
    def _invalid_response_payload() -> dict:
        return {"success": False, "code": "zsxq_backend_invalid_response"}
