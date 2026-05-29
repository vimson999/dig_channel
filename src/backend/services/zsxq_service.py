import hashlib
import json
import logging
import mimetypes
import base64
import re
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

    def post_json(self, cookie, path, body, params=None):
        cookie = self.normalize_cookie(cookie)
        params = params or {}
        signature, timestamp = self.generate_signature(path, params)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
            ),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Cookie": cookie,
            "Origin": "https://wx.zsxq.com",
            "Referer": "https://wx.zsxq.com/",
            "X-Signature": signature,
            "X-Timestamp": timestamp,
        }
        response = self.session.post(
            f"{self.BASE_URL}{path}",
            headers=headers,
            params=params,
            json=body,
            timeout=30,
        )
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
        blocked_key_tokens = {
            "avatar",
            "background",
            "cover",
            "thumbnail",
            "thumb",
            "image",
            "large",
            "small",
            "icon",
        }
        preferred_key_tokens = {
            "download",
            "file",
            "url",
            "link",
            "path",
        }

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
                    if any(token in lowered for token in blocked_key_tokens):
                        continue
                    if not any(token in lowered for token in preferred_key_tokens):
                        continue
                    priority = 0 if any(token in lowered for token in ("download", "file")) else 1
                    candidates.append((priority, value))

        return [url for _, url in sorted(candidates, key=lambda item: item[0])]

    def is_file_payload(self, payload):
        if not isinstance(payload, dict):
            return False
        file_keys = {
            "file_id",
            "fileid",
            "file_id_str",
            "hash_id",
            "hashid",
            "file_name",
            "filename",
            "size",
            "download_count",
            "downloads_count",
        }
        normalized_keys = {str(key).replace("-", "_").lower() for key in payload.keys()}
        if normalized_keys & file_keys:
            return True

        name = str(payload.get("name") or payload.get("title") or "")
        return bool(re.search(r"\.(pdf|mp3|m4a|wav|aac|docx?|xlsx?|pptx?|zip|rar|7z|csv|txt)$", name, re.IGNORECASE))

    def validate_download_response(self, file_info, response, url):
        expected_name = str(
            file_info.get("name")
            or file_info.get("title")
            or file_info.get("file_name")
            or ""
        )
        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if re.search(r"\.pdf($|\?)", expected_name, re.IGNORECASE) and content_type.startswith("image/"):
            raise ValueError(f"下载源返回图片而不是 PDF: {url}")

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
                self.validate_download_response(file_info, response, url)
                return response
            except (requests.RequestException, ValueError) as exc:
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
                if self.is_file_payload(current) and self.file_url_candidates(current):
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
        hashtag_id = (extra_params or {}).get("hashtag_id")
        if hashtag_id:
            return self.get_hashtag_topics(cookie, hashtag_id, count=count, end_time=end_time)

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

    def get_hashtag_topics(self, cookie, hashtag_id, count=20, end_time=None):
        path = f"/v2/hashtags/{hashtag_id}/topics"
        params = {
            "count": min(max(int(count), 1), 50),
        }
        if end_time:
            params["end_time"] = end_time

        result = self.request(cookie, path, params)
        topics = (result.get("resp_data") or {}).get("topics") or []
        if isinstance(topics, list):
            result.setdefault("resp_data", {})["next_end_time"] = self._oldest_topic_time(topics)
            result["resp_data"]["loaded_count"] = len(topics)
        return result

    def get_menus(self, cookie, group_id):
        return self.request(cookie, f"/v2/groups/{group_id}/menus", {})

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

    def create_upload(self, cookie, upload_type, filename, size, file_hash):
        payload = {
            "req_data": {
                "type": upload_type,
                "size": int(size),
                "name": filename,
                "hash": file_hash,
            }
        }
        return self.post_json(cookie, "/v2/uploads", payload)

    def upload_to_storage(self, upload_token, filename, content, content_type=None):
        content_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        policy_summary = self._decode_qiniu_policy_summary(upload_token)
        logger.info(
            "[ZSXQ_UPLOAD] qiniu upload start | filename=%s | size=%s | content_type=%s | policy=%s",
            filename,
            len(content or b""),
            content_type,
            policy_summary,
        )
        response = None
        attempted_hosts = []
        for endpoint in self._qiniu_upload_endpoints(policy_summary):
            attempted_hosts.append(endpoint)
            files = {
                "file": (filename, content, content_type),
            }
            response = requests.post(
                endpoint,
                data={"token": upload_token},
                files=files,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    )
                },
                timeout=180,
            )
            if response.status_code < 400:
                break

            suggested_endpoint = self._extract_qiniu_region_endpoint(response.text)
            if suggested_endpoint and suggested_endpoint not in attempted_hosts:
                logger.info(
                    "[ZSXQ_UPLOAD] qiniu suggested endpoint | from=%s | suggested=%s | filename=%s",
                    endpoint,
                    suggested_endpoint,
                    filename,
                )
                files = {
                    "file": (filename, content, content_type),
                }
                response = requests.post(
                    suggested_endpoint,
                    data={"token": upload_token},
                    files=files,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                        )
                    },
                    timeout=180,
                )
                attempted_hosts.append(suggested_endpoint)
                if response.status_code < 400:
                    break

        if response is None or response.status_code >= 400:
            body = response.text[:1000] if response is not None else "no response"
            status = response.status_code if response is not None else "NO_RESPONSE"
            logger.warning(
                "[ZSXQ_UPLOAD] qiniu upload failed | status=%s | filename=%s | endpoints=%s | response=%s",
                status,
                filename,
                attempted_hosts,
                body,
            )
            raise ValueError(f"七牛上传失败 status={status}: {body}")

        logger.info(
            "[ZSXQ_UPLOAD] qiniu upload response | status=%s | filename=%s | response=%s",
            response.status_code,
            filename,
            response.text[:1000],
        )
        try:
            return response.json()
        except ValueError as exc:
            raise ValueError(f"上传存储返回非 JSON: {response.text[:200]}") from exc

    def _qiniu_upload_endpoints(self, policy_summary):
        scope = (policy_summary or {}).get("scope")
        endpoints = []
        if scope == "files":
            endpoints.append("https://up-z1.qiniup.com")
        endpoints.extend([
            "https://upload.qiniup.com",
            "https://up.qiniup.com",
            "https://up-z0.qiniup.com",
            "https://up-z1.qiniup.com",
            "https://up-z2.qiniup.com",
            "https://up-na0.qiniup.com",
            "https://up-as0.qiniup.com",
        ])
        deduped = []
        for endpoint in endpoints:
            if endpoint not in deduped:
                deduped.append(endpoint)
        return deduped

    def _extract_qiniu_region_endpoint(self, text):
        match = re.search(r"use\\s+([a-z0-9.-]+\\.qiniup\\.com)", text or "", re.IGNORECASE)
        if not match:
            return None
        return f"https://{match.group(1)}"

    def _decode_qiniu_policy_summary(self, upload_token):
        try:
            policy_b64 = upload_token.split(":")[-1]
            policy_b64 += "=" * ((4 - len(policy_b64) % 4) % 4)
            policy = json.loads(base64.urlsafe_b64decode(policy_b64).decode("utf-8"))
            return {
                "scope": policy.get("scope"),
                "deadline": policy.get("deadline"),
                "callbackHost": policy.get("callbackHost"),
                "callbackUrl": policy.get("callbackUrl"),
                "saveKey": policy.get("saveKey"),
                "forceSaveKey": policy.get("forceSaveKey"),
            }
        except Exception as exc:
            return {"decode_error": str(exc)}

    def find_first_key(self, payload, keys):
        stack = [payload]
        seen = set()
        normalized_keys = {key.lower() for key in keys}

        while stack:
            current = stack.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))

            if isinstance(current, dict):
                for key, value in current.items():
                    if key.lower() in normalized_keys and value not in (None, ""):
                        return value
                    if isinstance(value, (dict, list)):
                        stack.append(value)
            elif isinstance(current, list):
                stack.extend(item for item in current if isinstance(item, (dict, list)))

        return None

    def upload_topic_asset(self, cookie, filename, content, content_type=None):
        content_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        upload_type = "image" if content_type.startswith("image/") else "file"
        file_hash = hashlib.sha256(content).hexdigest()
        token_result = self.create_upload(cookie, upload_type, filename, len(content), file_hash)
        if token_result.get("succeeded") is False:
            code = token_result.get("code") or "UNKNOWN"
            message = token_result.get("error") or token_result.get("message") or token_result
            raise ValueError(f"申请上传失败 code={code}: {message}")

        upload_token = (token_result.get("resp_data") or {}).get("upload_token")
        if not upload_token:
            raise ValueError("申请上传成功但没有返回 upload_token")

        storage_result = self.upload_to_storage(upload_token, filename, content, content_type)
        asset_id = self.find_first_key(
            storage_result,
            ("file_id", "fileId", "image_id", "imageId", "id"),
        )
        if not asset_id:
            raise ValueError(f"上传成功但没有找到附件 ID: {storage_result}")

        return {
            "type": upload_type,
            "id": asset_id,
            "name": filename,
            "size": len(content),
            "hash": file_hash,
            "storage_result": storage_result,
        }

    def build_topic_text(self, text, hashtag_id=None, hashtag_title=None):
        text = (text or "").strip()
        if hashtag_id and hashtag_title:
            title = str(hashtag_title).strip().strip("#")
            encoded = urlencode({"title": f"#{title}#"})[6:]
            prefix = f'<e type="hashtag" hid="{hashtag_id}" title="{encoded}" />'
            return f"{prefix} \n{text}" if text else f"{prefix} \n"
        return text

    def publish_topic(self, cookie, group_id, text, file_ids=None, image_ids=None):
        payload = {
            "req_data": {
                "type": "topic",
                "text": text or "",
                "image_ids": image_ids or [],
                "file_ids": file_ids or [],
                "mentioned_user_ids": [],
            }
        }
        return self.post_json(cookie, f"/v2/groups/{group_id}/topics", payload)

    def upload_and_publish_topic(self, cookie, group_id, text, uploaded_files, hashtag_id=None, hashtag_title=None):
        file_ids = []
        image_ids = []
        uploaded = []

        for item in uploaded_files:
            filename = item.get("filename") or "upload.bin"
            content = item.get("content") or b""
            if not content:
                continue
            asset = self.upload_topic_asset(cookie, filename, content, item.get("content_type"))
            uploaded.append(asset)
            if asset["type"] == "image":
                image_ids.append(asset["id"])
            else:
                file_ids.append(asset["id"])

        topic_text = self.build_topic_text(text, hashtag_id=hashtag_id, hashtag_title=hashtag_title)
        if not topic_text.strip() and not file_ids and not image_ids:
            raise ValueError("正文和附件不能同时为空")

        publish_result = self.publish_topic(cookie, group_id, topic_text, file_ids=file_ids, image_ids=image_ids)
        return {
            "uploaded": uploaded,
            "publish": publish_result,
        }


zsxq_service = ZsxqService()
