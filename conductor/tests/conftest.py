"""Test fixtures for the conductor bridge.

The canonical bridge script lives at ``conductor/conductor_bridge.py``
(embedded into the binary via ``//go:embed`` in ``conductor/conductor_bridge_embed.go``).
To keep these tests running against the one canonical file - and to preserve the
existing ``from bridge import ...`` / ``mock.patch("bridge.<attr>")`` usage in
the test bodies - load that file under the module name ``bridge`` before any
test module is imported.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# repo_root/conductor/tests/conftest.py -> repo_root/conductor/conductor_bridge.py
_CANONICAL = (
    Path(__file__).resolve().parents[1]
    / "conductor_bridge.py"
)


def _load_canonical_bridge() -> None:
    if "bridge" in sys.modules:
        return
    if not _CANONICAL.is_file():
        raise FileNotFoundError(
            f"canonical bridge source not found at {_CANONICAL}; "
            "it should live at conductor/conductor_bridge.py"
        )
    spec = importlib.util.spec_from_file_location("bridge", _CANONICAL)
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the module can be patched/imported as "bridge".
    sys.modules["bridge"] = module
    spec.loader.exec_module(module)


_load_canonical_bridge()
