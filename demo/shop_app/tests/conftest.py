import pytest

from tests.acme_sandbox import Sandbox


@pytest.fixture(autouse=True, scope="session")
def acme(request):
    """Point the shop at a local acme sandbox for the whole test run."""
    sandbox = Sandbox().start()
    patch = pytest.MonkeyPatch()
    patch.setenv("ACME_BASE_URL", sandbox.url)
    yield sandbox
    patch.undo()
    sandbox.shutdown()
