"""Shared fixtures. Sample files are built fresh in each test's temp folder."""
from __future__ import annotations

import dataclasses

import pytest

from nova.config import Config


@pytest.fixture
def cfg(tmp_path):
    """Nova's config with the workspace pointed at the test's temp folder."""
    return dataclasses.replace(
        Config.from_env(),
        workspace=tmp_path,
        restrict_to_workspace=False,
        log_dir=tmp_path / "logs",
    )
