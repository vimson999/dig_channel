import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
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
from services.runtime_guard import apply_async_after_db_release, release_db_session_quietly


logger = logging.getLogger(__name__)

FEISHU_HOST_HINTS = ("feishu.cn", "larksuite.com")
ZSXQ_HOST_HINTS = ("zsxq.com",)
WECHAT_HOSTS = {"mp.weixin.qq.com"}
GENERIC_WEB_IGNORED_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
    ".css", ".js", ".woff", ".woff2", ".ttf",
)
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
    """Extract user-visible linked content without scanning platform metadata URLs."""
    chunks = []
    if isinstance(topic, ZsxqTopic):
        chunks.extend([
            ("title", topic.title or ""),
            ("content", topic.content or ""),
        ])
        chunks.extend(_explicit_topic_link_chunks(topic.raw_payload))
    elif isinstance(topic, dict):
        chunks.extend(_explicit_topic_link_chunks(topic))
        if not chunks:
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
    parts = urlsplit(str(url or "").strip())
    host = (parts.hostname or "").lower()
    path = (parts.path or "").lower()
    if not host or host in {"localhost", "127.0.0.1", "::1"}:
        return None
    if any(hint in host for hint in FEISHU_HOST_HINTS):
        return "feishu"
    if host in WECHAT_HOSTS:
        return "wechat"
    if any(host == hint or host.endswith("." + hint) for hint in ZSXQ_HOST_HINTS):
        return "zsxq" if topic_id_for_zsxq_url(url) else None
    if path.endswith(GENERIC_WEB_IGNORED_SUFFIXES):
        return None
    return "generic_web"


def document_type_for_url(url: str) -> Optional[str]:
    provider = provider_for_url(url)
    if provider == "zsxq":
        return "topic"
    if provider == "wechat":
        return "article"
    if provider == "generic_web":
        return "web"
    path = urlsplit(str(url or "")).path.strip("/")
    if not path:
        return None
    head = path.split("/", 1)[0].lower()
    if head in {"docx", "docs", "doc", "wiki", "sheets", "base", "mindnotes", "file"}:
        return head
    return head[:40]


def topic_id_for_zsxq_url(url: str) -> str:
    parts = urlsplit(str(url or "").strip())
    path = unquote(parts.path or "")
    patterns = (
        r"/topic_detail/([0-9]{6,})",
        r"/topics?/([0-9]{6,})",
    )
    for pattern in patterns:
        match = re.search(pattern, path, re.I)
        if match:
            return match.group(1)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    value = str(query.get("topic_id") or query.get("topicId") or "").strip()
    return value if re.fullmatch(r"[0-9]{6,}", value) else ""


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


def _document_remote_snapshot(document: ExternalDocument) -> SimpleNamespace:
    """Materialize all fields needed after the DB session is released."""

    return SimpleNamespace(
        id=str(document.id),
        provider=str(document.provider or ""),
        canonical_url=str(document.canonical_url or ""),
        title=document.title,
        content_text=document.content_text or "",
        content_hash=document.content_hash,
    )


def _validate_feishu_document_response(*, title: str, content_text: str) -> None:
    """Reject Feishu application shells that return HTTP 200 without a document."""
    normalized_content = _normalize_text(content_text)
    if _looks_like_feishu_application_shell(title=title, content_text=normalized_content):
        raise ExternalDocumentError(
            "飞书返回了 Docs 首页/模板中心，而不是目标文档正文。"
            "当前 Cookie 可能已失效、无权访问该文档，或不适用于此飞书域名；请更新凭据后重试。"
        )

    if not normalized_content:
        raise ExternalDocumentError("飞书页面返回成功，但没有抽取到目标文档正文。")


def _looks_like_feishu_application_shell(*, title: str, content_text: str) -> bool:
    normalized_title = _normalize_text(title).lower()
    normalized_content = _normalize_text(content_text)
    shell_markers = (
        "模板推荐",
        "Recommended templates",
        "Bitable_VibeView_Onboard_",
        "✨ 明星企业案例",
        "暂无最近动态",
    )
    marker_count = sum(marker in normalized_content for marker in shell_markers)
    return normalized_title in {"docs", "飞书", "lark"} or marker_count >= 4


class ExternalDocumentService:
    def scan_topic_record(self, topic: ZsxqTopic, *, commit: bool = False) -> List[ZsxqTopicExternalDocument]:
        links = extract_external_links_from_topic(topic)
        if not links:
            return []

        links_by_hash = {
            link["url_hash"]: link
            for link in links
        }
        links = list(links_by_hash.values())
        url_hashes = tuple(links_by_hash)
        documents = self._upsert_document_shells(links)
        relations = {
            relation.url_hash: relation
            for relation in (
                ZsxqTopicExternalDocument.query
                .filter(
                    ZsxqTopicExternalDocument.group_id == topic.group_id,
                    ZsxqTopicExternalDocument.tab_id == topic.tab_id,
                    ZsxqTopicExternalDocument.topic_id == topic.topic_id,
                    ZsxqTopicExternalDocument.url_hash.in_(url_hashes),
                )
                .all()
            )
        }
        created: List[ZsxqTopicExternalDocument] = []
        for link in links:
            document = documents[link["url_hash"]]
            relation = relations.get(link["url_hash"])
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
        commit_every: int = 500,
    ) -> Dict[str, Any]:
        # Scan projected content plus a small explicit subset of raw payload
        # fields. Do not filter on the whole raw JSON: platform metadata places
        # zsxq.com URLs in almost every record and creates thousands of false positives.
        id_query = ZsxqTopic.query.with_entities(ZsxqTopic.id).order_by(ZsxqTopic.id.asc())
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
                ZsxqTopic.question_text,
                ZsxqTopic.answer_text,
                ZsxqTopic.article_title,
                ZsxqTopic.article_excerpt,
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
            batch_result = self._scan_topic_batch(batch)
            scanned += len(batch)
            topics_with_links += batch_result["topics_with_links"]
            relations += batch_result["relations"]
            documents.update(batch_result["document_hashes"])
            db.session.commit()
            db.session.expunge_all()
        return {
            "scanned_topics": scanned,
            "topics_with_links": topics_with_links,
            "relations": relations,
            "documents": len(documents),
        }

    def _scan_topic_batch(self, topics: Iterable[ZsxqTopic]) -> Dict[str, Any]:
        """Upsert one scan batch with bounded queries instead of one query per URL."""
        topic_links = []
        for topic in topics:
            # Historical backfill reads compact projection columns. Loading the
            # full raw payload is several orders of magnitude heavier and mostly
            # contains attachments/platform metadata rather than user links.
            links = extract_external_links_from_topic({
                "title": topic.title,
                "content": topic.content,
                "question_text": topic.question_text,
                "answer_text": topic.answer_text,
                "article_title": topic.article_title,
                "article_excerpt": topic.article_excerpt,
            })
            if links:
                topic_links.append((topic, links))
        if not topic_links:
            return {"topics_with_links": 0, "relations": 0, "document_hashes": set()}

        hashes = {link["url_hash"] for _, links in topic_links for link in links}
        documents = {
            row.url_hash: row
            for row in ExternalDocument.query.options(load_only(
                ExternalDocument.id,
                ExternalDocument.url_hash,
            )).filter(ExternalDocument.url_hash.in_(hashes)).all()
        }
        for _, links in topic_links:
            for link in links:
                document = documents.get(link["url_hash"])
                if document:
                    continue
                initial_payload = {}
                if link["provider"] == "zsxq":
                    initial_payload["target_topic_id"] = topic_id_for_zsxq_url(link["canonical_url"])
                document = ExternalDocument(
                    provider=link["provider"],
                    document_type=link.get("document_type"),
                    source_url=link["source_url"],
                    canonical_url=link["canonical_url"],
                    url_hash=link["url_hash"],
                    fetch_status="registered" if link["provider"] == "generic_web" else "pending",
                    summary_status="pending",
                    tts_status="not_submitted",
                    raw_payload=initial_payload,
                )
                db.session.add(document)
                documents[link["url_hash"]] = document
        db.session.flush()

        topic_ids = {str(topic.topic_id) for topic, _ in topic_links}
        existing_relations = {
            (str(row.group_id), str(row.tab_id), str(row.topic_id), row.url_hash): row
            for row in ZsxqTopicExternalDocument.query.filter(
                ZsxqTopicExternalDocument.topic_id.in_(topic_ids),
                ZsxqTopicExternalDocument.url_hash.in_(hashes),
            ).options(load_only(
                ZsxqTopicExternalDocument.id,
                ZsxqTopicExternalDocument.group_id,
                ZsxqTopicExternalDocument.tab_id,
                ZsxqTopicExternalDocument.topic_id,
                ZsxqTopicExternalDocument.url_hash,
            )).all()
        }
        relation_count = 0
        for topic, links in topic_links:
            for link in links:
                key = (str(topic.group_id), str(topic.tab_id), str(topic.topic_id), link["url_hash"])
                relation = existing_relations.get(key)
                document = documents[link["url_hash"]]
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
                    existing_relations[key] = relation
                relation_count += 1
        return {
            "topics_with_links": len(topic_links),
            "relations": relation_count,
            "document_hashes": hashes,
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

    def document_references(self, document_id: str, *, limit: int = 100) -> List[Dict[str, Any]]:
        rows = (
            ZsxqTopicExternalDocument.query.filter(
                ZsxqTopicExternalDocument.external_document_id == str(document_id)
            )
            .order_by(ZsxqTopicExternalDocument.updated_at.desc())
            .limit(max(min(int(limit or 100), 500), 1))
            .all()
        )
        return [row.to_dict(include_document=False) for row in rows]

    def list_documents(
        self,
        *,
        provider: Optional[str] = None,
        status: Optional[str] = None,
        keyword: Optional[str] = None,
        page: int = 1,
        per_page: int = 30,
    ) -> Dict[str, Any]:
        page = max(int(page or 1), 1)
        per_page = max(min(int(per_page or 30), 100), 1)
        query = ExternalDocument.query
        if provider:
            query = query.filter(ExternalDocument.provider == str(provider).strip().lower())
        if status:
            query = query.filter(ExternalDocument.fetch_status == str(status).strip().lower())
        if keyword:
            like = f"%{str(keyword).strip()}%"
            query = query.filter(or_(
                ExternalDocument.title.ilike(like),
                ExternalDocument.canonical_url.ilike(like),
            ))
        total = query.count()
        rows = (
            query.order_by(ExternalDocument.updated_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )
        counts = dict(
            db.session.query(ExternalDocument.fetch_status, db.func.count(ExternalDocument.id))
            .group_by(ExternalDocument.fetch_status)
            .all()
        )
        provider_counts = dict(
            db.session.query(ExternalDocument.provider, db.func.count(ExternalDocument.id))
            .group_by(ExternalDocument.provider)
            .all()
        )
        return {
            "items": [row.compact_dict() for row in rows],
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page,
            "status_counts": counts,
            "provider_counts": provider_counts,
        }

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
        if document.provider == "generic_web":
            raise ExternalDocumentError("普通网页当前只登记原始地址，不自动抓取")
        if document.provider not in {"feishu", "zsxq", "wechat"}:
            raise ExternalDocumentError(f"暂不支持抓取 {document.provider}")

        remote_document = _document_remote_snapshot(document)
        credential = {}
        if remote_document.provider == "feishu":
            credential = _resolve_feishu_cookie(account_id=credential_id, required=True)
        document.fetch_status = "fetching"
        document.fetch_error = None
        db.session.commit()
        release_db_session_quietly()

        try:
            if remote_document.provider == "feishu":
                fetched = self._fetch_feishu_document(remote_document, credential["cookie"])
            elif remote_document.provider == "zsxq":
                fetched = self._fetch_zsxq_document(remote_document, credential_id=credential_id)
                credential = fetched.pop("credential", {})
            else:
                fetched = self._fetch_wechat_document(remote_document)
            document = ExternalDocument.query.get(remote_document.id)
            if not document:
                raise ExternalDocumentError("外链文档在抓取期间已被删除")
            now = datetime.utcnow()
            document.title = fetched.get("title") or document.title
            document.author_name = fetched.get("author_name") or document.author_name
            document.content_text = _normalize_text(fetched.get("content_text") or "")
            document.content_html = fetched.get("content_html") or None
            document.content_chars = len(document.content_text or "")
            document.content_hash = content_hash(document.content_text or "")
            document.fetch_status = "fetched" if document.content_text else "empty"
            document.fetch_error = None if document.content_text else "页面返回成功，但没有抽取到正文。"
            document.fetched_at = now
            document.fetch_credential_id = credential.get("credential_id")
            document.raw_payload = fetched.get("raw_payload") or {}
            if document.summary_status in {"completed", "failed"} and _document_summary_stale(document):
                document.summary_status = "pending"
                document.summary_error = None
            if credential.get("credential_id"):
                _mark_credential_success(credential.get("credential_id"))
            db.session.commit()
            return document
        except Exception as exc:
            db.session.rollback()
            document = ExternalDocument.query.get(remote_document.id)
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
            raise ExternalDocumentError("关联内容正文尚未抓取，无法总结")

        remote_document = _document_remote_snapshot(document)
        document.summary_status = "summarizing"
        document.summary_error = None
        db.session.commit()
        release_db_session_quietly()
        try:
            summary = self._deepseek_summary(remote_document)
            document = ExternalDocument.query.get(remote_document.id)
            if not document:
                raise ExternalDocumentError("外链文档在总结期间已被删除")
            document.summary_text = summary
            document.summary_model = _summary_model()
            document.summary_status = "completed"
            document.summary_error = None
            document.summarized_at = datetime.utcnow()
            raw_payload = dict(document.raw_payload or {})
            raw_payload["summarized_content_hash"] = remote_document.content_hash
            document.raw_payload = raw_payload
            flag_modified(document, "raw_payload")
            db.session.commit()
            return document
        except Exception as exc:
            db.session.rollback()
            document = ExternalDocument.query.get(remote_document.id)
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
        task_id = str(task.id)
        document_id = str(document.id)
        document.tts_task_id = task_id
        document.tts_status = "queued"
        document.tts_error = None
        document.tts_audio_path = None
        document.tts_submitted_at = datetime.utcnow()
        document.tts_completed_at = None
        db.session.commit()
        try:
            celery_task = apply_async_after_db_release(
                process_doubao_browser_tts_job,
                args=[task_id],
                queue=QUEUE_NAME,
                retry=False,
            )
        except Exception as exc:
            error_message = f"任务入队失败: {exc}"
            PublicationTask.query.filter(PublicationTask.id == task_id).update({
                PublicationTask.status: "failed",
                PublicationTask.current_step: "failed",
                PublicationTask.error_message: error_message,
                PublicationTask.completed_at: datetime.utcnow(),
            }, synchronize_session=False)
            ExternalDocument.query.filter(ExternalDocument.id == document_id).update({
                ExternalDocument.tts_status: "failed",
                ExternalDocument.tts_error: error_message,
            }, synchronize_session=False)
            db.session.commit()
            raise ExternalDocumentError(error_message) from exc

        PublicationTask.query.filter(PublicationTask.id == task_id).update({
            PublicationTask.celery_task_id: celery_task.id,
        }, synchronize_session=False)
        db.session.commit()
        return ExternalDocument.query.get(document_id)

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
        providers: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        result = {}
        if scan:
            result["scan"] = self.scan_existing_topics(group_id=group_id, limit=scan_limit)
        if not (fetch or summarize):
            return result

        if isinstance(providers, str):
            providers = [providers]
        selected_providers = {
            str(item).strip().lower()
            for item in (providers or ("feishu", "zsxq", "wechat"))
            if str(item).strip().lower() in {"feishu", "zsxq", "wechat"}
        }
        query = ExternalDocument.query.filter(ExternalDocument.provider.in_(selected_providers)).order_by(
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
                    document = self.fetch_document(
                        document.id,
                        credential_id=credential_id if document.provider == "feishu" else None,
                    )
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

    def _upsert_document_shells(
        self,
        links: Iterable[Dict[str, str]],
    ) -> Dict[str, ExternalDocument]:
        links_by_hash = {
            link["url_hash"]: link
            for link in links
        }
        url_hashes = tuple(links_by_hash)
        documents = {
            document.url_hash: document
            for document in (
                ExternalDocument.query
                .options(load_only(
                    ExternalDocument.id,
                    ExternalDocument.provider,
                    ExternalDocument.document_type,
                    ExternalDocument.source_url,
                    ExternalDocument.canonical_url,
                    ExternalDocument.url_hash,
                ))
                .filter(ExternalDocument.url_hash.in_(url_hashes))
                .all()
            )
        }
        new_documents = []
        for hash_value, link in links_by_hash.items():
            document = documents.get(hash_value)
            if not document:
                initial_payload = {}
                if link["provider"] == "zsxq":
                    initial_payload["target_topic_id"] = topic_id_for_zsxq_url(link["canonical_url"])
                document = ExternalDocument(
                    provider=link["provider"],
                    document_type=link.get("document_type"),
                    source_url=link["source_url"],
                    canonical_url=link["canonical_url"],
                    url_hash=link["url_hash"],
                    fetch_status="registered" if link["provider"] == "generic_web" else "pending",
                    summary_status="pending",
                    tts_status="not_submitted",
                    raw_payload=initial_payload,
                )
                db.session.add(document)
                documents[hash_value] = document
                new_documents.append(document)
                continue
            values = {
                "provider": link["provider"],
                "document_type": link.get("document_type") or document.document_type,
                "source_url": link["source_url"] or document.source_url,
                "canonical_url": link["canonical_url"] or document.canonical_url,
            }
            for field, value in values.items():
                if getattr(document, field) != value:
                    setattr(document, field, value)
        if new_documents:
            db.session.flush()
        return documents

    def _upsert_document_shell(self, link: Dict[str, str]) -> ExternalDocument:
        return self._upsert_document_shells((link,))[link["url_hash"]]

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

    def _fetch_zsxq_document(
        self,
        document: ExternalDocument,
        cookie: Optional[str] = None,
        credential_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_topic_id = topic_id_for_zsxq_url(document.canonical_url)
        if not target_topic_id:
            raise ExternalDocumentError("知识星球链接中没有可识别的 topic_id")

        cached = (
            ZsxqTopic.query.filter(ZsxqTopic.topic_id == target_topic_id)
            .order_by(ZsxqTopic.last_synced_at.desc())
            .first()
        )
        if cached and cached.content:
            return {
                "title": cached.title or cached.article_title or f"知识星球 item {target_topic_id}",
                "author_name": cached.author_name,
                "content_text": cached.content,
                "content_html": None,
                "raw_payload": {
                    "extractor": "zsxq_local_topic_v1",
                    "target_topic_id": target_topic_id,
                    "target_group_id": cached.group_id,
                    "target_tab_id": cached.tab_id,
                    "fetched_at": datetime.utcnow().isoformat() + "Z",
                },
            }

        from services.zsxq_service import zsxq_service

        credential = {}
        if not cookie:
            credential = credential_service.resolve_zsxq_cookie(account_id=credential_id, required=True)
            cookie = credential["cookie"]
        release_db_session_quietly()
        response = zsxq_service.get_topic(cookie, target_topic_id)
        topic_payload = _zsxq_topic_payload(response)
        if not topic_payload:
            raise ExternalDocumentError("知识星球 topic detail 返回成功，但没有找到 item 数据")
        snapshot = ZsxqTopic(raw_payload=topic_payload)
        content_text = _normalize_text(snapshot.content or "")
        if not content_text:
            raise ExternalDocumentError("知识星球 item 没有可展示正文")
        return {
            "title": snapshot.title or snapshot.article_title or f"知识星球 item {target_topic_id}",
            "author_name": snapshot.author_name,
            "content_text": content_text,
            "content_html": None,
            "raw_payload": {
                "extractor": "zsxq_topic_api_v1",
                "target_topic_id": target_topic_id,
                "fetched_at": datetime.utcnow().isoformat() + "Z",
            },
            "credential": credential,
        }

    def _fetch_wechat_document(self, document: ExternalDocument) -> Dict[str, Any]:
        response = requests.get(
            document.canonical_url,
            headers={
                "User-Agent": os.environ.get(
                    "WECHAT_FETCH_USER_AGENT",
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                ),
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            timeout=_int_env("WECHAT_FETCH_TIMEOUT", 30),
            allow_redirects=True,
        )
        if response.status_code >= 400:
            raise ExternalDocumentError(f"微信文章抓取失败 HTTP {response.status_code}")
        response.encoding = response.encoding or "utf-8"
        # WeChat pages can place the article after 500 KB+ of styles/scripts.
        # Parse the complete response and clip only the extracted readable text.
        title, author_name, content_text = _extract_wechat_article(response.text)
        if not content_text:
            raise ExternalDocumentError("微信文章没有抽取到正文，可能已删除或需要验证")
        return {
            "title": title or document.title or "微信文章",
            "author_name": author_name,
            "content_text": content_text,
            "content_html": None,
            "raw_payload": {
                "status_code": response.status_code,
                "final_url": response.url,
                "content_type": response.headers.get("content-type"),
                "fetched_at": datetime.utcnow().isoformat() + "Z",
                "extractor": "wechat_html_v1",
            },
        }

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
        if _looks_like_feishu_application_shell(title=title or "", content_text=content_text):
            rendered = _fetch_feishu_document_with_browser(document.canonical_url, cookie)
            _validate_feishu_document_response(
                title=rendered.get("title") or "",
                content_text=rendered.get("content_text") or "",
            )
            rendered["raw_payload"] = {
                **(rendered.get("raw_payload") or {}),
                "initial_status_code": response.status_code,
                "initial_title": title,
            }
            return rendered
        _validate_feishu_document_response(title=title or "", content_text=content_text)
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
            "你是知识星球关联内容整理助手。你只能基于用户提供的正文总结，"
            "不得新增正文没有出现的事实。输出中文 Markdown，不要使用代码块。"
        )
        user_prompt = "\n".join([
            f"标题：{document.title or '未命名关联内容'}",
            f"来源 URL：{document.canonical_url}",
            "",
            "请输出：",
            "1. 5 条以内核心结论",
            "2. 关键方法/步骤",
            "3. 值得追踪的数字、时间、实体",
            "4. 适合详情页展示的一段 120 字以内摘要",
            "",
            "【正文】",
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


def _explicit_topic_link_chunks(payload: Any) -> List[Tuple[str, str]]:
    if not isinstance(payload, dict):
        return []
    allowed_text_keys = {
        "text", "content", "question_text", "answer_text", "article_content",
        "article_title", "article_excerpt", "description", "title",
        "article_url", "inline_article_url", "url", "link", "href",
    }
    allowed_container_keys = {"talk", "question", "answer", "article", "links", "link", "references"}
    chunks: List[Tuple[str, str]] = []

    def visit(value: Any, path: str, depth: int) -> None:
        if depth > 5:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key)
                next_path = f"{path}.{key_text}" if path else key_text
                if key_text in allowed_text_keys and isinstance(item, (str, int, float)):
                    chunks.append((next_path, str(item)))
                elif key_text in allowed_container_keys and isinstance(item, (dict, list)):
                    visit(item, next_path, depth + 1)
        elif isinstance(value, list):
            for index, item in enumerate(value[:100]):
                if isinstance(item, (dict, list)):
                    visit(item, f"{path}[{index}]", depth + 1)

    visit(payload, "payload", 0)
    return chunks


def _fetch_feishu_document_with_browser(url: str, cookie: str) -> Dict[str, Any]:
    """Render a Feishu SPA with the selected credential and collect virtualized document blocks."""
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ExternalDocumentError("飞书正文需要浏览器渲染，但当前环境缺少 Playwright 依赖。") from exc

    cookie_pairs = _cookie_pairs_from_header(cookie)
    if not cookie_pairs:
        raise _credential_error("飞书凭据里没有可注入浏览器的 Cookie")

    timeout_ms = _int_env("FEISHU_BROWSER_FETCH_TIMEOUT_MS", 45_000)
    started_at = time.monotonic()
    with sync_playwright() as playwright:
        browser = _launch_feishu_fetch_browser(playwright)
        context = None
        try:
            context = browser.new_context(
                locale="zh-CN",
                user_agent=os.environ.get(
                    "FEISHU_FETCH_USER_AGENT",
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                ),
            )
            cookie_domain = _feishu_cookie_domain(url)
            context.add_cookies([
                {
                    "name": key,
                    "value": value,
                    "domain": cookie_domain,
                    "path": "/",
                    "secure": True,
                }
                for key, value in cookie_pairs.items()
                if key and value is not None
            ])
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_function(
                    """() => Boolean(
                        document.querySelector('h1')
                        && document.querySelector('.block[data-record-id] .ace-line')
                    )""",
                    timeout=timeout_ms,
                )
            except PlaywrightTimeoutError as exc:
                sample = _normalize_text(page.locator("body").inner_text(timeout=5_000))[:500]
                if any(marker in sample.lower() for marker in ("登录", "sign in", "log in", "扫码登录")):
                    raise _credential_error("飞书浏览器未登录或登录态已失效") from exc
                raise ExternalDocumentError(
                    "飞书返回了动态页面，但浏览器等待正文渲染超时；请检查页面权限或浏览器运行环境。"
                ) from exc

            title, content_text, block_count = _collect_feishu_rendered_document(page, started_at)
            return {
                "title": title,
                "content_text": content_text,
                "content_html": None,
                "raw_payload": {
                    "final_url": page.url,
                    "fetched_at": datetime.utcnow().isoformat() + "Z",
                    "extractor": "browser_dom_v1",
                    "rendered_blocks": block_count,
                },
            }
        finally:
            if context is not None:
                context.close()
            browser.close()


def _collect_feishu_rendered_document(page: Any, started_at: float) -> Tuple[str, str, int]:
    timeout_seconds = max(_int_env("FEISHU_BROWSER_TOTAL_TIMEOUT_SECONDS", 90), 10)
    pause_ms = max(_int_env("FEISHU_BROWSER_SCROLL_PAUSE_MS", 180), 20)
    max_steps = max(_int_env("FEISHU_BROWSER_MAX_SCROLL_STEPS", 240), 1)
    seen_ids = set()
    blocks: List[str] = []
    position = 0
    stable_bottom_rounds = 0

    title = _clean_feishu_dom_text(page.locator("h1").inner_text(timeout=5_000))[:500]
    for _ in range(max_steps):
        if time.monotonic() - started_at > timeout_seconds:
            raise ExternalDocumentError("飞书浏览器正文抽取超过总时限")
        metrics = page.evaluate(
            """() => {
                const container = document.querySelector('.bear-web-x-container');
                if (!container) return null;
                return {
                    scrollHeight: container.scrollHeight,
                    clientHeight: container.clientHeight,
                    scrollTop: container.scrollTop,
                };
            }"""
        )
        if not metrics:
            raise ExternalDocumentError("飞书文档已渲染，但未找到正文滚动容器")

        page.evaluate(
            """(top) => {
                const container = document.querySelector('.bear-web-x-container');
                if (container) container.scrollTop = top;
            }""",
            position,
        )
        page.wait_for_timeout(pause_ms)
        visible_blocks = page.evaluate(
            """() => Array.from(document.querySelectorAll('.block[data-record-id]')).map(block => ({
                id: block.getAttribute('data-record-id') || '',
                text: block.innerText || '',
            }))"""
        )
        for block in visible_blocks or []:
            record_id = str(block.get("id") or "")
            text = _clean_feishu_dom_text(block.get("text") or "")
            if not record_id or record_id in seen_ids:
                continue
            seen_ids.add(record_id)
            if text:
                blocks.append(text)

        scroll_height = int(metrics.get("scrollHeight") or 0)
        client_height = max(int(metrics.get("clientHeight") or 0), 1)
        bottom = max(scroll_height - client_height, 0)
        if position >= bottom:
            stable_bottom_rounds += 1
            if stable_bottom_rounds >= 2:
                break
        else:
            stable_bottom_rounds = 0
        position = min(position + max(int(client_height * 0.75), 320), bottom)

    content_text = _normalize_text("\n\n".join(blocks))
    if title and not content_text.startswith(title):
        content_text = _normalize_text(f"{title}\n\n{content_text}")
    return title, _clip(content_text, MAX_FETCH_CHARS), len(seen_ids)


def _launch_feishu_fetch_browser(playwright: Any) -> Any:
    chrome_path = (os.environ.get("FEISHU_FETCH_CHROME_PATH") or _default_chrome_path()).strip()
    kwargs: Dict[str, Any] = {"headless": True}
    if chrome_path:
        kwargs.update({
            "executable_path": chrome_path,
            "headless": False,
            "args": ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--mute-audio"],
        })
    channel = (os.environ.get("FEISHU_FETCH_CHROME_CHANNEL") or "").strip()
    if channel and not chrome_path:
        kwargs["channel"] = channel
    return playwright.chromium.launch(**kwargs)


def _default_chrome_path() -> str:
    for candidate in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ):
        if Path(candidate).exists():
            return candidate
    return ""


def _feishu_cookie_domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host == "feishu.cn" or host.endswith(".feishu.cn"):
        return ".feishu.cn"
    if host == "larksuite.com" or host.endswith(".larksuite.com"):
        return ".larksuite.com"
    return host


def _cookie_pairs_from_header(cookie: str) -> Dict[str, str]:
    pairs: Dict[str, str] = {}
    for part in str(cookie or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if key and not re.search(r"\s", key):
            pairs[key] = value.strip()
    return pairs


def _clean_feishu_dom_text(value: str) -> str:
    text = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]", "", str(value or ""))
    return _normalize_text(text)


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


class _WechatArticleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: List[str] = []
        self.capture_depth = 0
        self.skip_depth = 0
        self.capture_stack: List[str] = []

    def handle_starttag(self, tag, attrs):
        attrs_map = dict(attrs)
        if attrs_map.get("id") == "js_content":
            self.capture_stack = [tag]
            self.capture_depth = len(self.capture_stack)
            return
        if not self.capture_depth:
            return
        if tag in {"br", "img", "input", "meta", "link", "hr"}:
            if tag in {"br", "hr"}:
                self.parts.append("\n")
            return
        self.capture_stack.append(tag)
        self.capture_depth = len(self.capture_stack)
        if tag in {"script", "style", "svg", "canvas", "noscript"}:
            self.skip_depth += 1
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "blockquote"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if not self.capture_depth or tag not in self.capture_stack:
            return
        while self.capture_stack:
            opened = self.capture_stack.pop()
            if opened in {"script", "style", "svg", "canvas", "noscript"} and self.skip_depth:
                self.skip_depth -= 1
            if opened == tag:
                break
        self.capture_depth = len(self.capture_stack)
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "blockquote"}:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in {"br", "img", "input", "meta", "link", "hr"}:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self.capture_depth and not self.skip_depth:
            value = str(data or "").strip()
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


def _extract_wechat_article(html: str) -> Tuple[str, str, str]:
    title = _extract_html_title(html)
    author = ""
    for pattern in (
        r'<meta[^>]+name=["\']author["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+property=["\']og:article:author["\'][^>]+content=["\']([^"\']+)',
        r'<span[^>]+class=["\'][^"\']*rich_media_meta_nickname[^"\']*["\'][^>]*>(.*?)</span>',
    ):
        match = re.search(pattern, html or "", re.I | re.S)
        if match:
            author = _normalize_text(re.sub(r"<[^>]+>", " ", match.group(1)))[:255]
            if author:
                break
    parser = _WechatArticleParser()
    try:
        parser.feed(html or "")
        content_text = parser.text()
    except Exception:
        content_text = ""
    return title, author, _clip(content_text, MAX_FETCH_CHARS)


def _zsxq_topic_payload(response: Any) -> Dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    candidates = [response]
    for key in ("resp_data", "data"):
        value = response.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    for candidate in candidates:
        topic = candidate.get("topic") if isinstance(candidate, dict) else None
        if isinstance(topic, dict):
            return topic
        if any(key in candidate for key in ("talk", "question", "answer", "article")):
            return candidate
    return {}


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
