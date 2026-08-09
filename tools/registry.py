"""
Tool registry with auto-discovery.

Discovers tools from tools/shared/ (such as web_search).
"""

import importlib
import logging
import pkgutil
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


def _discover_tools() -> dict[str, dict]:
    """Discover tools from tools/shared directory."""
    tools = {}
    folder_path = Path(__file__).parent / "shared"

    if not folder_path.exists():
        return tools

    for _, module_name, _ in pkgutil.iter_modules([str(folder_path)]):
        if module_name.startswith("_"):
            continue

        try:
            module = importlib.import_module(f"tools.shared.{module_name}")
            if hasattr(module, "TOOL_CONFIG"):
                config = module.TOOL_CONFIG
                tool_name = config["name"]
                if hasattr(module, tool_name):
                    tools[tool_name] = {
                        "config": config,
                        "func": getattr(module, tool_name),
                        "folder": "shared"
                    }
        except Exception as e:
            logger.error(f"[REGISTRY] Error loading tools.shared.{module_name}: {e}")

    return tools


ALL_TOOLS = _discover_tools()
TOOLS = {name: tool["func"] for name, tool in ALL_TOOLS.items()}


def get_tool_func(name: str) -> Callable | None:
    """Get a tool function by name."""
    if name in ALL_TOOLS:
        return ALL_TOOLS[name]["func"]
    return None
