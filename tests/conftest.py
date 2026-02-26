import json
import os
import pytest
import tempfile
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURES_DIR / name) as f:
        return json.load(f)


@pytest.fixture
def tmp_state_path(tmp_path):
    return str(tmp_path / "state.json")


@pytest.fixture
def eth_beaconcha_fixture():
    return load_fixture("eth_slashings_beaconcha.json")


@pytest.fixture
def eth_attester_slashings_fixture():
    return load_fixture("eth_attester_slashings.json")


@pytest.fixture
def eth_proposer_slashings_fixture():
    return load_fixture("eth_proposer_slashings.json")


@pytest.fixture
def sol_vote_accounts_fixture():
    return load_fixture("sol_vote_accounts.json")


@pytest.fixture
def sui_system_state_fixture():
    return load_fixture("sui_system_state.json")


@pytest.fixture
def eth_block_with_slashings_fixture():
    return load_fixture("eth_block_with_slashings.json")
