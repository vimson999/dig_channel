"""Read-only analytics for the local ZSXQ topic library."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from sqlalchemy import text

from models import db


GROUP_ID_SHENGCAI = "1824528822"
MIN_RANGE_DAYS = 30
MAX_RANGE_DAYS = 730
DEFAULT_PAGE_SIZE = 30
MAX_PAGE_SIZE = 5000

AI_PATTERN = r"人工智能|大模型|智能体|提示词|chatgpt|claude|deepseek|cursor|coze|扣子|mcp|工作流|自动化|aigc|(?<![a-z])ai(?![a-z])"
CONTENT_PATTERN = r"小红书|抖音|视频号|公众号|短视频|自媒体|内容创作|个人IP|起号|直播|播客|流量"
SALES_PATTERN = r"私域|销售|获客|成交|客服|社群|企业微信|客单|转化率"
ECOMMERCE_PATTERN = r"电商|淘宝|天猫|拼多多|京东|闲鱼|抖店|店铺|带货|选品|商品卡|供应链"

DEMAND_RE = re.compile(
    r"求助|请教|寻找|有没有|需要|需求|招募|合作|对接|怎么做|如何|怎么办|求推荐|求资源|缺少|难点|痛点",
    re.I,
)
PROOF_RE = re.compile(
    r"收入|营收|利润|净利|变现|成交|订单|单量|gmv|月入|赚了|赚钱|复盘|实操|跑通|中标|案例|增长|从0到1",
    re.I,
)


def _both(first: str, second: str) -> str:
    return rf"(?=.*(?:{first}))(?=.*(?:{second}))"


THEMES = [
    {
        "id": "ai",
        "label": "AI与自动化",
        "icon": "fa-microchip",
        "pattern": AI_PATTERN,
        "summary": "AI 已从单点工具讨论转向内容、销售与交付流程，跨业务环节的组合应用最活跃。",
        "questions": ["如何把 AI 接进真实业务流程？", "智能体怎样稳定交付？", "怎样从工具使用走向业务结果？"],
        "tactics": ["垂直场景工作流", "人工审核兜底", "先服务后产品化"],
        "platforms": ["Claude", "Cursor", "扣子", "Dify"],
    },
    {
        "id": "content",
        "label": "内容/IP/自媒体",
        "icon": "fa-pen-to-square",
        "pattern": CONTENT_PATTERN,
        "summary": "内容仍是最大的获客入口，讨论重点正在从单纯起号转向稳定生产、矩阵分发与线索承接。",
        "questions": ["怎样持续产出而不降质？", "内容如何承接到私域？", "什么选题更容易转化？"],
        "tactics": ["平台差异化分发", "选题库复用", "内容到私域闭环"],
        "platforms": ["小红书", "抖音", "视频号", "公众号"],
    },
    {
        "id": "tools",
        "label": "工具/效率产品",
        "icon": "fa-screwdriver-wrench",
        "pattern": r"工具|插件|软件|saas|网站|app|应用|产品经理|开发者",
        "summary": "工具类机会集中在明确、重复、可量化的工作环节，通用工具的分发压力明显高于垂直产品。",
        "questions": ["怎样找到付费场景？", "工具如何获得第一批用户？", "通用产品怎样做垂直化？"],
        "tactics": ["窄需求切入", "服务验证需求", "按结果设计定价"],
        "platforms": ["Web", "小程序", "SaaS", "浏览器插件"],
    },
    {
        "id": "sales",
        "label": "私域/销售",
        "icon": "fa-user-group",
        "pattern": SALES_PATTERN,
        "summary": "获客成本上升后，线索沉淀、自动跟进和成交效率成为更明确的付费问题。",
        "questions": ["线索如何自动沉淀？", "怎样持续跟进而不骚扰？", "内容和成交如何打通？"],
        "tactics": ["企微承接", "线索分层", "自动跟进加人工成交"],
        "platforms": ["企业微信", "微信", "飞书", "视频号"],
    },
    {
        "id": "ecommerce",
        "label": "电商/带货",
        "icon": "fa-bag-shopping",
        "pattern": ECOMMERCE_PATTERN,
        "summary": "电商讨论从流量技巧延伸到选品、素材、投放和店铺运营，执行效率比单一平台红利更重要。",
        "questions": ["如何低成本测品？", "素材怎样规模化生产？", "自然流和付费流如何配合？"],
        "tactics": ["小批量测品", "素材矩阵", "数据化复盘"],
        "platforms": ["淘宝", "抖店", "闲鱼", "视频号"],
    },
    {
        "id": "resources",
        "label": "资源对接",
        "icon": "fa-share-nodes",
        "pattern": r"资源对接|寻找|招募|合作|货源|货盘|渠道|供应商|合伙人|组队",
        "summary": "资源撮合需求密集，但验证、匹配和履约仍是主要摩擦，可信结构化信息比单纯拉群更稀缺。",
        "questions": ["怎样验证合作方？", "需求如何准确匹配？", "合作过程如何保障履约？"],
        "tactics": ["结构化需求单", "案例验证", "履约评价"],
        "platforms": ["微信", "飞书", "知识星球", "线下活动"],
    },
    {
        "id": "crossborder",
        "label": "出海/跨境",
        "icon": "fa-earth-asia",
        "pattern": r"出海|跨境|亚马逊|ozon|tiktok|shopee|temu|独立站|海外市场|外贸",
        "summary": "跨境方向的结果案例正在增加，机会更多落在本地化、内容获客和运营基础设施，而非单纯开店。",
        "questions": ["如何完成本地化获客？", "平台规则变化怎样应对？", "供应链和履约如何稳定？"],
        "tactics": ["本地化内容", "多平台分散", "运营服务产品化"],
        "platforms": ["TikTok", "Ozon", "Amazon", "独立站"],
    },
]

AI_CHILDREN = [
    {"id": "ai-content", "label": "AI+内容生产", "pattern": CONTENT_PATTERN},
    {"id": "agent-workflow", "label": "智能体/工作流", "pattern": r"智能体|agent|工作流|n8n|dify|扣子|coze"},
    {"id": "ai-coding", "label": "AI编程/应用", "pattern": r"ai编程|vibe coding|cursor|claude code|代码|编程|开发应用|独立开发"},
    {"id": "ai-sales", "label": "AI+销售私域", "pattern": SALES_PATTERN},
    {"id": "ai-ecommerce", "label": "AI+电商", "pattern": ECOMMERCE_PATTERN},
]

SUBTOPICS = {
    "ai-sales": [
        ("获客", r"获客|引流|线索|流量"),
        ("私域跟进", r"私域|跟进|企业微信|企微|朋友圈"),
        ("客服", r"客服|答疑|回复|接待"),
        ("成交", r"成交|销售|转化|客单"),
        ("社群运营", r"社群|群运营|社区运营"),
    ],
    "ai": [
        ("内容生产", CONTENT_PATTERN),
        ("智能体", r"智能体|agent|dify|扣子|coze"),
        ("编程开发", r"编程|代码|cursor|claude code"),
        ("销售增长", SALES_PATTERN),
        ("电商运营", ECOMMERCE_PATTERN),
    ],
}

CHILD_INFO = {
    "ai-content": {
        "summary": "AI 内容生产已经从单篇生成转向选题、生产、分发和复盘的连续工作流。",
        "questions": ["怎样稳定批量生产内容？", "多平台内容如何自动改写？", "AI 内容如何保持真实感？"],
        "tactics": ["选题库驱动", "人机协同审核", "多平台分发"],
        "platforms": ["小红书", "公众号", "抖音", "视频号"],
    },
    "agent-workflow": {
        "summary": "智能体讨论正在进入真实交付阶段，稳定性、数据连接和人工兜底比演示效果更关键。",
        "questions": ["智能体怎样稳定运行？", "如何连接企业数据？", "复杂流程怎样设置人工兜底？"],
        "tactics": ["单流程先跑通", "结构化输入输出", "异常人工接管"],
        "platforms": ["Dify", "n8n", "扣子", "飞书"],
    },
    "ai-coding": {
        "summary": "AI 编程显著降低了原型成本，但真实机会仍来自明确需求、分发渠道和持续维护。",
        "questions": ["怎样选择足够窄的需求？", "AI 代码如何保证质量？", "产品怎样获得第一批用户？"],
        "tactics": ["先验证再开发", "小工具快速上线", "真实用户反馈迭代"],
        "platforms": ["Cursor", "Claude Code", "GitHub", "Vercel"],
    },
    "ai-sales": {
        "summary": "需求与结果信号同步上升，获客后私域跟进相关内容密集，智能体在自动化线索处理、客服跟进和社群运营场景落地明显。",
        "questions": ["如何自动把线索沉淀到企微并打标签？", "AI 自动跟进话术怎么设计效果更好？", "智能体如何识别高意向客户？"],
        "tactics": ["智能体自动获客 + 入群", "线索分层 + 自动跟进", "AI 客服 + 会话转人工"],
        "platforms": ["企业微信", "飞书", "钉钉", "微信"],
    },
    "ai-ecommerce": {
        "summary": "AI 正在进入电商选品、素材、客服和投放复盘环节，机会集中在可重复的运营工作。",
        "questions": ["怎样用 AI 提高测品效率？", "商品素材如何规模化生产？", "客服和评价如何自动处理？"],
        "tactics": ["数据辅助选品", "素材批量生成", "客服自动化"],
        "platforms": ["抖店", "淘宝", "视频号", "闲鱼"],
    },
}

THEME_LOOKUP = {item["id"]: item for item in THEMES}
THEME_LOOKUP.update({**{item["id"]: item for item in AI_CHILDREN}})
THEME_REGEX = {key: re.compile(value["pattern"], re.I | re.S) for key, value in THEME_LOOKUP.items()}


def _normalize_range_days(value) -> int:
    try:
        days = int(value or 90)
    except (TypeError, ValueError) as error:
        raise ValueError("range_days 必须是整数") from error
    return max(MIN_RANGE_DAYS, min(days, MAX_RANGE_DAYS))


def _normalize_page_size(value) -> int:
    raw_value = DEFAULT_PAGE_SIZE if value is None or value == "" else value
    try:
        size = int(raw_value)
    except (TypeError, ValueError) as error:
        raise ValueError("page_size 必须是整数") from error
    if size < 1:
        raise ValueError("page_size 必须大于 0")
    return min(size, MAX_PAGE_SIZE)


def _normalize_sort(value) -> str:
    sort = str(value or "latest").strip().lower()
    if sort not in {"latest", "signal"}:
        raise ValueError("sort 仅支持 latest 或 signal")
    return sort


def matches_theme(body: str, theme_id: str) -> bool:
    """Return whether normalized topic text belongs to a theme."""
    if theme_id in {"ai-content", "ai-sales", "ai-ecommerce"}:
        child_pattern = THEME_REGEX.get(theme_id)
        return bool(THEME_REGEX["ai"].search(body or "") and child_pattern and child_pattern.search(body or ""))
    pattern = THEME_REGEX.get(theme_id)
    return bool(pattern and pattern.search(body or ""))


def _classify_topic(body: str):
    theme_ids = {item["id"] for item in THEMES if THEME_REGEX[item["id"]].search(body)}
    for child in AI_CHILDREN:
        child_id = child["id"]
        if child_id in {"ai-content", "ai-sales", "ai-ecommerce"}:
            matched = "ai" in theme_ids and bool(THEME_REGEX[child_id].search(body))
        else:
            matched = bool(THEME_REGEX[child_id].search(body))
        if matched:
            theme_ids.add(child_id)
    return theme_ids


def _topic_body(row) -> str:
    return " ".join(
        str(row.get(key) or "")
        for key in ("title", "content", "question_text", "answer_text", "article_title", "article_excerpt")
    )


def _parse_time(value):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d")
        except ValueError:
            return None


def _load_topics(group_id: str):
    statement = text(
        """
        WITH ranked AS (
            SELECT *, row_number() OVER (
                PARTITION BY topic_id
                ORDER BY CASE tab_label
                    WHEN '精华' THEN 0 WHEN '中标' THEN 1 WHEN '生财好事' THEN 2 ELSE 3 END,
                    last_synced_at DESC
            ) AS rn
            FROM zsxq_topics
            WHERE group_id = :group_id
        )
        SELECT topic_id, title, author_name, content, question_text, answer_text,
               article_title, article_excerpt, tab_id, tab_label, topic_type,
               create_time, files_count, images_count, last_synced_at
        FROM ranked
        WHERE rn = 1
        """
    )
    rows = db.session.execute(statement, {"group_id": group_id}).mappings().all()
    topics = []
    for raw in rows:
        topic = dict(raw)
        topic["body"] = _topic_body(topic)
        topic["created_at"] = _parse_time(topic.get("create_time"))
        topic["theme_ids"] = _classify_topic(topic["body"])
        topic["has_demand"] = bool(DEMAND_RE.search(topic["body"]))
        topic["has_proof"] = bool(PROOF_RE.search(topic["body"]))
        topics.append(topic)
    return topics


def _category_parameters():
    rows = []
    for item in THEMES + AI_CHILDREN:
        required = AI_PATTERN if item["id"] in {"ai-content", "ai-sales", "ai-ecommerce"} else None
        rows.append({"id": item["id"], "pattern": item["pattern"], "required": required})
    return rows


def _aggregate_themes(group_id: str):
    categories = _category_parameters()
    category_sql = " UNION ALL ".join(
        f"SELECT :id_{index} AS id, :pattern_{index} AS pattern, :required_{index} AS required"
        for index in range(len(categories))
    )
    params = {"group_id": group_id}
    for index, item in enumerate(categories):
        params[f"id_{index}"] = item["id"]
        params[f"pattern_{index}"] = item["pattern"]
        params[f"required_{index}"] = item["required"]

    statement = text(
        f"""
        WITH ranked AS (
            SELECT *, row_number() OVER (
                PARTITION BY topic_id
                ORDER BY CASE tab_label
                    WHEN '精华' THEN 0 WHEN '中标' THEN 1 WHEN '生财好事' THEN 2 ELSE 3 END,
                    last_synced_at DESC
            ) AS rn
            FROM zsxq_topics
            WHERE group_id = :group_id
        ), base AS (
            SELECT topic_id, author_name, tab_label,
                   create_time,
                   CASE
                       WHEN substring(create_time from 1 for 10) ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}$'
                       THEN substring(create_time from 1 for 10)::date
                   END AS created_date,
                   concat_ws(' ', title, content, question_text, answer_text, article_title, article_excerpt) AS body
            FROM ranked WHERE rn = 1
        ), newest AS (
            SELECT max(created_date) AS newest_date, max(create_time) AS updated_at, count(*) AS total_topics FROM base
        ), categories AS ({category_sql})
        SELECT categories.id,
               count(base.topic_id) AS topics,
               count(distinct base.author_name) AS authors,
               count(base.topic_id) FILTER (WHERE base.created_date > newest.newest_date - interval '30 days') AS recent,
               count(base.topic_id) FILTER (
                   WHERE base.created_date <= newest.newest_date - interval '30 days'
                     AND base.created_date > newest.newest_date - interval '60 days'
               ) AS previous,
               count(base.topic_id) FILTER (WHERE base.body ~* :demand_pattern) AS demand,
               count(base.topic_id) FILTER (WHERE base.body ~* :proof_pattern) AS proof,
               newest.newest_date, newest.updated_at, newest.total_topics
        FROM categories
        CROSS JOIN newest
        LEFT JOIN base
          ON base.body ~* categories.pattern
         AND (categories.required IS NULL OR base.body ~* categories.required)
        GROUP BY categories.id, newest.newest_date, newest.updated_at, newest.total_topics
        """
    )
    params["demand_pattern"] = DEMAND_RE.pattern
    params["proof_pattern"] = PROOF_RE.pattern
    rows = db.session.execute(statement, params).mappings().all()
    result = {}
    for raw in rows:
        row = dict(raw)
        recent = int(row.get("recent") or 0)
        previous = int(row.get("previous") or 0)
        row["growth"] = round(((recent - previous) / previous) * 100, 1) if previous else (100.0 if recent else 0.0)
        for key in ("topics", "authors", "recent", "demand", "proof", "total_topics"):
            row[key] = int(row.get(key) or 0)
        result[row["id"]] = row
    return result


def _load_selected_topics(group_id: str, theme_id: str, range_days: int, newest: datetime, query: str = ""):
    config = THEME_LOOKUP[theme_id]
    required = AI_PATTERN if theme_id in {"ai-content", "ai-sales", "ai-ecommerce"} else None
    start_date = (newest - timedelta(days=range_days - 1)).date()
    statement = text(
        """
        WITH ranked AS (
            SELECT *, row_number() OVER (
                PARTITION BY topic_id
                ORDER BY CASE tab_label
                    WHEN '精华' THEN 0 WHEN '中标' THEN 1 WHEN '生财好事' THEN 2 ELSE 3 END,
                    last_synced_at DESC
            ) AS rn
            FROM zsxq_topics
            WHERE group_id = :group_id
        ), base AS (
            SELECT topic_id, title, author_name, content, question_text, answer_text,
                   article_title, article_excerpt, tab_id, tab_label, topic_type,
                   create_time, files_count, images_count, last_synced_at,
                   concat_ws(' ', title, content, question_text, answer_text, article_title, article_excerpt) AS body
            FROM ranked WHERE rn = 1
        )
        SELECT topic_id, title, author_name, left(content, 1600) AS content,
               left(question_text, 800) AS question_text, left(answer_text, 800) AS answer_text,
               article_title, left(article_excerpt, 800) AS article_excerpt,
               tab_id, tab_label, topic_type, create_time, files_count, images_count, last_synced_at
        FROM base
        WHERE CASE
                  WHEN substring(create_time from 1 for 10) ~ '^\\d{4}-\\d{2}-\\d{2}$'
                  THEN substring(create_time from 1 for 10)::date
              END >= :start_date
          AND body ~* :pattern
          AND (cast(:required AS text) IS NULL OR body ~* cast(:required AS text))
          AND (
              cast(:query AS text) = ''
              OR lower(body) LIKE cast(:query_like AS text)
              OR lower(coalesce(author_name, '')) LIKE cast(:query_like AS text)
          )
        ORDER BY create_time DESC
        """
    )
    rows = db.session.execute(
        statement,
        {
            "group_id": group_id,
            "start_date": start_date,
            "pattern": config["pattern"],
            "required": required,
            "query": query,
            "query_like": f"%{query.lower()}%",
        },
    ).mappings().all()
    topics = []
    for raw in rows:
        topic = dict(raw)
        topic["body"] = _topic_body(topic)
        topic["created_at"] = _parse_time(topic.get("create_time"))
        topic["has_demand"] = bool(DEMAND_RE.search(topic["body"]))
        topic["has_proof"] = bool(PROOF_RE.search(topic["body"]))
        topics.append(topic)
    return topics


def get_topic_detail(group_id: str, topic_id: str):
    statement = text(
        """
        SELECT topic_id, title, author_name, content, question_text, answer_text,
               article_title, article_excerpt, tab_label, create_time, files_count, images_count
        FROM zsxq_topics
        WHERE group_id = :group_id AND topic_id = :topic_id
        ORDER BY CASE tab_label
            WHEN '精华' THEN 0 WHEN '中标' THEN 1 WHEN '生财好事' THEN 2 ELSE 3 END,
            last_synced_at DESC
        LIMIT 1
        """
    )
    row = db.session.execute(statement, {"group_id": str(group_id), "topic_id": str(topic_id)}).mappings().first()
    if not row:
        return None
    item = dict(row)
    content = item.get("content") or item.get("answer_text") or item.get("question_text") or item.get("article_excerpt") or ""
    return {
        "topic_id": item.get("topic_id"),
        "title": item.get("title") or item.get("article_title") or "未命名主题",
        "author": item.get("author_name") or "匿名圈友",
        "content": str(content)[:50000],
        "date": str(item.get("create_time") or "")[:10],
        "tab": item.get("tab_label") or "未分类",
        "files_count": int(item.get("files_count") or 0),
        "images_count": int(item.get("images_count") or 0),
    }


def _period_growth(topics, newest: datetime):
    current_start = newest - timedelta(days=29)
    previous_start = newest - timedelta(days=59)
    current = sum(bool(item["created_at"] and item["created_at"] >= current_start) for item in topics)
    previous = sum(bool(item["created_at"] and previous_start <= item["created_at"] < current_start) for item in topics)
    growth = round(((current - previous) / previous) * 100, 1) if previous else (100.0 if current else 0.0)
    return current, growth


def _theme_metrics(topics, newest: datetime):
    recent, growth = _period_growth(topics, newest)
    return {
        "topics": len(topics),
        "recent": recent,
        "demand": sum(item["has_demand"] for item in topics),
        "proof": sum(item["has_proof"] for item in topics),
        "authors": len({item["author_name"] for item in topics if item.get("author_name")}),
        "growth": growth,
    }


def _weekly_trend(topics, newest: datetime, range_days: int):
    start = (newest - timedelta(days=range_days - 1)).date()
    start -= timedelta(days=start.weekday())
    end = newest.date()
    buckets = []
    cursor = start
    while cursor <= end:
        buckets.append(cursor)
        cursor += timedelta(days=7)
    counts = defaultdict(lambda: {"topics": 0, "proof": 0})
    for item in topics:
        created = item.get("created_at")
        if not created or created.date() < start:
            continue
        week = created.date() - timedelta(days=created.date().weekday())
        counts[week]["topics"] += 1
        counts[week]["proof"] += int(item["has_proof"])
    return {
        "keys": [item.isoformat() for item in buckets],
        "labels": [item.strftime("%m-%d") for item in buckets],
        "topics": [counts[item]["topics"] for item in buckets],
        "proof": [counts[item]["proof"] for item in buckets],
    }


def _subtopic_breakdown(theme_id: str, topics):
    definitions = SUBTOPICS.get(theme_id) or SUBTOPICS.get("ai") or []
    raw_counts = []
    for label, pattern in definitions:
        matcher = re.compile(pattern, re.I | re.S)
        raw_counts.append((label, sum(bool(matcher.search(item["body"])) for item in topics)))
    total = sum(count for _, count in raw_counts) or 1
    result = []
    allocated = 0
    for index, (label, count) in enumerate(raw_counts):
        percent = round(count / total * 100)
        if index == len(raw_counts) - 1:
            percent = max(0, 100 - allocated)
        allocated += percent
        result.append({"label": label, "count": count, "percent": percent})
    return result


def _topic_item(item):
    has_proof = item["has_proof"]
    has_demand = item["has_demand"]
    signal = "结果案例" if has_proof else ("明确需求" if has_demand else "普通讨论")
    content = str(item.get("content") or item.get("article_excerpt") or "").strip()
    return {
        "topic_id": item.get("topic_id"),
        "title": item.get("title") or item.get("article_title") or "未命名主题",
        "excerpt": re.sub(r"\s+", " ", content)[:180],
        "author": item.get("author_name") or "匿名圈友",
        "date": str(item.get("create_time") or "")[:10],
        "tab": item.get("tab_label") or "未分类",
        "signal": signal,
        "files_count": int(item.get("files_count") or 0),
        "images_count": int(item.get("images_count") or 0),
    }


def _active_authors(topics):
    author_stats = defaultdict(lambda: {"topics": 0, "proof": 0})
    for item in topics:
        name = item.get("author_name")
        if not name:
            continue
        author_stats[name]["topics"] += 1
        author_stats[name]["proof"] += int(item["has_proof"])
    ranked = sorted(author_stats.items(), key=lambda pair: (pair[1]["proof"], pair[1]["topics"]), reverse=True)
    return [{"name": name, **stats} for name, stats in ranked[:5]]


def _sort_topics(topics, sort: str):
    if sort == "signal":
        return sorted(
            topics,
            key=lambda item: (
                int(item["has_proof"]),
                int(item["has_demand"]),
                item.get("created_at") or datetime.min,
            ),
            reverse=True,
        )
    return sorted(topics, key=lambda item: item.get("created_at") or datetime.min, reverse=True)


def build_insights(
    group_id=GROUP_ID_SHENGCAI,
    theme_id="ai-sales",
    range_days=90,
    query="",
    page_size=DEFAULT_PAGE_SIZE,
    sort="latest",
):
    group_id = str(group_id)
    range_days = _normalize_range_days(range_days)
    page_size = _normalize_page_size(page_size)
    sort = _normalize_sort(sort)
    query = str(query or "").strip().lower()
    aggregates = _aggregate_themes(group_id)
    if not aggregates:
        return {"group_id": str(group_id), "empty": True, "themes": [], "topics": []}
    sample = next(iter(aggregates.values()))
    newest_value = sample.get("newest_date")
    newest = datetime.combine(newest_value, datetime.min.time()) if newest_value else datetime.utcnow()
    theme_rows = []
    for config in THEMES:
        metric = aggregates.get(config["id"], {})
        row = {"id": config["id"], "label": config["label"], "icon": config["icon"], **metric}
        if config["id"] == "ai":
            children = []
            for child in AI_CHILDREN:
                children.append({"id": child["id"], "label": child["label"], **aggregates.get(child["id"], {})})
            row["children"] = children
        theme_rows.append(row)

    if theme_id not in THEME_LOOKUP:
        theme_id = "ai-sales"
    selected_topics = _load_selected_topics(group_id, theme_id, range_days, newest, query=query)
    visible_topics = _sort_topics(selected_topics, sort)

    selected_config = THEME_LOOKUP[theme_id]
    fallback = THEME_LOOKUP["ai"]
    selected_info = next((item for item in THEMES if item["id"] == theme_id), None) or CHILD_INFO.get(theme_id, fallback)
    metrics = {
        key: aggregates.get(theme_id, {}).get(key, 0)
        for key in ("topics", "recent", "demand", "proof", "authors", "growth")
    }
    return {
        "group_id": str(group_id),
        "group_name": "生财有术" if str(group_id) == GROUP_ID_SHENGCAI else str(group_id),
        "updated_at": str(sample.get("updated_at") or newest.isoformat())[:16].replace("T", " "),
        "range_days": range_days,
        "total_topics": int(sample.get("total_topics") or 0),
        "themes": theme_rows,
        "selected": {
            "id": theme_id,
            "label": selected_config["label"],
            "summary": selected_info.get("summary", fallback["summary"]),
            "questions": selected_info.get("questions", fallback["questions"]),
            "tactics": selected_info.get("tactics", fallback["tactics"]),
            "platforms": selected_info.get("platforms", fallback["platforms"]),
            "metrics": metrics,
            "trend": _weekly_trend(selected_topics, newest, range_days),
            "subtopics": _subtopic_breakdown(theme_id, selected_topics),
            "authors": _active_authors(selected_topics),
        },
        "query": query,
        "sort": sort,
        "topics": [_topic_item(item) for item in visible_topics[:page_size]],
        "topic_matches": len(visible_topics),
        "pagination": {
            "page": 1,
            "page_size": page_size,
            "returned": min(len(visible_topics), page_size),
            "total": len(visible_topics),
            "has_more": len(visible_topics) > page_size,
        },
    }


zsxq_insights_service = build_insights
