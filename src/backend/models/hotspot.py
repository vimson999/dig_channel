from . import db


class Hotspot(db.Model):
    __tablename__ = 'hotspots'
    id = db.Column(db.String(64), primary_key=True)
    source_platform = db.Column(db.String(32), nullable=False)
    category = db.Column(db.String(32), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    rank = db.Column(db.Integer)
    heat_value = db.Column(db.String(64))
    raw_url = db.Column(db.String(512))
    summary = db.Column(db.Text)
    scraped_at = db.Column(db.DateTime, nullable=False, index=True)
    evidence_image = db.deferred(db.Column(db.LargeBinary))
    evidence_image_type = db.Column(db.String(32))
    cover_image_url = db.Column(db.String(512))
    status = db.Column(db.String(32), nullable=False)


class XPost(db.Model):
    __tablename__ = 'x_posts'
    id = db.Column(db.String(64), primary_key=True)
    hotspot_id = db.Column(db.String(64), db.ForeignKey('hotspots.id'), nullable=False, index=True)
    persona = db.Column(db.String(64), nullable=False)
    post_type = db.Column(db.String(32), nullable=False)
    hook = db.Column(db.String(255))
    content = db.Column(db.Text, nullable=False)
    call_to_action = db.Column(db.String(255))
    suggested_tags = db.Column(db.JSON, nullable=False)
    publish_status = db.Column(db.String(32), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False)
