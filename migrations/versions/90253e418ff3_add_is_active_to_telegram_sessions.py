"""add is_active to telegram_sessions

Revision ID: 90253e418ff3
Revises: fc7f66ea8038
Create Date: 2025-03-30 01:47:13.302309

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '90253e418ff3'
down_revision: Union[str, None] = 'fc7f66ea8038'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
