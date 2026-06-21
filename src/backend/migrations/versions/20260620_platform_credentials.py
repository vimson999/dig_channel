"""add platform credential accounts

Revision ID: 20260620_platform_credentials
Revises: 20260619_report_video_hot_indexes
Create Date: 2026-06-20

"""
from alembic import op


revision = "20260620_platform_credentials"
down_revision = "20260619_report_video_hot_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.platform_credential_accounts (
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
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_platform_credential_accounts_platform "
        "ON public.platform_credential_accounts (platform)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_platform_credential_accounts_status "
        "ON public.platform_credential_accounts (status)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.platform_credential_bindings (
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
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_platform_credential_bindings_account_id "
        "ON public.platform_credential_bindings (account_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_platform_credential_bindings_scope "
        "ON public.platform_credential_bindings (scope)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_platform_credential_bindings_status "
        "ON public.platform_credential_bindings (status)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS public.ix_platform_credential_bindings_status")
    op.execute("DROP INDEX IF EXISTS public.ix_platform_credential_bindings_scope")
    op.execute("DROP INDEX IF EXISTS public.ix_platform_credential_bindings_account_id")
    op.execute("DROP TABLE IF EXISTS public.platform_credential_bindings")
    op.execute("DROP INDEX IF EXISTS public.ix_platform_credential_accounts_status")
    op.execute("DROP INDEX IF EXISTS public.ix_platform_credential_accounts_platform")
    op.execute("DROP TABLE IF EXISTS public.platform_credential_accounts")
