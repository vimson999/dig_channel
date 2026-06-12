import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from string import Template

from services.report_video_service import PROJECT_ROOT


PROMPT_CONFIG_PATH = PROJECT_ROOT / "data" / "report_video_ai_prompts.json"


DEFAULT_PROMPTS = {
    "report_ai_content": {
        "name": "单篇研报 AI 文案",
        "stage": "AI 文案",
        "description": "基于单篇研报正文生成摘要、思维导图和分段口播。",
        "system_prompt": "你只输出严格 JSON。",
        "user_prompt": """
你是财经研报短视频编导。请基于研报正文生成结构化 JSON，只输出 JSON，不要 Markdown。

要求：
1. 生成 report_title、institution、theme、core_view、key_points。
2. 生成 opening_text，作为视频开场白，30-60 字，要像财经短视频开头，点明今天看什么和为什么值得看。
3. 生成 $segment_count 段口播 segments。
4. 每段必须有 focus_node_id，且必须能在 mindmap.nodes 中找到同名 id。
5. 横屏后续会用 mindmap 节点漫游，竖屏后续会用节点卡片，所以节点标题要短。
6. voice_text 适合中文口播，每段 80-180 字，不要重复开场白。
7. 生成 closing_text，作为视频结尾，20-50 字，做简短收束，不要出现投资建议承诺。

JSON 格式：
{
  "report_title": "...",
  "institution": "...",
  "theme": "...",
  "opening_text": "...",
  "core_view": "...",
  "key_points": ["..."],
  "video_value_score": 0,
  "recommended_video_type": "daily_digest",
  "recommended_duration_sec": 120,
  "reason": "...",
  "closing_text": "...",
  "mindmap": {"center": "...", "nodes": [{"id": "root", "label": "...", "type": "center", "children": ["node_001"]}]},
  "segments": [{"segment_id": "seg_001", "segment_index": 1, "segment_type": "key_point", "segment_title": "...", "voice_text": "...", "estimated_duration_sec": 30, "focus_node_id": "node_001"}]
}

文件名：$file_name
正文：
$text
""".strip(),
    },
    "report_quick_card": {
        "name": "单篇研报摘要卡",
        "stage": "主题策划",
        "description": "为每份研报生成用于批量聚类的快速摘要卡。",
        "system_prompt": (
            "你是一名资深财经研报分析师和内容策划编辑，擅长从机构研报中提炼投资主线、"
            "行业主题、关键公司、资产类别和视频化价值。你必须只输出严格 JSON，不要输出 Markdown，"
            "不要解释，不要添加 JSON 以外的内容。"
        ),
        "user_prompt": """
请基于下面这份研报正文，生成一份“研报快速摘要卡片”。

你的任务不是写完整视频脚本，而是把这份研报结构化，方便后续进行 50 份研报的主题聚类和视频选题。

请重点提取：
1. 研报标题；
2. 机构名称；
3. 日期；
4. 一级主题；
5. 二级主题；
6. 主题标签；
7. 涉及公司；
8. 涉及行业；
9. 涉及资产类别；
10. 一句话摘要；
11. 核心观点；
12. 3-5 个关键点；
13. 为什么这份研报值得关注；
14. 是否适合做视频；
15. 视频价值评分。

评分标准：
- 8-10 分：强观点、强主题、适合单独或主题聚合视频；
- 5-7 分：有信息价值，适合放入串烧；
- 1-4 分：信息弱，不建议优先制作。

要求：
1. 不要编造研报中没有的信息；
2. 如果机构、日期无法识别，填 null；
3. 主题标签要短；
4. 一句话摘要要适合财经内容编辑快速浏览；
5. 输出严格 JSON。

JSON 格式：
{
  "report_id": "$report_id",
  "file_name": "$file_name",
  "report_title": "...",
  "institution": "...",
  "report_date": "...",
  "primary_theme": "...",
  "secondary_themes": ["...", "..."],
  "theme_tags": ["...", "..."],
  "companies": ["...", "..."],
  "industries": ["...", "..."],
  "assets": ["...", "..."],
  "one_sentence_summary": "...",
  "core_view": "...",
  "key_points": ["...", "...", "..."],
  "why_it_matters": "...",
  "suitable_for_video": true,
  "video_value_score": 0,
  "recommended_usage": "single_video | topic_collection | digest_only | skip"
}

文件名：$file_name

研报正文：
$text
""".strip(),
    },
    "topic_clustering": {
        "name": "批量主题聚类",
        "stage": "主题策划",
        "description": "把多篇摘要卡聚类成适合制作财经视频的主题榜。",
        "system_prompt": (
            "你是一名资深财经内容主编和机构研报选题策划负责人。你擅长从大量研报摘要中识别市场主线、"
            "行业主题、机构共识、观点分歧和视频选题价值。你必须只输出严格 JSON，不要输出 Markdown，"
            "不要解释，不要添加 JSON 以外的内容。"
        ),
        "user_prompt": """
下面是一批研报的结构化摘要卡片。请你把这些研报按“适合做财经视频的主题主线”进行聚类。

注意：
观众不是为了看一份份 PDF，而是为了看某个领域、某条市场主线。
因此你的聚类不能只按机械行业分类，而要按“有视频叙事价值的主题”组织。

请输出：
1. 主题列表；
2. 每个主题包含哪些研报；
3. 每个主题涉及哪些机构；
4. 每个主题的共同观点；
5. 每个主题的分歧观点；
6. 每个主题为什么值得看；
7. 每个主题推荐的视频标题；
8. 内部研报热度评分；
9. 视频优先级评分。

主题命名要求：
不要只输出“宏观”“电力”“消费”这种宽泛名称。
要输出更像视频选题的主题名，例如“AI 算力背后的电力需求重估”“机器人产业链的确定性环节”。

输出严格 JSON：
{
  "topics": [
    {
      "topic_id": "T001",
      "topic_name": "...",
      "topic_category": "...",
      "reports": ["R001", "R008"],
      "report_count": 0,
      "institutions": ["...", "..."],
      "topic_summary": "...",
      "common_views": ["...", "..."],
      "conflicting_views": ["...", "..."],
      "representative_reports": ["R001", "R008"],
      "internal_heat_score": 0,
      "video_priority_score": 0,
      "recommended_video_angle": "...",
      "recommended_title": "...",
      "why_it_is_worth_making": "..."
    }
  ]
}

研报摘要卡片：
$cards_json
""".strip(),
    },
    "topic_video_script": {
        "name": "主题级视频脚本",
        "stage": "主题策划",
        "description": "基于主题和多篇研报摘要生成主题级视频脚本。",
        "system_prompt": (
            "你是一名资深财经视频总编、机构研报分析师和短视频编导。你擅长把多份机构研报聚合成一条"
            "清晰、有吸引力、有来源背书的主题级财经视频脚本。你必须只输出严格 JSON，不要输出 Markdown，"
            "不要解释，不要添加 JSON 以外的内容。"
        ),
        "user_prompt": """
请基于下面这个主题，以及该主题下的多份研报摘要，生成一个主题级研报解读视频脚本。

注意：这不是单篇研报解读。这是从一批研报里筛选出的一个主题主线视频。

视频开场必须体现：
1. 今天这批研报里出现了什么共同主线；
2. 有哪些机构在关注；
3. 为什么这个主题现在值得看；
4. 观众接下来会听到什么；
5. 第一时间抛出主线或冲突，要像专业财经视频主编写的黄金三秒开场，而不是普通摘要；
6. 明确表达“不是单篇观点，而是多家机构、多份研报共同指向的一条主题主线”。
7. opening_text 和第一个“主题总开场” section 会覆盖视频开头的多研报证据链画面，口播时长控制在 5-10 秒，建议 45-80 个中文字符，不要写成长摘要。

视频主体要求：
1. 分成 5-7 个 section；
2. 第一个 section 必须是“主题总开场”，section_type 为 topic_opening，focus_node_id 为 node_overview；
3. 主题总开场要讲清楚共同主线、集中关注的机构、为什么值得看、接下来拆哪几个关键点；
4. 主题总开场要短、锋利、有钩子，适合作为证据链开场口播；estimated_duration_sec 建议 6-10 秒；
5. 后续每个 section 有一个明确观点；
6. 每个 section 可以引用 1-3 份 supporting_reports；
7. 每个 section 要有 voice_text；
8. 每个 section 要有 visual_explanation；
9. 每个 section 要有 bullet_points；
10. 每个 section 要有 focus_node_id；
11. 每个 section 要有 source_report_ids；
12. 每个 section 如果能判断来源页，请给 source_page，否则为 null；
13. 生成主题级 mindmap，并生成 theme_flow，描述主题主线链路。

主题级结构要求：
1. 脚本要像“我从多份研报里帮你筛出一条主线”，不要像“逐篇讲报告”；
2. 正文段落可以围绕“为什么现在讲、哪些机构在关注、核心逻辑、受益环节、风险分歧”等展开；
3. 主题主线要能被渲染成左侧逻辑链，例如 “AI 算力 → 电力需求 → 电网投资 → 储能扩张 → 资产重估”。

内容要求：
1. 不要编造研报没有的信息；
2. 不要给确定性投资建议；
3. 不要承诺收益；
4. 语言要适合中文财经口播；
5. 每段 voice_text 80-180 字；
6. visual_explanation 60-100 字；
7. bullet_points 每条不超过 18 个中文字符；
8. 标题要有视频点击价值，但不要夸张失真。

JSON 格式：
{
  "topic_id": "$topic_id",
  "video_title": "...",
  "video_subtitle": "...",
  "opening_text": "...",
  "topic_core_view": "...",
  "source_card": {
    "institution_summary": "...",
    "report_count": 0,
    "main_institutions": ["...", "..."],
    "topic_summary": "...",
    "focus_points": ["...", "...", "..."]
  },
  "theme_mindmap": {
    "center": "...",
    "nodes": [{"id": "node_001", "label": "...", "type": "section", "children": ["node_001_a"]}]
  },
  "theme_flow": [
    {"id": "node_overview", "label": "主题总开场", "section_id": "S000", "section_type": "topic_opening"}
  ],
  "sections": [
    {
      "section_id": "S000",
      "section_index": 0,
      "section_title": "主题总开场",
      "section_type": "topic_opening | core_view | logic | evidence | impact | risk | closing",
      "voice_text": "...",
      "visual_explanation": "...",
      "bullet_points": ["...", "...", "..."],
      "focus_node_id": "node_overview",
      "source_report_ids": ["R001", "R008"],
      "source_page": null,
      "source_evidence": "...",
      "estimated_duration_sec": 30
    }
  ],
  "closing_text": "..."
}

主题信息：
$topic_json

该主题下研报摘要：
$topic_reports_json

外部热度信息：
$external_heat_json
""".strip(),
    },
    "asr_calibration": {
        "name": "ASR 抽样校准提示词",
        "stage": "音频匹配",
        "description": "上传整片音频后，用于抽样识别时提示 ASR 相关机构、公司和术语。",
        "system_prompt": "",
        "user_prompt": (
            "这是一段专业财经研报解读音频，请使用简体中文输出。"
            "本期内容涉及：$terms。"
            "请尽量准确识别公司名称、机构名称、行业术语、英文缩写和金融表达。"
        ),
    },
}


def _ensure_prompt_dir():
    PROMPT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load_overrides():
    if not PROMPT_CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(PROMPT_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_overrides(overrides):
    _ensure_prompt_dir()
    PROMPT_CONFIG_PATH.write_text(json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8")


def list_prompt_templates():
    overrides = _load_overrides()
    result = []
    for prompt_id, default in DEFAULT_PROMPTS.items():
        merged = deepcopy(default)
        override = overrides.get(prompt_id) if isinstance(overrides.get(prompt_id), dict) else {}
        merged.update({key: value for key, value in override.items() if key in {"system_prompt", "user_prompt"}})
        merged["id"] = prompt_id
        merged["is_custom"] = bool(override)
        merged["updated_at"] = override.get("updated_at") if isinstance(override, dict) else None
        result.append(merged)
    return result


def get_prompt_template(prompt_id):
    prompts = {item["id"]: item for item in list_prompt_templates()}
    if prompt_id not in prompts:
        raise KeyError(f"Unknown prompt id: {prompt_id}")
    return prompts[prompt_id]


def save_prompt_template(prompt_id, system_prompt=None, user_prompt=None):
    if prompt_id not in DEFAULT_PROMPTS:
        raise KeyError(f"Unknown prompt id: {prompt_id}")
    overrides = _load_overrides()
    current = overrides.get(prompt_id) if isinstance(overrides.get(prompt_id), dict) else {}
    if system_prompt is not None:
        current["system_prompt"] = str(system_prompt)
    if user_prompt is not None:
        current["user_prompt"] = str(user_prompt)
    current["updated_at"] = datetime.utcnow().isoformat()
    overrides[prompt_id] = current
    _write_overrides(overrides)
    return get_prompt_template(prompt_id)


def reset_prompt_template(prompt_id):
    if prompt_id not in DEFAULT_PROMPTS:
        raise KeyError(f"Unknown prompt id: {prompt_id}")
    overrides = _load_overrides()
    overrides.pop(prompt_id, None)
    _write_overrides(overrides)
    return get_prompt_template(prompt_id)


def render_prompt(prompt_id, context=None):
    template = get_prompt_template(prompt_id)
    context = context or {}
    system_prompt = Template(template.get("system_prompt") or "").safe_substitute(context)
    user_prompt = Template(template.get("user_prompt") or "").safe_substitute(context)
    return system_prompt, user_prompt
