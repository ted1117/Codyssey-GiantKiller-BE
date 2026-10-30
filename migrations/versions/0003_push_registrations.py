"""사용자별 FCM FID 저장 테이블을 추가합니다."""

import sqlalchemy as sa
from alembic import op

revision = "0003_push_registrations"
down_revision = "0002_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """기기 정보 없이 사용자와 FID의 유일한 연결을 저장합니다."""
    op.create_table(
        "push_registrations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("fid", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("fid"),
    )
    op.create_index("ix_push_registrations_user_id", "push_registrations", ["user_id"])


def downgrade() -> None:
    """기존 사건·가족·사용자는 유지하고 FID 테이블만 제거합니다."""
    op.drop_index("ix_push_registrations_user_id", table_name="push_registrations")
    op.drop_table("push_registrations")
