"""Offline tests must never contact a model provider, browser daemon or model hub."""
import socket

import pytest


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError('Network is disabled in offline tests; mock the external boundary.')

    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket.socket, 'connect_ex', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
