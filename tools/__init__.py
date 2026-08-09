"""
Tools package for live web discovery.
"""

from tools.registry import TOOLS, ALL_TOOLS, get_tool_func
from tools.shared.web_search import web_search

__all__ = ["TOOLS", "ALL_TOOLS", "get_tool_func", "web_search"]
