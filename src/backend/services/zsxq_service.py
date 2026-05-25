import hashlib
import json
import logging
import time
from urllib.parse import urlparse
from urllib.parse import urlencode

import requests


logger = logging.getLogger(__name__)


class ZsxqService:
    """Small proxy client for testing ZSXQ mobile API access.

    The caller supplies a Cookie per request. We do not persist or log it.
    """

    BASE_URL = "https://api.zsxq.com"
    SECRET = "zsxqapi2020"

    def __init__(self):
        self.session = requests.Session()

    def generate_signature(self, path, params=None):
        common_params = {
            "app_version": "3.11.0",
            "platform": "ios",
            "timestamp": str(int(time.time() * 1000)),
        }
        all_params = {**common_params, **(params or {})}
        params_str = urlencode(sorted(all_params.items()))
        sign_str = f"{path}&{params_str}&{self.SECRET}"
        return hashlib.md5(sign_str.encode("utf-8")).hexdigest(), common_params["timestamp"]

    def normalize_cookie(self, cookie):
        """Accept either a Cookie header string or browser-exported cookie JSON."""
        cookie = (cookie or "").strip()
        if not cookie:
            raise ValueError("Cookie 缺失或格式不完整")

        if cookie.startswith("[") or cookie.startswith("{"):
            try:
                parsed = json.loads(cookie)
            except json.JSONDecodeError as exc:
                raise ValueError("Cookie JSON 格式不正确") from exc

            items = parsed if isinstance(parsed, list) else [parsed]
            pairs = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                value = str(item.get("value") or "").strip()
                if name and value:
                    pairs.append(f"{name}={value}")
            cookie = "; ".join(pairs)

        cookie = " ".join(cookie.splitlines()).strip()
        if "\r" in cookie or "\n" in cookie:
            raise ValueError("Cookie 中不能包含换行符")
        if "zsxq_access_token=" not in cookie:
            raise ValueError("Cookie 缺少 zsxq_access_token")
        return cookie

    def request(self, cookie, path, params=None):
        cookie = self.normalize_cookie(cookie)

        params = params or {}
        signature, timestamp = self.generate_signature(path, params)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
            ),
            "Accept": "application/json",
            "Cookie": cookie,
            "Origin": "https://wx.zsxq.com",
            "Referer": "https://wx.zsxq.com/",
            "X-Signature": signature,
            "X-Timestamp": timestamp,
        }

        url = f"{self.BASE_URL}{path}"
        response = self.session.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        return response.json()

    def request_raw(self, cookie, path, params=None, accept="application/json"):
        cookie = self.normalize_cookie(cookie)
        params = params or {}
        signature, timestamp = self.generate_signature(path, params)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
            ),
            "Accept": accept,
            "Cookie": cookie,
            "Origin": "https://wx.zsxq.com",
            "Referer": "https://wx.zsxq.com/",
            "X-Signature": signature,
            "X-Timestamp": timestamp,
        }
        response = self.session.get(f"{self.BASE_URL}{path}", headers=headers, params=params, timeout=60, stream=True)
        response.raise_for_status()
        return response

    def signed_headers(self, cookie, path, params=None):
        cookie = self.normalize_cookie(cookie)
        signature, timestamp = self.generate_signature(path, params or {})
        return {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
            ),
            "Accept": "application/json",
            "Cookie": cookie,
            "Origin": "https://wx.zsxq.com",
            "Referer": "https://wx.zsxq.com/",
            "X-Signature": signature,
            "X-Timestamp": timestamp,
        }

    def file_url_candidates(self, file_info):
        candidates = []
        stack = [file_info] if isinstance(file_info, dict) else []
        seen = set()

        while stack:
            current = stack.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))

            for key, value in current.items():
                if isinstance(value, dict):
                    stack.append(value)
                elif isinstance(value, list):
                    stack.extend(item for item in value if isinstance(item, dict))
                elif isinstance(value, str) and value.startswith(("http://", "https://")):
                    lowered = key.lower()
                    priority = 0 if any(token in lowered for token in ("download", "url", "link")) else 1
                    candidates.append((priority, value))

        return [url for _, url in sorted(candidates, key=lambda item: item[0])]

    def file_id_candidates(self, file_info):
        candidates = []
        stack = [file_info] if isinstance(file_info, dict) else []
        seen = set()
        id_keys = {"file_id", "fileid", "fileidstr", "fileid_str", "id", "hash_id", "hashid"}

        while stack:
            current = stack.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))

            for key, value in current.items():
                lowered = key.replace("-", "_").lower()
                if isinstance(value, dict):
                    stack.append(value)
                elif isinstance(value, list):
                    stack.extend(item for item in value if isinstance(item, dict))
                elif lowered in id_keys and value is not None:
                    value_str = str(value).strip()
                    if value_str:
                        candidates.append(value_str)

        deduped = []
        for candidate in candidates:
            if candidate not in deduped:
                deduped.append(candidate)
        return deduped

    def fetch_file(self, cookie, file_info):
        """Fetch one file from a URL exposed in the topic payload."""
        urls = self.file_url_candidates(file_info)
        if not urls:
            raise ValueError("附件里没有找到可下载 URL")

        last_error = None
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
            ),
            "Cookie": self.normalize_cookie(cookie),
            "Referer": "https://wx.zsxq.com/",
        }

        for url in urls:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                continue
            try:
                response = self.session.get(url, headers=headers, timeout=60, stream=True)
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc

        raise ValueError(f"附件下载失败: {last_error}")

    def fetch_file_by_id(self, cookie, file_info):
        """Try common ZSXQ file endpoints using ids exposed in file metadata."""
        ids = self.file_id_candidates(file_info)
        if not ids:
            raise ValueError("附件里没有找到 file_id")

        endpoint_templates = [
            "/v2/files/{file_id}/download_url",
            "/v2/files/{file_id}/download",
            "/v2/files/{file_id}",
        ]
        last_error = None

        for file_id in ids[:5]:
            for template in endpoint_templates:
                path = template.format(file_id=file_id)
                try:
                    response = self.request_raw(cookie, path)
                    content_type = response.headers.get("Content-Type", "")
                    if "application/json" not in content_type:
                        return response

                    data = response.json()
                    urls = self.file_url_candidates(data)
                    if urls:
                        richer_info = dict(file_info)
                        richer_info["_download_probe"] = data
                        return self.fetch_file(cookie, richer_info)
                except Exception as exc:
                    last_error = exc

        raise ValueError(f"通过 file_id 获取下载地址失败: {last_error}")

    def find_file_in_topic(self, topic_detail, target_file):
        """Find a richer file payload in topic detail by file id or name."""
        target_id = str(
            target_file.get("file_id")
            or target_file.get("id")
            or target_file.get("fileId")
            or ""
        )
        target_name = str(
            target_file.get("name")
            or target_file.get("title")
            or target_file.get("file_name")
            or ""
        )

        matches = []
        stack = [topic_detail] if isinstance(topic_detail, dict) else []
        seen = set()

        while stack:
            current = stack.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))

            if isinstance(current, dict):
                if self.file_url_candidates(current):
                    current_id = str(current.get("file_id") or current.get("id") or current.get("fileId") or "")
                    current_name = str(current.get("name") or current.get("title") or current.get("file_name") or "")
                    if (target_id and current_id == target_id) or (target_name and current_name == target_name):
                        return current
                    matches.append(current)

                for value in current.values():
                    if isinstance(value, (dict, list)):
                        stack.append(value)
            elif isinstance(current, list):
                stack.extend(item for item in current if isinstance(item, (dict, list)))

        if len(matches) == 1:
            return matches[0]
        return None

    def fetch_topic_file(self, cookie, topic_id, file_info):
        """Fetch an attachment, falling back to topic detail when list payload is shallow."""
        try:
            return self.fetch_file(cookie, file_info)
        except ValueError as first_error:
            try:
                return self.fetch_file_by_id(cookie, file_info)
            except ValueError:
                if not topic_id:
                    raise first_error

        detail = self.get_topic(cookie, topic_id)
        rich_file = self.find_file_in_topic(detail, file_info)
        if not rich_file:
            try:
                return self.fetch_file_by_id(cookie, file_info)
            except ValueError:
                raise ValueError("主题详情里也没有找到该附件的可下载 URL")

        try:
            return self.fetch_file(cookie, rich_file)
        except ValueError:
            return self.fetch_file_by_id(cookie, rich_file)

    def get_groups(self, cookie, count=50):
        return self.request(cookie, "/v2/groups", {"count": min(int(count), 50)})

    def get_topics(self, cookie, group_id, count=20, scope="all", end_time=None, extra_params=None):
        path = f"/v2/groups/{group_id}/topics"
        params = {
            "count": min(max(int(count), 1), 50),
            "scope": scope or "all",
        }
        if end_time:
            params["end_time"] = end_time

        allowed_extra_keys = {
            "topic_type",
            "tag_id",
            "hashtag_id",
            "category_id",
            "tab_id",
            "scope",
            "sort",
            "filter",
        }
        for key, value in (extra_params or {}).items():
            if key in allowed_extra_keys and value not in (None, ""):
                params[key] = value

        result = self.request(cookie, path, params)
        topics = (result.get("resp_data") or {}).get("topics") or []
        if isinstance(topics, list):
            result.setdefault("resp_data", {})["next_end_time"] = self._oldest_topic_time(topics)
            result["resp_data"]["loaded_count"] = len(topics)
        return result

    def _oldest_topic_time(self, topics):
        times = [
            topic.get("create_time") or (topic.get("talk") or {}).get("create_time")
            for topic in topics
            if isinstance(topic, dict)
        ]
        times = [item for item in times if item]
        return min(times) if times else None

    def get_topic(self, cookie, topic_id):
        return self.request(cookie, f"/v2/topics/{topic_id}", {})


zsxq_service = ZsxqService()
