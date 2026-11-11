"""
Base API client with common utilities and error handling.

Provides rate limiting, error handling, and request utilities
for all external API clients.
"""

import logging
import time
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


class BaseAPIClient:
    """
    Base class for external API clients.
    
    Provides:
    - Rate limiting
    - Error handling
    - Common request utilities
    """

    def __init__(
        self,
        base_url: str,
        headers: Optional[Dict[str, str]] = None,
        rate_limit_delay: float = 2.0,
        timeout: int = 10
    ):
        """
        Initialize BaseAPIClient.
        
        Args:
            base_url: Base URL for API
            headers: Custom HTTP headers
            rate_limit_delay: Delay between requests in seconds
            timeout: Request timeout in seconds
        """
        self.base_url = base_url
        self.headers = headers or {"Accept": "application/json"}
        self.rate_limit_delay = rate_limit_delay
        self.timeout = timeout
        self._last_request_time = 0.0

    def _rate_limit(self) -> None:
        """Apply rate limiting based on last request time."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self._last_request_time = time.time()

    def _get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Make a GET request to the API.
        
        Args:
            endpoint: API endpoint (relative to base_url)
            params: Query parameters
            
        Returns:
            Parsed JSON response or None on error
        """
        self._rate_limit()
        try:
            url = f"{self.base_url}{endpoint}"
            response = requests.get(
                url,
                headers=self.headers,
                params=params,
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            logger.error(f"HTTP error {status} on GET {endpoint}: {e}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error on GET {endpoint}: {e}")
            return None

    def _post(
        self,
        endpoint: str,
        json_data: Dict[str, Any],
        params: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Make a POST request to the API.
        
        Args:
            endpoint: API endpoint
            json_data: JSON body
            params: Query parameters
            
        Returns:
            Parsed JSON response or None on error
        """
        self._rate_limit()
        try:
            url = f"{self.base_url}{endpoint}"
            response = requests.post(
                url,
                headers=self.headers,
                json=json_data,
                params=params,
                timeout=self.timeout
            )
            
            if response.status_code == 400:
                logger.error(f"Bad request (400) on POST {endpoint}: {response.text}")
                return None
            
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            logger.error(f"HTTP error {status} on POST {endpoint}: {e}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error on POST {endpoint}: {e}")
            return None
