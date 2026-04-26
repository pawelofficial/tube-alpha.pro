"""Proxy management service.

Handles Webshare residential proxy rotation for YouTube scraping.

Usage:
    from tube_alpha.services.proxy import ProxyService
    from tube_alpha.config import Settings

    proxy = ProxyService(Settings())
    proxies = proxy.get_requests_proxy(user_index=2)
    # {'http': 'http://user-2:pass@p.webshare.io:80', 'https': '...'}
"""

import logging
from typing import Dict, Optional

import requests

from tube_alpha.config import Settings

logger = logging.getLogger(__name__)


class ProxyService:
    def __init__(self, settings: Settings):
        self._settings = settings

    @property
    def is_configured(self) -> bool:
        return bool(self._settings.proxy_username and self._settings.proxy_password)

    def get_proxy_url(self, user_index: Optional[int] = None) -> Optional[str]:
        """Get proxy URL for a given user index.

        Args:
            user_index: If provided, uses base_username-{user_index}.
                        Otherwise uses the default username.
        """
        if not self.is_configured:
            return None

        if user_index is not None:
            username = f"{self._settings.proxy_base_username}-{user_index}"
        else:
            username = self._settings.proxy_username

        return f"http://{username}:{self._settings.proxy_password}@p.webshare.io:80"

    def get_requests_proxy(self, user_index: Optional[int] = None) -> Optional[Dict[str, str]]:
        """Get proxy dict for use with requests library."""
        url = self.get_proxy_url(user_index)
        if not url:
            return None
        return {"http": url, "https": url}

    def iter_proxy_users(self):
        """Iterate through all proxy user indices (2..count+1)."""
        for i in range(2, self._settings.proxy_user_count + 2):
            yield i

    def test_connection(self, user_index: Optional[int] = None) -> bool:
        """Test if the proxy works by hitting ipify."""
        proxies = self.get_requests_proxy(user_index)
        if not proxies:
            return False
        try:
            resp = requests.get(
                "https://api.ipify.org?format=json",
                proxies=proxies,
                timeout=5,
            )
            ip = resp.json().get("ip")
            logger.info("Proxy test OK, IP: %s", ip)
            return True
        except Exception as e:
            logger.error("Proxy test failed: %s", e)
            return False
