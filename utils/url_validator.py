"""
Strict source URL validation and sanitization utility.

Guards against SSRF, XSS, and metadata service probing by enforcing that all
source URLs used across topic discovery and editorial synthesis are strictly public
HTTP/HTTPS endpoints with valid hostnames.
"""

import ipaddress
import logging
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def is_valid_public_url(url: str) -> bool:
    """Validate that a URL is a well-formed, public HTTP or HTTPS web address.

    Rejects:
    - Non-HTTP/HTTPS schemes (javascript:, data:, file:, gopher:, ftp:)
    - Localhost names and loopback addresses (127.0.0.0/8, ::1)
    - Private IP ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
    - Link-local and cloud metadata endpoints (169.254.0.0/16, fe80::/10)
    - Hostnames without valid domain dots or valid characters
    """
    if not url or not isinstance(url, str):
        return False

    trimmed = url.strip()
    try:
        parsed = urlparse(trimmed)
    except Exception:
        return False

    # 1. Scheme must be strictly http or https
    if parsed.scheme.lower() not in ("http", "https"):
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    hostname_lower = hostname.lower().strip(".")

    # 2. Rejection of local hostnames
    if (
        hostname_lower in ("localhost", "0.0.0.0", "broadcast")
        or hostname_lower.endswith(".localhost")
        or hostname_lower.endswith(".local")
    ):
        return False

    # 3. IP address boundary enforcement
    try:
        ip = ipaddress.ip_address(hostname_lower)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
            or str(ip).startswith("169.254.")
        ):
            return False
    except ValueError:
        # Hostname is a domain name, not an IP address
        if "." not in hostname_lower:
            return False
        # Basic character validation for domain label
        if any(c in hostname_lower for c in (" ", "\t", "\r", "\n", "<", ">", '"', "'", ";", "{", "}")):
            return False

    return True


def sanitize_source_urls(urls: list[Any]) -> list[str]:
    """Filter, sanitize, and deduplicate a list of candidate source URLs.

    Returns only valid public HTTP/HTTPS URLs preserving insertion order.
    """
    if not urls:
        return []

    sanitized: list[str] = []
    seen: set[str] = set()

    for item in urls:
        if isinstance(item, str):
            clean_item = item.strip().rstrip(".,;:)>]")
            if clean_item and clean_item not in seen and is_valid_public_url(clean_item):
                seen.add(clean_item)
                sanitized.append(clean_item)

    return sanitized
