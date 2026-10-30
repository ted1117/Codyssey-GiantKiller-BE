"""회원·가족·초대 테이블을 생성합니다."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_membership"
down_revision = None
branch_labels = None
depends_on = None

role = postgresql.ENUM("PARENT", "CHILD", name="family_role", create_type=False)


def upgrade() -> None:
    """가족별 역할과 초대 이력을 저장할 스키마를 생성합니다."""
    role.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("hashed_password", sa.String(1024), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_table(
        "families",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "family_members",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", role, nullable=False),
        sa.Column(
            "is_representative", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["family_id"], ["families.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint(
            "family_id", "user_id", name="uq_family_members_family_user"
        ),
        sa.CheckConstraint(
            "NOT is_representative OR role = 'CHILD'",
            name="ck_family_members_representative_child",
        ),
    )
    op.create_index("ix_family_members_user_id", "family_members", ["user_id"])
    op.create_index(
        "uq_family_members_representative",
        "family_members",
        ["family_id"],
        unique=True,
        postgresql_where=sa.text("is_representative"),
    )
    op.create_table(
        "family_invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_member_id", sa.Uuid(), nullable=False),
        sa.Column("role", role, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_by_user_id", sa.Uuid()),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["family_id"], ["families.id"]),
        sa.ForeignKeyConstraint(["created_by_member_id"], ["family_members.id"]),
        sa.ForeignKeyConstraint(["accepted_by_user_id"], ["users.id"]),
        sa.UniqueConstraint("token_hash"),
        sa.CheckConstraint(
            "(accepted_at IS NULL) = (accepted_by_user_id IS NULL)",
            name="ck_family_invitations_acceptance_pair",
        ),
        sa.CheckConstraint(
            "accepted_at IS NULL OR revoked_at IS NULL",
            name="ck_family_invitations_single_outcome",
        ),
    )
    op.create_index(
        "ix_family_invitations_family_id", "family_invitations", ["family_id"]
    )


def downgrade() -> None:
    """참조 순서에 맞춰 테이블과 역할 타입을 제거합니다."""
    op.drop_table("family_invitations")
    op.drop_table("family_members")
    op.drop_table("families")
    op.drop_table("users")
    role.drop(op.get_bind(), checkfirst=True)
