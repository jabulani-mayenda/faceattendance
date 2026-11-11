"""Separate a student's department from their attendance class/group."""
from alembic import op
import sqlalchemy as sa

revision = '0003_person_department'
down_revision = '0002_teacher_assignments'
branch_labels = None
depends_on = None


def upgrade():
    columns = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('people')}
    if 'department' not in columns:
        with op.batch_alter_table('people') as batch:
            batch.add_column(sa.Column('department', sa.String(length=120), nullable=True))


def downgrade():
    columns = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('people')}
    if 'department' in columns:
        with op.batch_alter_table('people') as batch:
            batch.drop_column('department')
