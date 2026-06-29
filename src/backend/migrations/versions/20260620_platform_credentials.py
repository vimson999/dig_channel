"""add platform credential accounts

Revision ID: 20260620_platform_credentials
Revises: 20260619_report_video_hot_indexes
Create Date: 2026-06-20

"""
from alembic import op
import sqlalchemy as sa


revision = "20260620_platform_credentials"
down_revision = "20260619_report_video_hot_indexes"
branch_labels = None
depends_on = None


def upgrade():
    _create_table_if_missing(
        "platform_credential_accounts",
        """
        CREATE TABLE public.platform_credential_accounts (
            id varchar(36) PRIMARY KEY,
            platform varchar(40) NOT NULL,
            name varchar(120) NOT NULL,
            credential_type varchar(40) NOT NULL DEFAULT 'cookie_header',
            purposes jsonb NOT NULL,
            status varchar(24) NOT NULL DEFAULT 'active',
            priority integer NOT NULL DEFAULT 100,
            owner_label varchar(120),
            encrypted_payload text NOT NULL,
            payload_fingerprint varchar(64) NOT NULL UNIQUE,
            masked_summary varchar(255),
            notes text,
            last_validated_at timestamp,
            last_used_at timestamp,
            last_error text,
            extra jsonb NOT NULL,
            created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )
    _create_index_if_allowed(
        "ix_platform_credential_accounts_platform",
        "platform_credential_accounts",
        "CREATE INDEX ix_platform_credential_accounts_platform "
        "ON public.platform_credential_accounts (platform)",
    )
    _create_index_if_allowed(
        "ix_platform_credential_accounts_status",
        "platform_credential_accounts",
        "CREATE INDEX ix_platform_credential_accounts_status "
        "ON public.platform_credential_accounts (status)",
    )

    _create_table_if_missing(
        "platform_credential_bindings",
        """
        CREATE TABLE public.platform_credential_bindings (
            id varchar(36) PRIMARY KEY,
            account_id varchar(36) NOT NULL REFERENCES public.platform_credential_accounts(id) ON DELETE CASCADE,
            scope varchar(80) NOT NULL,
            resource_type varchar(50),
            resource_id varchar(120),
            status varchar(24) NOT NULL DEFAULT 'active',
            priority integer NOT NULL DEFAULT 100,
            notes text,
            created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )
    _create_index_if_allowed(
        "ix_platform_credential_bindings_account_id",
        "platform_credential_bindings",
        "CREATE INDEX ix_platform_credential_bindings_account_id "
        "ON public.platform_credential_bindings (account_id)",
    )
    _create_index_if_allowed(
        "ix_platform_credential_bindings_scope",
        "platform_credential_bindings",
        "CREATE INDEX ix_platform_credential_bindings_scope "
        "ON public.platform_credential_bindings (scope)",
    )
    _create_index_if_allowed(
        "ix_platform_credential_bindings_status",
        "platform_credential_bindings",
        "CREATE INDEX ix_platform_credential_bindings_status "
        "ON public.platform_credential_bindings (status)",
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS public.ix_platform_credential_bindings_status")
    op.execute("DROP INDEX IF EXISTS public.ix_platform_credential_bindings_scope")
    op.execute("DROP INDEX IF EXISTS public.ix_platform_credential_bindings_account_id")
    op.execute("DROP TABLE IF EXISTS public.platform_credential_bindings")
    op.execute("DROP INDEX IF EXISTS public.ix_platform_credential_accounts_status")
    op.execute("DROP INDEX IF EXISTS public.ix_platform_credential_accounts_platform")
    op.execute("DROP TABLE IF EXISTS public.platform_credential_accounts")


def _table_exists(table_name):
    return bool(op.get_bind().execute(
        sa.text("SELECT to_regclass(:table_name)"),
        {"table_name": f"public.{table_name}"},
    ).scalar())


def _create_table_if_missing(table_name, sql):
    if _table_exists(table_name):
        return
    op.execute(sql)


def _create_index_if_allowed(index_name, table_name, sql):
    bind = op.get_bind()
    exists = bind.execute(
        sa.text("SELECT to_regclass(:index_name)"),
        {"index_name": f"public.{index_name}"},
    ).scalar()
    if exists:
        return
    can_create = bind.execute(
        sa.text(
            """
            SELECT pg_has_role(c.relowner, 'MEMBER')
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = :table_name
            LIMIT 1
            """
        ),
        {"table_name": table_name},
    ).scalar()
    if not can_create:
        return
    op.execute(sql)
