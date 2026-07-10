#!/usr/bin/env python3
import argparse
import json
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUG_DIR = PROJECT_ROOT / "docs" / "bugs"
REGISTRY_PATH = BUG_DIR / "recurrence-registry.json"


def bug_record_names(bug_dir=BUG_DIR):
    return sorted(path.name for path in bug_dir.glob("20??-??-??-*.md"))


def load_registry(path=REGISTRY_PATH):
    return json.loads(path.read_text(encoding="utf-8"))


def validate_registry(registry, bug_dir=BUG_DIR):
    issues = []
    records = set(bug_record_names(bug_dir))
    assigned = set()
    cluster_keys = set()
    cluster_counts = {}

    for cluster in registry.get("clusters", []):
        key = str(cluster.get("key") or "").strip()
        if not key:
            issues.append("存在缺少 key 的复发根因簇")
            continue
        if key in cluster_keys:
            issues.append(f"复发根因簇 key 重复: {key}")
        cluster_keys.add(key)
        for field in ("title", "priority", "status", "systemic_guardrail"):
            if not str(cluster.get(field) or "").strip():
                issues.append(f"{key} 缺少字段: {field}")

        cluster_records = set(cluster.get("records") or [])
        if len(cluster_records) < 2:
            issues.append(f"{key} 不是复发簇，关联记录少于 2 条")
        unknown = sorted(cluster_records - records)
        for record in unknown:
            issues.append(f"{key} 引用了不存在的记录: {record}")
        assigned.update(cluster_records)
        cluster_counts[key] = len(cluster_records)

    for item in registry.get("standalone_records", []):
        record = str(item.get("record") or "").strip()
        if not record:
            issues.append("存在缺少 record 的独立事件")
            continue
        if record not in records:
            issues.append(f"独立事件引用了不存在的记录: {record}")
        if not str(item.get("watch_for") or "").strip():
            issues.append(f"独立事件缺少复发观察项: {record}")
        assigned.add(record)

    unclassified = sorted(records - assigned)
    for record in unclassified:
        issues.append(f"Bug 记录尚未归类: {record}")

    reviewed_through = date.fromisoformat(registry["reviewed_through"])
    max_days = int(registry.get("max_days_between_reviews", 7))
    latest_record_date = max(date.fromisoformat(name[:10]) for name in records)
    review_lag_days = (latest_record_date - reviewed_through).days
    if review_lag_days > max_days:
        issues.append(
            f"Bug 回顾已过期: 最新记录 {latest_record_date.isoformat()}，"
            f"最近回顾 {reviewed_through.isoformat()}，间隔 {review_lag_days} 天"
        )

    summary = {
        "record_count": len(records),
        "classified_count": len(records & assigned),
        "cluster_count": len(cluster_counts),
        "cluster_counts": cluster_counts,
        "standalone_count": len(registry.get("standalone_records", [])),
        "reviewed_through": reviewed_through.isoformat(),
        "latest_record_date": latest_record_date.isoformat(),
        "review_lag_days": review_lag_days,
        "unclassified": unclassified,
    }
    return summary, issues


def render_markdown(summary, issues):
    lines = [
        "# Bug 复发回顾检查",
        "",
        f"- Bug 记录：{summary['record_count']} 条",
        f"- 已归类：{summary['classified_count']} 条",
        f"- 复发根因簇：{summary['cluster_count']} 个",
        f"- 独立观察事件：{summary['standalone_count']} 条",
        f"- 最近正式回顾：{summary['reviewed_through']}",
        f"- 最新 Bug 日期：{summary['latest_record_date']}",
        "",
        "## 根因簇",
        "",
    ]
    for key, count in summary["cluster_counts"].items():
        lines.append(f"- `{key}`：{count} 条关联记录")
    lines.extend(["", "## 检查结果", ""])
    if issues:
        lines.extend(f"- [FAIL] {issue}" for issue in issues)
    else:
        lines.append("- [OK] 所有 Bug 均已归类，回顾周期未过期。")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="检查 Bug 复发分类和回顾新鲜度")
    parser.add_argument("--strict", action="store_true", help="发现问题时返回非零退出码")
    args = parser.parse_args()

    summary, issues = validate_registry(load_registry())
    print(render_markdown(summary, issues))
    return 1 if args.strict and issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
