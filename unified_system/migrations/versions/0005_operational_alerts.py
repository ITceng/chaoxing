"""Persist deduplicated operational alerts and recovery state.

Revision ID: 0005_operational_alerts
Revises: 0004_account_verification
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_operational_alerts"
down_revision = "0004_account_verification"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "operational_alerts",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_notified_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("notification_failures", sa.Integer(), nullable=False),
        sa.Column("last_notification_error_type", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("fingerprint", name="uq_operational_alert_fingerprint"),
        sa.CheckConstraint("severity IN ('INFO', 'WARNING', 'ERROR', 'CRITICAL')", name="ck_operational_alert_severity"),
        sa.CheckConstraint("state IN ('active', 'resolved')", name="ck_operational_alert_state"),
        sa.CheckConstraint("occurrence_count >= 1 AND notification_failures >= 0", name="ck_operational_alert_counts"),
    )
    op.create_index("ix_operational_alerts_code", "operational_alerts", ["code"])
    op.create_index("ix_operational_alerts_severity", "operational_alerts", ["severity"])
    op.create_index("ix_operational_alerts_state", "operational_alerts", ["state"])
    op.create_index("ix_operational_alerts_state_severity_seen", "operational_alerts",
                    ["state", "severity", "last_seen_at"])


def downgrade():
    op.drop_index("ix_operational_alerts_state_severity_seen", table_name="operational_alerts")
    op.drop_index("ix_operational_alerts_state", table_name="operational_alerts")
    op.drop_index("ix_operational_alerts_severity", table_name="operational_alerts")
    op.drop_index("ix_operational_alerts_code", table_name="operational_alerts")
    op.drop_table("operational_alerts")
