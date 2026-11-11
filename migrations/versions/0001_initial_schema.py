"""Create the initial FaceTrack schema from the versioned SQLAlchemy models."""
from alembic import op

revision = '0001_initial_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    from app.models import Base
    Base.metadata.create_all(bind=op.get_bind())


def downgrade():
    from app.models import Base
    Base.metadata.drop_all(bind=op.get_bind())
