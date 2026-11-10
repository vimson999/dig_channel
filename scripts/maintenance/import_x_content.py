#!/usr/bin/env python3
"""Import X operations data, optionally creating only the four new tables."""
import argparse
import json
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src/backend'))
os.environ.setdefault('DB_PROCESS_ROLE','worker')
from flask import Flask
from config import Config
from models import db
from models.x_content import MODELS
from services.x_content_service import import_bundle, seed_mock

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path,nargs='?')
    parser.add_argument('--ensure-tables',action='store_true')
    parser.add_argument('--replace-mock',action='store_true')
    parser.add_argument('--mock',action='store_true')
    args=parser.parse_args()
    if bool(args.source)==args.mock: parser.error('Provide a source file OR --mock')
    source=None
    if args.source:
        with args.source.open('rb') as f: raw=f.read(5*1024*1024+1)
        if len(raw)>5*1024*1024: parser.error('File exceeds 5 MiB')
        source=json.loads(raw.decode('utf-8-sig'))
    app=Flask(__name__);app.config.from_object(Config);db.init_app(app)
    with app.app_context():
        if args.ensure_tables:
            for model in MODELS.values(): model.__table__.create(bind=db.engine,checkfirst=True)
        result=seed_mock() if args.mock else import_bundle(source,replace_mock=args.replace_mock)
        result['counts']={k:m.query.count() for k,m in MODELS.items()}
        print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
