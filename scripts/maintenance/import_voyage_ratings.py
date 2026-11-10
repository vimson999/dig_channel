"""Persist curated voyage ratings, safely rerunnable."""
import json
from pathlib import Path
from import_voyage_analyses import db, Config, Flask
from sqlalchemy import Column, Integer, String, Text, MetaData, Table, select

root = Path(__file__).resolve().parents[2]
ratings = json.loads((root / 'src/backend/apis/v1/voyage_ratings.json').read_text())
app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)
with app.app_context():
    table = Table('voyage_analysis_ratings', MetaData(), Column('analysis_id', String(200), primary_key=True), Column('score', Integer, nullable=False), Column('grade', String(8), nullable=False), Column('reason', Text, nullable=False))
    table.create(db.engine, checkfirst=True)
    with db.engine.begin() as connection:
        for key, values in ratings.items():
            exists = connection.execute(select(table.c.analysis_id).where(table.c.analysis_id == key)).first()
            if exists:
                connection.execute(table.update().where(table.c.analysis_id == key).values(**values))
            else:
                connection.execute(table.insert().values(analysis_id=key, **values))
    print(f'Persisted {len(ratings)} ratings')
