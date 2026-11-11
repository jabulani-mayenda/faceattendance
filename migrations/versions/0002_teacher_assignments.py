"""Add departments and teacher assignments to school schedules."""
from alembic import op
import sqlalchemy as sa

revision = '0002_teacher_assignments'
down_revision = '0001_initial_schema'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {column['name'] for column in inspector.get_columns('users')}
    if 'department' not in user_columns:
        with op.batch_alter_table('users') as batch:
            batch.add_column(sa.Column('department', sa.String(length=120), nullable=True))

    session_columns = {column['name'] for column in inspector.get_columns('attendance_sessions')}
    if 'teacher_id' not in session_columns:
        with op.batch_alter_table('attendance_sessions') as batch:
            batch.add_column(sa.Column('teacher_id', sa.Integer(), nullable=True))
            batch.create_foreign_key('fk_attendance_sessions_teacher_id_users', 'users', ['teacher_id'], ['id'], ondelete='SET NULL')
            batch.create_index('ix_attendance_sessions_teacher_id', ['teacher_id'], unique=False)


def downgrade():
    with op.batch_alter_table('attendance_sessions') as batch:
        batch.drop_index('ix_attendance_sessions_teacher_id')
        batch.drop_constraint('fk_attendance_sessions_teacher_id_users', type_='foreignkey')
        batch.drop_column('teacher_id')
    with op.batch_alter_table('users') as batch:
        batch.drop_column('department')
