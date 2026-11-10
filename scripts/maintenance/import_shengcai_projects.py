#!/usr/bin/env python3
"""Import a local Shengcai list; optional creation of only its two new tables."""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/backend'))
os.environ.setdefault('DB_PROCESS_ROLE', 'worker')
from flask import Flask
from config import Config
from models import db
from models.shengcai_project import ShengcaiProject, ShengcaiProjectImport
from services.shengcai_project_service import MAX_SOURCE_BYTES, import_catalog, validate_catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--ensure-tables', action='store_true', help='Create missing catalog tables only; do not stamp or migrate other tables')
    args = parser.parse_args()
    with args.source.open('rb') as stream: raw = stream.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES: parser.error('Source exceeds 5 MiB')
    source = json.loads(raw.decode('utf-8-sig')); validate_catalog(source)
    app = Flask(__name__); app.config.from_object(Config); db.init_app(app)
    with app.app_context():
        if args.ensure_tables:
            for model in (ShengcaiProject, ShengcaiProjectImport):
                model.__table__.create(bind=db.engine, checkfirst=True)
        print(json.dumps(import_catalog(source, args.source.name), ensure_ascii=False))


if __name__ == '__main__': main()
