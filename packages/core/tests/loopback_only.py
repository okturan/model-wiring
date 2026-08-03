"""Run a test suite with every off-machine connection blocked.

Provider data is the part of this kit most likely to smuggle a live call into
the suite: a base URL added to an overlay silently turns a route that used to
be undeclared into one pointing at a real API. That has already happened twice
here, so CI runs the suite once under this guard.

Usage: ``python tests/loopback_only.py <test-directory>``

Only ``socket.connect`` is wrapped. ``create_connection``, ``http.client``,
``urllib``, and everything else in the stdlib funnel through it, so a single
guard covers them all, and loopback servers the tests start still work.
"""

from __future__ import annotations

import ipaddress
import socket
import sys
import unittest

_connect = socket.socket.connect

# A name resolving somewhere is a decision the test never made explicit, so
# only literal loopback addresses pass.
LOOPBACK_NAMES = frozenset({"localhost", ""})


def _guarded(self: socket.socket, address: object) -> object:
    host = address[0] if isinstance(address, tuple) else None
    if isinstance(host, str):
        try:
            off_machine = not ipaddress.ip_address(host).is_loopback
        except ValueError:
            off_machine = host not in LOOPBACK_NAMES
        if off_machine:
            raise AssertionError(
                f"a test tried to reach {address!r}, which is off this machine"
            )
    return _connect(self, address)


def main(directory: str) -> int:
    socket.socket.connect = _guarded  # type: ignore[method-assign]
    suite = unittest.TestLoader().discover(directory, top_level_dir=directory)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "tests"))
