"""Keep automated tests away from the operator's local attendance database."""

import os
import tempfile
from pathlib import Path
from uuid import uuid4


test_db = Path(tempfile.gettempdir()) / f'facetrack-tests-{os.getpid()}-{uuid4().hex}.db'
os.environ['DATABASE_URL'] = f'sqlite:///{test_db.as_posix()}'
