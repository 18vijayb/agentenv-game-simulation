import pytest

from agent_env.config import configure, reset_config
from agent_env.store.object_store.local_object_store import LocalFilesystemObjectStore


@pytest.fixture
def store(tmp_path):
    store = LocalFilesystemObjectStore(str(tmp_path / "objects"))
    configure(object_store=store)
    yield store
    reset_config()
