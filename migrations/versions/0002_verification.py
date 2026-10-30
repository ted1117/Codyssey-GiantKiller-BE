"""가족 확인과 최소 사건·알림 요청 테이블을 생성합니다."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_verification"
down_revision = "0001_membership"
branch_labels = None
depends_on = None

verification_status = postgresql.ENUM(
    "PENDING",
    "CONFIRMED",
    "DENIED",
    "EXPIRED",
    name="verification_status",
    create_type=False,
)
verification_answer = postgresql.ENUM(
    "SENT", "NOT_SENT", name="verification_answer", create_type=False
)
notification_type = postgresql.ENUM(
    "VERIFY_REQUEST",
    "VERIFY_WAITING",
    "VERIFY_CONFIRMED",
    "VERIFY_DENIED",
    "VERIFY_EXPIRED",
    name="notification_type",
    create_type=False,
)


def upgrade() -> None:
    """사건·확인 요청·자녀별 응답·알림 요청의 스키마를 생성합니다."""
    for enum in (verification_status, verification_answer, notification_type):
        enum.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "security_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("parent_member_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("classification", sa.String(64), nullable=False),
        sa.Column("risk_level", sa.String(32)),
        sa.Column("summary", sa.Text()),
        sa.Column("status", sa.String(32), server_default="OPEN", nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["family_id"], ["families.id"]),
        sa.ForeignKeyConstraint(["parent_member_id"], ["family_members.id"]),
    )
    op.create_table(
        "verification_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("parent_member_id", sa.Uuid(), nullable=False),
        sa.Column("status", verification_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["event_id"], ["security_events.id"]),
        sa.ForeignKeyConstraint(["family_id"], ["families.id"]),
        sa.ForeignKeyConstraint(["parent_member_id"], ["family_members.id"]),
        sa.UniqueConstraint("event_id"),
        sa.CheckConstraint(
            "expires_at >= created_at", name="ck_verification_requests_expiry"
        ),
        sa.CheckConstraint(
            "finalized_at IS NULL OR finalized_at >= created_at",
            name="ck_verification_requests_finalized_time",
        ),
        sa.CheckConstraint(
            "(status = 'PENDING') = (finalized_at IS NULL)",
            name="ck_verification_requests_finalized_state",
        ),
    )
    op.create_index(
        "ix_verification_requests_status_expiry",
        "verification_requests",
        ["status", "expires_at"],
    )
    op.create_table(
        "verification_responses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("verification_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("response", verification_answer),
        sa.Column("responded_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["verification_id"], ["verification_requests.id"]),
        sa.ForeignKeyConstraint(["member_id"], ["family_members.id"]),
        sa.UniqueConstraint(
            "verification_id", "member_id", name="uq_verification_responses_member"
        ),
        sa.CheckConstraint(
            "(response IS NULL) = (responded_at IS NULL)",
            name="ck_verification_responses_answer_time",
        ),
    )
    op.create_table(
        "notification_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("verification_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("message_type", notification_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["event_id"], ["security_events.id"]),
        sa.ForeignKeyConstraint(["verification_id"], ["verification_requests.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint(
            "verification_id",
            "user_id",
            "message_type",
            name="uq_notification_requests_recipient_type",
        ),
    )


def downgrade() -> None:
    """기존 회원 테이블을 남기고 신규 테이블과 타입을 제거합니다."""
    op.drop_table("notification_requests")
    op.drop_table("verification_responses")
    op.drop_table("verification_requests")
    op.drop_table("security_events")
    for enum in (notification_type, verification_answer, verification_status):
        enum.drop(op.get_bind(), checkfirst=True)
