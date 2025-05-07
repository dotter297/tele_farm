"""add is_active to telegram_sessions

Revision ID: fc7f66ea8038
Revises: 9fba597aaeb6
Create Date: 2025-03-30 01:41:06.962361

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fc7f66ea8038'
down_revision: Union[str, None] = '9fba597aaeb6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
