import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

import requests
from sqlalchemy import or_
from sqlalchemy.orm import load_only
from sqlalchemy.orm.attributes import flag_modified

from models import db
from models.external_document import ExternalDocument, ZsxqTopicExternalDocument
from models.task import PublicationTask
from models.zsxq_sync import ZsxqTopic
from services import platform_credential_service as credential_service


logger = logging.getLogger(__name__)

FEISHU_HOST_HINTS = ("feishu.cn", "larksuite.com")
URL_RE = re.compile(r"https?://[^\s<>'\"，。；;、）)】\]}]+", re.I)
MAX_FETCH_CHARS = 350_000
MAX_SUMMARY_INPUT_CHARS = 28_000
MAX_TTS_CHARS = 12_000
TRACKING_QUERY_KEYS = {"from", "from_copylink", "share_token", "timestamp", "utm_source", "utm_medium", "utm_campaign"}


class ExternalDocumentError(ValueError):
    pass


def _resolve_feishu_cookie(*, account_id: Optional[str] = None, required: bool = True) -> Dict[str, Any]:
    resolver = getattr(credential_service, "resolve_feishu_cookie", None)
    if not callable(resolver):
        raise ExternalDocumentError(
            "飞书凭据解析函数未加载。当前 Celery worker 可能仍缓存旧代码，请滚动重启 worker 后重试。"
        )
    return resolver(account_id=account_id, required=required)


def _mark_credential_success(credential_id: Optional[str]) -> None:
    marker = getattr(credential_service, "mark_credential_success", None)
    if callable(marker):
        marker(credential_id)


def _mark_credential_error(credential_id: Optional[str], error: Any, *, expired: bool = False) -> None:
    marker = getattr(credential_service, "mark_credential_error", None)
    if callable(marker):
        marker(credential_id, error, expired=expired)


def _is_auth_error(error: Any) -> bool:
    checker = getattr(credential_service, "is_auth_error", None)
    return bool(checker(error)) if callable(checker) else False


def _credential_error(message: str) -> Exception:
    error_cls = getattr(credential_service, "CredentialError", ExternalDocumentError)
    return error_cls(message)


def extract_external_links_from_topic(topic: Any) -> List[Dict[str, str]]:
    """Extract supported external links from a ZSXQ topic record or payload."""
    chunks = []
    if isinstance(topic, ZsxqTopic):
        chunks.extend([
            ("title", topic.title or ""),
            ("content", topic.content or ""),
            ("raw_payload", _json_text(topic.raw_payload)),
        ])
    elif isinstance(topic, dict):
        chunks.append(("payload", _json_text(topic)))
    else:
        chunks.append(("text", str(topic or "")))

    found: Dict[str, Dict[str, str]] = {}
    for source, text in chunks:
        for candidate in _iter_candidate_texts(text):
            for url in URL_RE.findall(candidate):
                provider = provider_for_url(url)
                if not provider:
                    continue
                canonical = canonicalize_url(url)
                if not canonical:
                    continue
                key = url_hash(canonical)
                found.setdefault(key, {
                    "provider": provider,
                    "document_type": document_type_for_url(canonical),
                    "source_url": _trim_url(url),
                    "canonical_url": canonical,
                    "url_hash": key,
                    "link_text": link_text_for_url(candidate, url),
                    "link_source": source,
                })
    return list(found.values())


def provider_for_url(url: str) -> Optional[str]:
    host = urlsplit(str(url or "").strip()).netloc.lower()
    if any(hint in host for hint in FEISHU_HOST_HINTS):
        return "feishu"
    return None


def document_type_for_url(url: str) -> Optional[str]:
    path = urlsplit(str(url or "")).path.strip("/")
    if not path:
        return None
    head = path.split("/", 1)[0].lower()
    if head in {"docx", "docs", "doc", "wiki", "sheets", "base", "mindnotes", "file"}:
        return head
    return head[:40]


def canonicalize_url(url: str) -> str:
    text = unescape(unquote(str(url or "").strip()))
    text = _trim_url(text)
    parts = urlsplit(text)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return ""
    scheme = "https"
    netloc = parts.netloc.lower()
    path = quote(unquote(parts.path or "/"), safe="/:@")
    kept_query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key and key.lower() not in TRACKING_QUERY_KEYS
    ]
    query = urlencode(kept_query, doseq=True)
    return urlunsplit((scheme, netloc, path.rstrip("/") or "/", query, ""))


def url_hash(canonical_url: str) -> str:
    return hashlib.sha256(str(canonical_url or "").encode("utf-8")).hexdigest()


def content_hash(text: str) -> str:
    return hashlib.sha256(_normalize_text(text).encode("utf-8")).hexdigest()


class ExternalDocumentService:
    def scan_topic_record(self, topic: ZsxqTopic, *, commit: bool = False) -> List[ZsxqTopicExternalDocument]:
        links = extract_external_links_from_topic(topic)
        if not links:
            return []

        created: List[ZsxqTopicExternalDocument] = []
        for link in links:
            document = self._upsert_document_shell(link)
            relation = ZsxqTopicExternalDocument.query.filter_by(
                group_id=topic.group_id,
                tab_id=topic.tab_id,
                topic_id=topic.topic_id,
                url_hash=link["url_hash"],
            ).first()
            if not relation:
                relation = ZsxqTopicExternalDocument(
                    group_id=topic.group_id,
                    group_name=topic.group_name,
                    tab_id=topic.tab_id,
                    tab_label=topic.tab_label,
                    topic_id=topic.topic_id,
                    topic_title=topic.title,
                    provider=link["provider"],
                    external_document_id=document.id,
                    source_url=link["source_url"],
                    canonical_url=link["canonical_url"],
                    url_hash=link["url_hash"],
                    link_text=link.get("link_text") or None,
                    link_source=link.get("link_source") or None,
                )
                db.session.add(relation)
            else:
                relation.group_name = topic.group_name
                relation.tab_label = topic.tab_label
                relation.topic_title = topic.title
                relation.provider = link["provider"]
                relation.external_document_id = document.id
                relation.source_url = link["source_url"]
                relation.canonical_url = link["canonical_url"]
                relation.link_text = link.get("link_text") or relation.link_text
                relation.link_source = link.get("link_source") or relation.link_source
                relation.updated_at = datetime.utcnow()
            created.append(relation)

        if commit:
            db.session.commit()
        return created

    def scan_existing_topics(
        self,
        *,
        group_id: Optional[str] = None,
        topic_id: Optional[str] = None,
        limit: Optional[int] = None,
        commit_every: int = 200,
    ) -> Dict[str, Any]:
        candidate_filter = or_(
            ZsxqTopic.content.ilike("%feishu.cn%"),
            ZsxqTopic.content.ilike("%larksuite.com%"),
            ZsxqTopic.title.ilike("%feishu.cn%"),
            ZsxqTopic.title.ilike("%larksuite.com%"),
        )
        id_query = ZsxqTopic.query.with_entities(ZsxqTopic.id).filter(candidate_filter).order_by(ZsxqTopic.id.asc())
        query = ZsxqTopic.query.options(
            load_only(
                ZsxqTopic.id,
                ZsxqTopic.group_id,
                ZsxqTopic.group_name,
                ZsxqTopic.tab_id,
                ZsxqTopic.tab_label,
                ZsxqTopic.topic_id,
                ZsxqTopic.title,
                ZsxqTopic.content,
                ZsxqTopic.raw_payload,
            )
        ).order_by(ZsxqTopic.id.asc())
        if group_id:
            id_query = id_query.filter(ZsxqTopic.group_id == str(group_id))
            query = query.filter(ZsxqTopic.group_id == str(group_id))
        if topic_id:
            id_query = id_query.filter(ZsxqTopic.topic_id == str(topic_id))
            query = query.filter(ZsxqTopic.topic_id == str(topic_id))
        scanned = 0
        topics_with_links = 0
        relations = 0
        documents = set()
        batch_size = max(min(int(commit_every or 200), 500), 1)
        target_limit = max(int(limit), 1) if limit else None
        if target_limit:
            id_query = id_query.limit(target_limit)
        candidate_ids = [row[0] for row in id_query.all()]
        for start in range(0, len(candidate_ids), batch_size):
            ids = candidate_ids[start:start + batch_size]
            batch = query.filter(ZsxqTopic.id.in_(ids)).all()
            if not batch:
                continue
            for topic in batch:
                scanned += 1
                linked = self.scan_topic_record(topic, commit=False)
                if linked:
                    topics_with_links += 1
                    relations += len(linked)
                    documents.update(item.url_hash for item in linked)
            db.session.commit()
            db.session.expunge_all()
        return {
            "scanned_topics": scanned,
            "topics_with_links": topics_with_links,
            "relations": relations,
            "documents": len(documents),
        }

    def links_for_topics(self, group_id: str, topics: Iterable[ZsxqTopic]) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
        topic_keys = [(str(topic.tab_id), str(topic.topic_id)) for topic in topics if topic.topic_id]
        if not topic_keys:
            return {}
        topic_ids = sorted({topic_id for _, topic_id in topic_keys})
        rows = (
            ZsxqTopicExternalDocument.query.filter(
                ZsxqTopicExternalDocument.group_id == str(group_id),
                ZsxqTopicExternalDocument.topic_id.in_(topic_ids),
            )
            .options(load_only(
                ZsxqTopicExternalDocument.id,
                ZsxqTopicExternalDocument.group_id,
                ZsxqTopicExternalDocument.group_name,
                ZsxqTopicExternalDocument.tab_id,
                ZsxqTopicExternalDocument.tab_label,
                ZsxqTopicExternalDocument.topic_id,
                ZsxqTopicExternalDocument.topic_title,
                ZsxqTopicExternalDocument.provider,
                ZsxqTopicExternalDocument.external_document_id,
                ZsxqTopicExternalDocument.source_url,
                ZsxqTopicExternalDocument.canonical_url,
                ZsxqTopicExternalDocument.url_hash,
                ZsxqTopicExternalDocument.link_text,
                ZsxqTopicExternalDocument.link_source,
                ZsxqTopicExternalDocument.created_at,
                ZsxqTopicExternalDocument.updated_at,
            ))
            .all()
        )
        documents = self._documents_by_id([row.external_document_id for row in rows])
        self._sync_tts_projection_for_documents(documents.values(), commit=True)
        grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        valid_topic_keys = set(topic_keys)
        for row in rows:
            key = (str(row.tab_id), str(row.topic_id))
            if key not in valid_topic_keys:
                continue
            document = documents.get(row.external_document_id)
            payload = row.to_dict(include_document=False)
            if document:
                payload["document"] = document.compact_dict()
            grouped.setdefault(key, []).append(payload)
        return grouped

    def get_document(self, document_id: str) -> ExternalDocument:
        document = ExternalDocument.query.get(document_id)
        if not document:
            raise ExternalDocumentError("外链文档不存在")
        self._sync_tts_projection_for_documents([document], commit=True)
        return document

    def fetch_document(
        self,
        document_id: str,
        *,
        credential_id: Optional[str] = None,
        force: bool = False,
    ) -> ExternalDocument:
        document = self.get_document(document_id)
        if document.fetch_status == "fetched" and document.content_text and not force:
            return document
        if document.provider != "feishu":
            raise ExternalDocumentError(f"暂不支持抓取 {document.provider}")

        document.fetch_status = "fetching"
        document.fetch_error = None
        db.session.commit()

        credential = {}
        try:
            credential = _resolve_feishu_cookie(account_id=credential_id, required=True)
            fetched = self._fetch_feishu_document(document, credential["cookie"])
            now = datetime.utcnow()
            document.title = fetched.get("title") or document.title
            document.author_name = fetched.get("author_name") or document.author_name
            document.content_text = _normalize_text(fetched.get("content_text") or "")
            document.content_html = fetched.get("content_html") or None
            document.content_chars = len(document.content_text or "")
            document.content_hash = content_hash(document.content_text or "")
            document.fetch_status = "fetched" if document.content_text else "empty"
            document.fetch_error = None if document.content_text else "飞书页面返回成功，但没有抽取到正文。"
            document.fetched_at = now
            document.fetch_credential_id = credential.get("credential_id")
            document.raw_payload = fetched.get("raw_payload") or {}
            if document.summary_status in {"completed", "failed"} and _document_summary_stale(document):
                document.summary_status = "pending"
                document.summary_error = None
            _mark_credential_success(credential.get("credential_id"))
            db.session.commit()
            return document
        except Exception as exc:
            db.session.rollback()
            document = ExternalDocument.query.get(document_id)
            if document:
                document.fetch_status = "failed"
                document.fetch_error = str(exc)[:2000]
                if credential.get("credential_id") and _is_auth_error(exc):
                    _mark_credential_error(credential.get("credential_id"), exc, expired=True)
                db.session.commit()
            raise

    def summarize_document(self, document_id: str, *, force: bool = False) -> ExternalDocument:
        document = self.get_document(document_id)
        if document.summary_status == "completed" and document.summary_text and not force and not _document_summary_stale(document):
            return document
        if not document.content_text:
            raise ExternalDocumentError("飞书正文尚未抓取，无法总结")

        document.summary_status = "summarizing"
        document.summary_error = None
        db.session.commit()
        try:
            summary = self._deepseek_summary(document)
            document.summary_text = summary
            document.summary_model = _summary_model()
            document.summary_status = "completed"
            document.summary_error = None
            document.summarized_at = datetime.utcnow()
            raw_payload = dict(document.raw_payload or {})
            raw_payload["summarized_content_hash"] = document.content_hash
            document.raw_payload = raw_payload
            flag_modified(document, "raw_payload")
            db.session.commit()
            return document
        except Exception as exc:
            db.session.rollback()
            document = ExternalDocument.query.get(document_id)
            if document:
                document.summary_status = "failed"
                document.summary_error = str(exc)[:2000]
                db.session.commit()
            raise

    def submit_tts(self, document_id: str, *, force: bool = False, user_id: Optional[str] = None) -> ExternalDocument:
        document = self.get_document(document_id)
        if document.tts_task_id and document.tts_status in {"queued", "running", "completed"} and not force:
            return document

        text = _tts_text_for_document(document)
        if not text:
            raise ExternalDocumentError("没有可朗读文本，请先抓取或总结")

        from services.doubao_browser_tts import FEATURE_NAME, QUEUE_NAME, status_payload
        from tasks.doubao_browser_tts import process_doubao_browser_tts_job

        provider = status_payload()
        if not provider.get("configured"):
            raise ExternalDocumentError("豆包浏览器 TTS 未配置，无法提交朗读任务")

        task = PublicationTask(
            status="pending",
            current_step="queued",
            progress=0,
            params={
                "feature": FEATURE_NAME,
                "user_id": user_id,
                "source_feature": "zsxq_external_document",
                "source_task_id": document.id,
                "doubao_browser_tts": {
                    "text": text[:MAX_TTS_CHARS],
                    "metadata": {
                        "source": "zsxq_external_document",
                        "external_document_id": document.id,
                        "provider": document.provider,
                        "title": document.title,
                        "url": document.canonical_url,
                        "text_hash": hashlib.sha256(text[:MAX_TTS_CHARS].encode("utf-8")).hexdigest(),
                    },
                },
            },
            result={
                "metadata": {
                    "source": "zsxq_external_document",
                    "external_document_id": document.id,
                    "title": document.title,
                },
                "audio": None,
                "logs": [],
            },
        )
        db.session.add(task)
        db.session.flush()
        try:
            celery_task = process_doubao_browser_tts_job.apply_async(args=[task.id], queue=QUEUE_NAME, retry=False)
            task.celery_task_id = celery_task.id
        except Exception as exc:
            task.status = "failed"
            task.current_step = "failed"
            task.error_message = f"任务入队失败: {exc}"
            task.completed_at = datetime.utcnow()
            document.tts_status = "failed"
            document.tts_error = task.error_message
            db.session.commit()
            raise ExternalDocumentError(task.error_message) from exc

        document.tts_task_id = task.id
        document.tts_status = "queued"
        document.tts_error = None
        document.tts_audio_path = None
        document.tts_submitted_at = datetime.utcnow()
        document.tts_completed_at = None
        db.session.commit()
        return document

    def backfill(
        self,
        *,
        group_id: Optional[str] = None,
        scan_limit: Optional[int] = None,
        scan: bool = True,
        fetch: bool = False,
        summarize: bool = False,
        document_limit: Optional[int] = None,
        credential_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        result = {}
        if scan:
            result["scan"] = self.scan_existing_topics(group_id=group_id, limit=scan_limit)
        if not (fetch or summarize):
            return result

        query = ExternalDocument.query.filter(ExternalDocument.provider == "feishu").order_by(
            ExternalDocument.updated_at.asc()
        )
        if fetch:
            query = query.filter(ExternalDocument.fetch_status.in_(["pending", "failed", "empty"]))
        elif summarize:
            query = query.filter(
                ExternalDocument.fetch_status == "fetched",
                ExternalDocument.summary_status.in_(["pending", "failed"]),
            )
        if document_limit:
            query = query.limit(max(int(document_limit), 1))

        processed = []
        for document in query.all():
            item = {"id": document.id, "url": document.canonical_url}
            try:
                if fetch:
                    document = self.fetch_document(document.id, credential_id=credential_id)
                    item["fetch_status"] = document.fetch_status
                if summarize and document.fetch_status == "fetched":
                    document = self.summarize_document(document.id)
                    item["summary_status"] = document.summary_status
                item["ok"] = True
            except Exception as exc:
                item["ok"] = False
                item["error"] = str(exc)
                logger.warning("[EXTERNAL_DOC] backfill item failed | doc=%s | error=%s", document.id, exc)
            processed.append(item)
        result["documents"] = processed
        return result

    def _upsert_document_shell(self, link: Dict[str, str]) -> ExternalDocument:
        document = ExternalDocument.query.filter_by(url_hash=link["url_hash"]).first()
        if not document:
            document = ExternalDocument(
                provider=link["provider"],
                document_type=link.get("document_type"),
                source_url=link["source_url"],
                canonical_url=link["canonical_url"],
                url_hash=link["url_hash"],
                fetch_status="pending",
                summary_status="pending",
                tts_status="not_submitted",
                raw_payload={},
            )
            db.session.add(document)
            db.session.flush()
            return document
        document.provider = link["provider"]
        document.document_type = link.get("document_type") or document.document_type
        document.source_url = link["source_url"] or document.source_url
        document.canonical_url = link["canonical_url"] or document.canonical_url
        document.updated_at = datetime.utcnow()
        return document

    def _documents_by_id(self, document_ids: Iterable[str]) -> Dict[str, ExternalDocument]:
        ids = [item for item in set(document_ids or []) if item]
        if not ids:
            return {}
        rows = ExternalDocument.query.filter(ExternalDocument.id.in_(ids)).options(load_only(
            ExternalDocument.id,
            ExternalDocument.provider,
            ExternalDocument.document_type,
            ExternalDocument.source_url,
            ExternalDocument.canonical_url,
            ExternalDocument.url_hash,
            ExternalDocument.title,
            ExternalDocument.content_chars,
            ExternalDocument.fetch_status,
            ExternalDocument.fetch_error,
            ExternalDocument.fetched_at,
            ExternalDocument.summary_status,
            ExternalDocument.summary_text,
            ExternalDocument.summary_model,
            ExternalDocument.summary_error,
            ExternalDocument.summarized_at,
            ExternalDocument.tts_status,
            ExternalDocument.tts_task_id,
            ExternalDocument.tts_audio_path,
            ExternalDocument.tts_error,
            ExternalDocument.tts_submitted_at,
            ExternalDocument.tts_completed_at,
            ExternalDocument.created_at,
            ExternalDocument.updated_at,
        )).all()
        return {row.id: row for row in rows}

    def _sync_tts_projection_for_documents(self, documents: Iterable[ExternalDocument], *, commit: bool = False) -> None:
        docs = [doc for doc in documents or [] if doc and doc.tts_task_id]
        if not docs:
            return
        tasks = PublicationTask.query.filter(
            PublicationTask.id.in_([doc.tts_task_id for doc in docs])
        ).options(load_only(
            PublicationTask.id,
            PublicationTask.status,
            PublicationTask.result,
            PublicationTask.error_message,
            PublicationTask.completed_at,
        )).all()
        task_map = {task.id: task for task in tasks}
        changed = False
        for document in docs:
            task = task_map.get(document.tts_task_id)
            if not task:
                continue
            status = _public_tts_status(task.status)
            result = task.result if isinstance(task.result, dict) else {}
            audio = result.get("audio") if isinstance(result.get("audio"), dict) else {}
            audio_path = audio.get("wav_path") or document.tts_audio_path
            error = task.error_message or None
            completed_at = task.completed_at if task.status == "completed" else document.tts_completed_at
            if (
                document.tts_status != status
                or document.tts_audio_path != audio_path
                or document.tts_error != error
                or document.tts_completed_at != completed_at
            ):
                document.tts_status = status
                document.tts_audio_path = audio_path
                document.tts_error = error
                document.tts_completed_at = completed_at
                document.updated_at = datetime.utcnow()
                changed = True
        if changed and commit:
            db.session.commit()

    def _fetch_feishu_document(self, document: ExternalDocument, cookie: str) -> Dict[str, Any]:
        session = requests.Session()
        headers = {
            "User-Agent": os.environ.get(
                "FEISHU_FETCH_USER_AGENT",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            "Cookie": cookie,
        }
        response = session.get(
            document.canonical_url,
            headers=headers,
            timeout=_int_env("FEISHU_FETCH_TIMEOUT", 30),
            allow_redirects=True,
        )
        if response.status_code in {401, 403}:
            raise _credential_error(f"飞书访问被拒绝 HTTP {response.status_code}")
        if response.status_code >= 400:
            raise ExternalDocumentError(f"飞书抓取失败 HTTP {response.status_code}: {response.text[:300]}")
        response.encoding = response.encoding or "utf-8"
        html = response.text[:MAX_FETCH_CHARS]
        title = _extract_html_title(html) or document.title
        content_text = _extract_readable_text(html)
        return {
            "title": title,
            "content_text": content_text,
            "content_html": None,
            "raw_payload": {
                "status_code": response.status_code,
                "final_url": response.url,
                "content_type": response.headers.get("content-type"),
                "fetched_at": datetime.utcnow().isoformat() + "Z",
                "extractor": "html_text_v1",
            },
        }

    def _deepseek_summary(self, document: ExternalDocument) -> str:
        api_key = os.environ.get("EXTERNAL_DOCUMENT_SUMMARY_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise ExternalDocumentError("缺少 DEEPSEEK_API_KEY，无法生成 DS v4 flash 总结")
        base_url = os.environ.get("EXTERNAL_DOCUMENT_SUMMARY_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        model = _summary_model()
        timeout = _int_env("EXTERNAL_DOCUMENT_SUMMARY_TIMEOUT", 120)
        system_prompt = (
            "你是知识星球外链文档整理助手。你只能基于用户提供的飞书文档正文总结，"
            "不得新增正文没有出现的事实。输出中文 Markdown，不要使用代码块。"
        )
        user_prompt = "\n".join([
            f"标题：{document.title or '未命名飞书文档'}",
            f"来源 URL：{document.canonical_url}",
            "",
            "请输出：",
            "1. 5 条以内核心结论",
            "2. 关键方法/步骤",
            "3. 值得追踪的数字、时间、实体",
            "4. 适合详情页展示的一段 120 字以内摘要",
            "",
            "【飞书正文】",
            _clip(document.content_text or "", MAX_SUMMARY_INPUT_CHARS),
        ])
        response = requests.post(
            f"{base_url.rstrip('/')}/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
            },
            timeout=timeout,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise ExternalDocumentError(f"DeepSeek 总结失败: HTTP {response.status_code} {response.text[:600]}") from exc
        data = response.json()
        content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if not content:
            raise ExternalDocumentError("DeepSeek 返回了空总结")
        return content


def _iter_candidate_texts(text: str) -> Iterable[str]:
    raw = str(text or "")
    if not raw:
        return []
    decoded = unescape(raw)
    variants = [raw, decoded]
    try:
        variants.append(unquote(decoded))
    except Exception:
        pass
    return variants


def _json_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _trim_url(url: str) -> str:
    return str(url or "").strip().rstrip(".,;，。；、)]}）】>\"'\\")


def link_text_for_url(text: str, url: str) -> str:
    xml = re.search(r"<e\s+[^>]*type=[\"']web[\"'][^>]*>", text or "", re.I)
    if xml:
        title = re.search(r"title=[\"']([^\"']+)[\"']", xml.group(0), re.I)
        if title:
            return _clip(_normalize_text(unquote(title.group(1))), 500)
    idx = str(text or "").find(url)
    if idx < 0:
        return ""
    start = max(0, idx - 80)
    end = min(len(text), idx + len(url) + 80)
    return _clip(_normalize_text(text[start:end]), 500)


def _normalize_text(value: str) -> str:
    text = unescape(str(value or ""))
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _clip(value: str, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit].rstrip() + "\n..."


def _extract_html_title(html: str) -> str:
    for pattern in [
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']title["\'][^>]+content=["\']([^"\']+)["\']',
        r"<title[^>]*>(.*?)</title>",
    ]:
        match = re.search(pattern, html or "", re.I | re.S)
        if match:
            title = _normalize_text(re.sub(r"<[^>]+>", "", match.group(1)))
            title = re.sub(r"\s*[-|]\s*飞书.*$", "", title).strip()
            if title:
                return title[:500]
    return ""


class _ReadableHTMLParser(HTMLParser):
    SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas"}
    BLOCK_TAGS = {"p", "div", "section", "article", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "blockquote"}

    def __init__(self):
        super().__init__()
        self.parts: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        value = data.strip()
        if value:
            self.parts.append(value)

    def text(self) -> str:
        return _normalize_text(" ".join(self.parts))


def _extract_readable_text(html: str) -> str:
    parser = _ReadableHTMLParser()
    try:
        parser.feed(html or "")
        body_text = parser.text()
    except Exception:
        body_text = _normalize_text(re.sub(r"<[^>]+>", " ", html or ""))

    script_text = _extract_script_strings(html or "")
    combined = "\n".join(item for item in [body_text, script_text] if item)
    combined = _remove_feishu_boilerplate(_normalize_text(combined))
    return _clip(combined, MAX_FETCH_CHARS)


def _extract_script_strings(html: str) -> str:
    candidates: List[str] = []
    seen = set()
    for key in ("text", "title", "content", "name", "plain_text", "text_run"):
        pattern = re.compile(rf'"{key}"\s*:\s*"((?:\\.|[^"\\]){{2,}})"')
        for match in pattern.finditer(html):
            raw = match.group(1)
            try:
                value = json.loads(f'"{raw}"')
            except Exception:
                value = raw.encode("utf-8", "ignore").decode("unicode_escape", "ignore")
            value = _normalize_text(value)
            if len(value) < 4 or not re.search(r"[\u4e00-\u9fffA-Za-z]", value):
                continue
            if value in seen:
                continue
            seen.add(value)
            candidates.append(value)
            if sum(len(item) for item in candidates) > 80_000:
                break
    return "\n".join(candidates)


def _remove_feishu_boilerplate(text: str) -> str:
    boilerplate = {
        "飞书",
        "加载中",
        "请稍候",
        "打开飞书",
        "下载飞书",
        "登录",
        "分享",
        "复制链接",
    }
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped in boilerplate:
            continue
        if stripped.startswith("window.") or stripped.startswith("__"):
            continue
        lines.append(stripped)
    return "\n".join(lines)


def _document_summary_stale(document: ExternalDocument) -> bool:
    if not document.content_hash or not document.summary_text:
        return False
    payload = document.raw_payload if isinstance(document.raw_payload, dict) else {}
    summarized_hash = payload.get("summarized_content_hash")
    return bool(summarized_hash and summarized_hash != document.content_hash)


def _summary_model() -> str:
    return os.environ.get("EXTERNAL_DOCUMENT_SUMMARY_MODEL") or os.environ.get("DEEPSEEK_FLASH_MODEL") or "deepseek-v4-flash"


def _tts_text_for_document(document: ExternalDocument) -> str:
    title = document.title or "飞书文档"
    summary = _normalize_text(document.summary_text or "")
    content = _normalize_text(document.content_text or "")
    if summary:
        return _normalize_text(f"{title}\n\n{summary}")
    return _normalize_text(f"{title}\n\n{_clip(content, MAX_TTS_CHARS)}")


def _public_tts_status(status: str) -> str:
    return {
        "pending": "queued",
        "processing": "running",
        "completed": "completed",
        "failed": "failed",
        "blocked_by_captcha": "blocked",
        "canceled": "canceled",
    }.get(status or "", status or "not_submitted")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


external_document_service = ExternalDocumentService()
