"""Top-level test configuration — cleans up sys.modules mocks after test session.

Both unit and integration tests inject MagicMock into ``sys.modules["claude_agent_sdk"]``
at module level so that ``main_server`` can be imported. This file saves the original
module references and restores them when the test session ends, preventing MagicMock
objects from leaking to any code that runs in the same Python process after tests.
"""

from __future__ import annotations

import sys


def pytest_sessionstart(session) -> None:  # noqa: ARG001
    """Capture original claude_agent_sdk modules before any test mocks replace them."""
    sys._saved_modules = {  # type: ignore[attr-defined]
        k: sys.modules.get(k)
        for k in ("claude_agent_sdk", "claude_agent_sdk.types")
    }


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    """Restore original claude_agent_sdk modules, removing test Mock pollution."""
    saved: dict = getattr(sys, "_saved_modules", {})
    for key, original in saved.items():
        if original is not None:
            sys.modules[key] = original
        else:
            sys.modules.pop(key, None)
    if hasattr(sys, "_saved_modules"):
        delattr(sys, "_saved_modules")
