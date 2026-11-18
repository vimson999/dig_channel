"""Persist reviewed title classifications as a reproducible analysis snapshot."""
import hashlib
import json
import sys
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/backend"))
from app import create_app
from models import db
from models.bilibili_upower_archive import BilibiliUpowerArchiveStat as Archive, BilibiliUpowerTopicReport as Report, BilibiliUpowerTopic as Topic

NOTES = {
    "油运/航运": "收益贡献最强，但前两条稿件贡献了本话题大部分收益。优先跟踪运价、运力、期租和企业盈利兑现，避免把爆款收益当成常态。",
    "光通信/光模块": "样本较多，是核心内容方向。优先围绕技术升级、订单验证、光互联和无源器件展开具体产业分析。",
    "PCB/电子布": "单条收益中位数相对较高，普通稿件表现较扎实。关注材料升级、供给瓶颈与订单兑现。",
    "液冷/电源/散热": "单条收益中位数较好，但样本较少。值得继续验证产业链细分环节的持续需求。",
    "生猪/农业": "去产能、现金流压力和猪价拐点已有收益信号，样本仍少，建议持续更新后观察。",
    "宏观/市场策略": "供稿占比较高，收益贡献相对偏低。建议把宽泛市场判断落到明确行业和可验证的变化上。",
    "AI应用/算力综合": "泛 AI 叙事收益表现相对一般，建议进一步聚焦商业化、成本与订单等具体问题。",
    "半导体/材料/设备": "部分细分材料和设备稿件表现突出，但整体中位数不高，应区分细分赛道与宽泛行业叙事。",
    "机器人/智驾/航天": "收益较集中于少数稿件，适合保留探索配额，尚不足以认定为稳定付费偏好。",
    "新能源/锂电": "少数稿件贡献较多，整体中位数较低，建议作为探索方向继续验证。",
    "医药/消费/建材/资源": "包含多个行业，暂不宜视为统一用户偏好，优先观察具体细分主题。",
    "其他": "样本较少，暂不作稳定偏好判断。",
}


def persist():
    source = json.loads((ROOT / "data/bilibili_upower_archive/topic_analysis.json").read_text())
    aids = [int(x["aid"]) for group in source["groups"].values() for x in group]
    if len(aids) != len(set(aids)):
        raise ValueError("话题归类存在重复稿件")
    rows = Archive.query.filter(Archive.aid.in_(aids), Archive.period_days == 90).all()
    if len(rows) != len(aids) or len({r.account_uid for r in rows}) != 1:
        raise ValueError("归类与数据库稿件不匹配")
    by_aid = {r.aid: r for r in rows}
    uid = rows[0].account_uid
    if Archive.query.filter_by(account_uid=uid, period_days=90).count() != len(rows):
        raise ValueError("归类未覆盖账号全部稿件")
    total = sum(r.income or 0 for r in rows)
    summary = {
        "archive_count": len(rows), "income_cents": total,
        "play_count": sum(r.play_count or 0 for r in rows),
        "method": "按标题主要话题初步归类，每条稿件归入一个话题；跨行业稿件存在判断误差。",
        "conclusion": "优先跟踪油运，以及光通信、PCB、液冷等 AI 产业链细分方向；生猪去产能值得持续验证。泛市场判断和 AI 大趋势讨论的收益稳定性相对较弱。",
        "caveat": "分析基于 period=90 的收益快照，包含更早发布的稿件；无付费人数及用户级记录，不能视为付费转化率。新旧稿件积累时间不同，结论为相关性观察。",
        "recommendations": [
            {"topic": "光通信、PCB、液冷", "count": 8}, {"topic": "油运产业跟踪", "count": 5},
            {"topic": "生猪去产能", "count": 3}, {"topic": "有行业落点的市场策略", "count": 2},
            {"topic": "其他探索选题", "count": 2}],
    }
    fingerprint = hashlib.sha256(json.dumps({"source": source, "summary": summary, "notes": NOTES,
        "snapshot": [(r.aid, r.income, r.play_count, r.fetched_at.isoformat()) for r in sorted(rows, key=lambda r:r.aid)]}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if db.session.get(Report, fingerprint):
        print("Analysis already persisted:", fingerprint)
        return
    report = Report(id=fingerprint, account_uid=uid, period_days=90, source_fetched_at=max(r.fetched_at for r in rows), summary=summary)
    db.session.add(report)
    db.session.flush()
    for name, entries in source["groups"].items():
        members = [by_aid[int(x["aid"])] for x in entries]
        incomes = [r.income or 0 for r in members]
        amount = sum(incomes)
        metrics = {"count": len(members), "income_cents": amount, "play_count": sum(r.play_count or 0 for r in members),
            "mean_income_cents": amount / len(members), "median_income_cents": median(incomes),
            "income_share": amount / total if total else 0,
            "top_two_share": sum(sorted(incomes, reverse=True)[:2]) / amount if amount else 0}
        db.session.add(Topic(report_id=fingerprint, name=name, metrics=metrics, archive_aids=[r.aid for r in members], conclusion=NOTES[name]))
    db.session.commit()
    print(f"Persisted {len(source['groups'])} topics, {len(rows)} archives, {total} cents, report {fingerprint}")


if __name__ == "__main__":
    with create_app(register_apis=False).app_context():
        persist()
