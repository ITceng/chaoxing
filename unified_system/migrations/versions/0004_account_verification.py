"""Track account-specific authorization verification time.

Revision ID: 0004_account_verification
Revises: 0003_platform_courses
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_account_verification"
down_revision = "0003_platform_courses"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("platform_accounts", sa.Column("verified_at", sa.DateTime(), nullable=True))
    op.create_index("ix_platform_accounts_verified_at", "platform_accounts", ["verified_at"], unique=False)


def downgrade():
    op.drop_index("ix_platform_accounts_verified_at", table_name="platform_accounts")
    op.drop_column("platform_accounts", "verified_at")
