"""Store report points, metrics and table cells as relational rows."""
from alembic import op
import sqlalchemy as sa
import re

revision = "20261110_codex_pdf_blocks"
down_revision = "20261110_codex_pdf_analysis"
branch_labels = None
depends_on = None


def _number(value):
    return float(value) if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value) else value


def _legacy_blocks(body, ranking_names=None):
    """Convert the known archive format; keep unrecognized lines as individual notes."""
    ranking_names = ranking_names or {}
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    blocks = []
    i = 0

    def add(kind, title, text=None, number=None, unit=None, columns=(), rows=()):
        blocks.append((dict(kind=kind, title=title[:255], content_text=text,
                            number_value=number, unit=unit), [columns, *rows] if columns else []))

    def markdown_row(line):
        return [cell.strip() for cell in line.strip("|").split("|")]

    group = re.compile(r"^- (.+?)：\s*(\d+)条 / (\d+)次 / (\d+)家可识别机构。$")
    daily = re.compile(r"^- (\d{4}-\d\d-\d\d)：(\d+) 条，累计下载 (\d+)。$")
    pdf = re.compile(r"^(?:\d+\. |- )(?:\[([^]]+)\] )?(.+?\.pdf)｜机构：(.+?)；报告日候选：(.+?)；上传：(.+?)；下载：(\d+)；状态：([^｜]+)(?:｜入选作用：(.*))?$")
    while i < len(lines):
        line = lines[i]
        following = lines[i + 1] if i + 1 < len(lines) else ""
        if line.startswith("|") and following.startswith("|") and all(
            re.fullmatch(r":?-{3,}:?", cell) for cell in markdown_row(following)
        ):
            columns = markdown_row(line)
            rows = []
            i += 2
            while i < len(lines) and lines[i].startswith("|") and len(markdown_row(lines[i])) == len(columns):
                rows.append([_number(cell) for cell in markdown_row(lines[i])])
                i += 1
            title = blocks.pop()[0]["title"] if blocks and blocks[-1][0]["kind"] == "heading" else "数据明细"
            add("table", title, columns=columns, rows=rows)
            continue
        if line.startswith("商品直接/市场类标题筛选：") and (totals := re.match(
            r"商品直接/市场类标题筛选：(\d+)条，原始累计下载 (\d+)；(.*)", line
        )):
            add("metric", "商品相关 PDF", number=int(totals[1]), unit="份")
            add("metric", "累计下载", number=int(totals[2]), unit="次")
            add("finding", "筛选口径", totals[3])
            i += 1
            continue
        for pattern, default, columns in (
            (group, "六组商品全景", ["商品组", "PDF 数", "累计下载", "可识别机构数下限"]),
            (daily, "逐日上传", ["上传日期", "PDF 数", "累计下载"]),
            (pdf, "相关 PDF", ["PDF 文件名", "机构", "报告日候选", "上传时间", "下载", "状态", "用途", "选用理由"]),
        ):
            if not pattern.match(line):
                continue
            rows = []
            while i < len(lines) and (match := pattern.match(lines[i])):
                values = list(match.groups())
                if pattern is pdf:
                    role, *values = values
                    values.insert(-1, role)
                rows.append([_number(value.strip()) if value else None for value in values])
                i += 1
            if pattern is pdf and not any(row[6] or row[7] for row in rows):
                columns = columns[:6]
                rows = [row[:6] for row in rows]
            title = blocks.pop()[0]["title"] if blocks and blocks[-1][0]["kind"] == "heading" else default
            if pattern is pdf:
                ranking_type = next((kind for prefix, kind in (
                    ("近三天新上传", "commodity_recent_3d"), ("当日新上传", "commodity_today"),
                    ("单篇PDF下载", "single_pdf_top"),
                ) if title.startswith(prefix)), None)
                if ranking_type and ranking_names.get(ranking_type) == [row[0] for row in rows]:
                    add("ranking", title)
                    blocks[-1][0]["ranking_type"] = ranking_type
                else:
                    add("table", title, columns=["序号", *columns], rows=[[j, *row] for j, row in enumerate(rows, 1)])
            else:
                add("table", title, columns=columns, rows=rows)
            break
        else:
            i += 1
            if line.startswith("### "):
                add("heading", line[4:])
                continue
            clean = re.sub(r"^(?:\d+[.)]\s*|- )", "", line)
            title, separator, detail = clean.partition("：")
            if pdf.match(following) and title.startswith(("近三天新上传", "当日新上传", "单篇PDF下载")):
                if detail.strip():
                    add("finding", "榜单口径", detail.strip())
                add("heading", title)
            elif len(title) <= 80 and separator and not detail.strip():
                add("heading", title)
            elif len(title) <= 55 and separator:
                caution = any(word in title for word in ("限制", "待核", "不确定", "材料状态", "可靠状态", "边界", "真实下载增量"))
                add("limitation" if caution else "finding", title, detail.strip())
            else:
                add("finding", "分析要点", clean)
    return blocks


def upgrade():
    blocks = op.create_table(
        "codex_pdf_analysis_blocks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("section_id", sa.Integer(), sa.ForeignKey("codex_pdf_analysis_sections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content_text", sa.Text()),
        sa.Column("number_value", sa.Float()),
        sa.Column("unit", sa.String(32)),
        sa.Column("ranking_type", sa.String(48)),
        sa.UniqueConstraint("section_id", "ordinal", name="uq_codex_pdf_blocks_order"),
        sa.CheckConstraint("kind IN ('heading','finding','limitation','metric','table','ranking')", name="ck_codex_pdf_blocks_kind"),
    )
    cells = op.create_table(
        "codex_pdf_analysis_cells",
        sa.Column("block_id", sa.Integer(), sa.ForeignKey("codex_pdf_analysis_blocks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("row_no", sa.Integer(), primary_key=True),
        sa.Column("column_no", sa.Integer(), primary_key=True),
        sa.Column("text_value", sa.Text()),
        sa.Column("number_value", sa.Float()),
    )
    conn = op.get_bind()
    rankings = {}
    for run_id, kind, name in conn.execute(sa.text("""
        SELECT r.run_id, r.ranking_type, s.name
        FROM codex_pdf_analysis_rankings r JOIN codex_pdf_analysis_sources s
        ON s.id = r.source_id AND s.run_id = r.run_id
        WHERE r.ranking_type IN ('commodity_recent_3d', 'commodity_today', 'single_pdf_top')
        AND r.topic_name IS NULL ORDER BY r.rank
    """)):
        rankings.setdefault(run_id, {}).setdefault(kind, []).append(name)
    sections = conn.execute(sa.text("SELECT id, run_id, body_text FROM codex_pdf_analysis_sections")).mappings().all()
    for section in sections:
        for ordinal, (block, rows) in enumerate(_legacy_blocks(section["body_text"], rankings.get(section["run_id"])), 1):
            block_id = conn.execute(blocks.insert().values(section_id=section["id"], ordinal=ordinal, **block)).inserted_primary_key[0]
            if rows:
                conn.execute(cells.insert(), [
                    dict(block_id=block_id, row_no=r, column_no=c,
                         text_value=value if isinstance(value, str) else None,
                         number_value=value if isinstance(value, (int, float)) else None)
                    for r, row in enumerate(rows) for c, value in enumerate(row)
                ])


def downgrade():
    # Structured-only runs have no equivalent legacy text.
    if op.get_bind().execute(sa.text("SELECT 1 FROM codex_pdf_analysis_sections WHERE body_text = '' LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade while structured-only PDF reports exist")
    op.drop_table("codex_pdf_analysis_cells")
    op.drop_table("codex_pdf_analysis_blocks")
