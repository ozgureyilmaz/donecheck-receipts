import os

import pytest


def pytest_collection_modifyitems(items):
    if not (os.getenv("MODEL_API_KEY") or os.getenv("META_API_KEY")):
        skip = pytest.mark.skip(reason="Live calls require MODEL_API_KEY or META_API_KEY")
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip)
