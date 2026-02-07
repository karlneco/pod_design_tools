"""
PyCharm-specific test configuration workaround.

If you're having respx mocking issues in PyCharm, you can use this conftest
to automatically skip problematic tests when running in PyCharm.
"""
import os
import pytest


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "pycharm_skip: Skip tests that don't work well in PyCharm"
    )


def is_pycharm():
    """Detect if we're running inside PyCharm."""
    return (
        'PYCHARM_HOSTED' in os.environ or
        'PYTEST_CURRENT_TEST' in os.environ and 'PyCharm' in os.environ.get('PYTEST_CURRENT_TEST', '')
    )


@pytest.fixture(autouse=True)
def skip_in_pycharm(request):
    """Auto-skip tests marked with pycharm_skip when running in PyCharm."""
    if request.node.get_closest_marker('pycharm_skip'):
        if is_pycharm():
            pytest.skip('Skipped in PyCharm due to environment issues')
