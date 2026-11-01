"""Persist minimal per-platform-account course catalog snapshots.

Revision ID: 0003_platform_courses
Revises: 0002_user_sessions
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_platform_courses"
down_revision = "0002_user_sessions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "platform_courses",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("account_id", sa.String(length=32), nullable=False),
        sa.Column("course_key", sa.String(length=160), nullable=False),
        sa.Column("course_name", sa.String(length=240), nullable=False),
        sa.Column("teacher_name", sa.String(length=160), nullable=False),
        sa.Column("semester", sa.String(length=120), nullable=False),
        sa.Column("resource_status", sa.String(length=40), nullable=False),
        sa.Column("source_verified", sa.Boolean(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["platform_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "course_key", name="uq_platform_course_account_key"),
    )
    op.create_index("ix_platform_courses_account_id", "platform_courses", ["account_id"], unique=False)
    op.create_index("ix_platform_courses_last_synced_at", "platform_courses", ["last_synced_at"], unique=False)
    op.create_index("ix_platform_courses_account_synced", "platform_courses", ["account_id", "last_synced_at"], unique=False)


def downgrade():
    op.drop_index("ix_platform_courses_account_synced", table_name="platform_courses")
    op.drop_index("ix_platform_courses_last_synced_at", table_name="platform_courses")
    op.drop_index("ix_platform_courses_account_id", table_name="platform_courses")
    op.drop_table("platform_courses")
