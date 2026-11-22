import sys
from pathlib import Path

import pytest
from flask import Flask
from sqlalchemy.exc import IntegrityError
from sqlalchemy import event

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src' / 'backend'))
from models import db
from models.book_commerce import Account, Observation, CandidateDecision


@pytest.fixture
def session():
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://', SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    with app.app_context():
        @event.listens_for(db.engine, 'connect')
        def foreign_keys(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
        db.create_all()
        yield db.session
        db.session.remove()
        db.drop_all()


def test_platform_account_id_is_unique(session):
    session.add_all([Account(name='a', platform='wechat_channels', source='s', source_key='1', external_id='x'),
                     Account(name='b', platform='wechat_channels', source='s', source_key='2', external_id='x')])
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_observation_slot_cannot_be_overwritten(session):
    from datetime import datetime, date
    account = Account(id='a', name='a', platform='wechat_channels', source='s', source_key='1')
    session.add(account)
    session.flush()
    common = dict(kind='account', account_id='a', target_key='account:a', series_key='s',
                  observed_at=datetime(2026, 11, 17), business_date=date(2026, 11, 17), slot_key='slot')
    session.add_all([Observation(content_hash='a'*64, **common), Observation(content_hash='b'*64, **common)])
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_decision_is_scoped_to_user(session):
    constraints = [set(c.columns.keys()) for c in CandidateDecision.__table__.constraints if hasattr(c, 'columns')]
    assert {'owner_user_id', 'video_id', 'account_listing_id', 'business_date'} in constraints


def test_invalid_observation_kind_and_target_are_rejected(session):
    from datetime import datetime, date
    account = Account(id='a', name='a', platform='wechat_channels', source='s', source_key='1')
    session.add(account)
    session.flush()
    session.add(Observation(kind='video', account_id='a', target_key='video:missing', series_key='s',
                            observed_at=datetime(2026, 11, 17), business_date=date(2026, 11, 17),
                            slot_key='slot', content_hash='a'*64))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_observations_have_date_and_history_indexes():
    indexes = {item.name for item in Observation.__table__.indexes}
    assert {'ix_bc_obs_date', 'ix_bc_obs_account_history', 'ix_bc_obs_video_history', 'ix_bc_obs_listing_history'} <= indexes
