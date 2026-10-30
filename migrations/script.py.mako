"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | Sequence[str] | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    """스키마 변경을 적용합니다."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """스키마 변경을 되돌립니다."""
    ${downgrades if downgrades else "pass"}
