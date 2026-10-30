"""통화 사건의 분석 상태와 구조화된 지시를 추가합니다."""

import sqlalchemy as sa
from alembic import op

revision = "0004_call_events"
down_revision = "0003_push_registrations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """기존 사건을 보존하면서 분석 전 사건 저장을 허용합니다."""
    op.alter_column("security_events", "classification", nullable=True)
    op.add_column("security_events", sa.Column("requires_attention", sa.Boolean()))
    op.add_column("security_events", sa.Column("analysis_status", sa.String(32)))
    op.add_column("security_events", sa.Column("analysis_error_code", sa.String(64)))
    op.create_table(
        "call_instructions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "event_id", sa.Uuid(), sa.ForeignKey("security_events.id"), nullable=False
        ),
        sa.Column("instruction_type", sa.String(64), nullable=False),
        sa.Column("description", sa.Text()),
    )
    op.create_index("ix_call_instructions_event_id", "call_instructions", ["event_id"])


def downgrade() -> None:
    """미분류 사건이 있으면 데이터 손실 없이 되돌리기를 중단합니다."""
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM security_events WHERE classification IS NULL)"
        )
    ):
        raise RuntimeError("미분류 사건을 처리한 뒤 마이그레이션을 되돌려야 합니다")
    op.drop_table("call_instructions")
    for column in ("analysis_error_code", "analysis_status", "requires_attention"):
        op.drop_column("security_events", column)
    op.alter_column("security_events", "classification", nullable=False)
