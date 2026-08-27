import pytest

from wfm.store.db import connect
from wfm.store.migrate import migrate


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "test.db")
    migrate(connection)
    yield connection
    connection.close()
