#!/usr/bin/env python3
"""Idempotently import the Markdown voyage analysis library into its own table."""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/backend'))
os.environ.setdefault('DB_PROCESS_ROLE', 'worker')

from flask import Flask
from config import Config
from models import db
from models.voyage_analysis import VoyageAnalysis


ANALYSIS_DIR = ROOT / 'docs' / '航海项目分析'
INDEX_ROW = re.compile(r'^\|\s*([^|]+?)\s*\|\s*\[查看\]\(([^)]+\.md)\)\s*\|\s*([^|]+?)\s*\|')
ACTIVITY_IDS = {
    '抖音爆款AI视频': '10472',
    'FDE入门企业AI服务': '10473',
    '企业AI内训': '10474',
    'AI加IP获客系统': '10475',
    '微信小程序产品': '10476',
    '小红书虚拟产品AI赋能': '10477',
    '小红书实体电商垂直小店': '10478',
    '视频号AI图书带货': '10479',
    '小红书AI自媒体视频': '10480',
    '微信贴图号': '10481',
    '垂直小号内容自动化': '10482',
    '生财MCP项目智库': '10483',
    'AI短篇小说': '10484',
    '流量词英文站': '10485',
    '个人AI助理': '10486',
}


def read_source_records():
    index_path = ANALYSIS_DIR / 'README.md'
    if not index_path.is_file():
        raise ValueError(f'Missing analysis index: {index_path}')

    root = ANALYSIS_DIR.resolve()
    paths_by_name = {
        path.name: path
        for path in ANALYSIS_DIR.glob('*.md')
        if path.name != 'README.md' and path.is_file() and path.resolve().parent == root
    }
    records = []
    for line in index_path.read_text(encoding='utf-8').splitlines():
        match = INDEX_ROW.match(line)
        if not match:
            continue
        filename = match.group(2)
        if Path(filename).name != filename or filename not in paths_by_name:
            continue

        path = paths_by_name[filename]
        markdown = path.read_text(encoding='utf-8')
        title_match = re.search(r'^#\s+(.+?)\s*$', markdown, re.MULTILINE)
        date_match = re.search(r'^分析日期：(\d{4}-\d{2}-\d{2})\s*$', markdown, re.MULTILINE)
        status_match = re.search(r'^状态：(.+?)\s*$', markdown, re.MULTILINE)
        activity_id = ACTIVITY_IDS.get(path.stem)
        if not title_match or not date_match or not activity_id:
            raise ValueError(f'Missing required metadata or activity ID in {path.name}')
        records.append({
            'id': path.stem,
            'activity_id': activity_id,
            'title': title_match.group(1),
            'analysis_date': date.fromisoformat(date_match.group(1)),
            'status': status_match.group(1).strip() if status_match else '初步分析',
            'summary': match.group(3).strip(),
            'content_markdown': markdown,
            'sort_order': len(records),
            'content_sha256': hashlib.sha256(markdown.encode('utf-8')).hexdigest(),
        })

    if len(records) != len(ACTIVITY_IDS):
        imported_slugs = {record['id'] for record in records}
        missing = sorted(set(ACTIVITY_IDS) - imported_slugs)
        raise ValueError(f'Expected {len(ACTIVITY_IDS)} indexed analyses; found {len(records)}. Missing: {missing}')
    if len({record['activity_id'] for record in records}) != len(records):
        raise ValueError('Duplicate source activity IDs in analysis index')
    return records


def import_records(records, ensure_table=False):
    if ensure_table:
        VoyageAnalysis.__table__.create(bind=db.engine, checkfirst=True)

    counts = {'inserted': 0, 'updated': 0, 'unchanged': 0}
    now = datetime.utcnow()
    try:
        existing_rows = {row.id: row for row in VoyageAnalysis.query.all()}
        for record in records:
            row = existing_rows.get(record['id'])
            if row is None:
                db.session.add(VoyageAnalysis(**record, created_at=now, updated_at=now))
                counts['inserted'] += 1
                continue

            same = all(getattr(row, key) == value for key, value in record.items())
            if same:
                counts['unchanged'] += 1
                continue
            for key, value in record.items():
                setattr(row, key, value)
            row.updated_at = now
            counts['updated'] += 1

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return {**counts, 'total': len(records)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ensure-table', action='store_true', help='Create only voyage_analyses if it is missing')
    args = parser.parse_args()
    records = read_source_records()

    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app)
    with app.app_context():
        result = import_records(records, ensure_table=args.ensure_table)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
