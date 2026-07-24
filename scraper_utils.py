import time
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_RETRY_COUNT = 3
DEFAULT_BACKOFF_FACTOR = 0.5


def build_requests_session(
    retries: int = DEFAULT_RETRY_COUNT,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(
        max_retries=Retry(
            total=retries,
            connect=retries,
            read=retries,
            status=retries,
            backoff_factor=backoff_factor,
            allowed_methods=["GET", "HEAD"],
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False,
        )
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    session.timeout = timeout
    session.trust_env = False
    return session


def request_with_retries(
    session: requests.Session,
    url: str,
    *,
    headers: Optional[dict] = None,
    timeout: Optional[float] = None,
    max_retries: int = DEFAULT_RETRY_COUNT,
    delay_seconds: float = 0.0,
) -> requests.Response:
    last_error: Optional[Exception] = None
    effective_timeout = timeout if timeout is not None else getattr(session, "timeout", DEFAULT_TIMEOUT_SECONDS)

    for attempt in range(max_retries):
        try:
            response = session.get(url, headers=headers, timeout=effective_timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            last_error = error
            if attempt < max_retries - 1:
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                continue

    if last_error is None:
        raise requests.RequestException(f"Request failed for {url}")
    raise last_error
