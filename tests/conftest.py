from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_hubspot import FakeHubSpot  # noqa: E402

from crm_platform.hubspot.client import HubSpotClient  # noqa: E402


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def fake() -> FakeHubSpot:
    return FakeHubSpot()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def client(fake: FakeHubSpot, clock: Clock) -> HubSpotClient:
    return HubSpotClient(fake, sleep=clock.sleep, clock=clock)
