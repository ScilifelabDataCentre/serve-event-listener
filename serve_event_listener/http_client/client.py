"""HTTP client wrappers providing retries, token refresh, and logging."""

import logging
import time
from typing import Any, Callable, Mapping, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

Timeout = Tuple[float, float]


def _request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    params: Optional[Mapping[str, Any]] = None,
    json: Optional[Mapping[str, Any]] = None,
    headers: Optional[Mapping[str, str]] = None,
    verify: bool = True,
    timeout: Timeout = (3.05, 20.0),
    backoff_seconds=(1, 2, 4),
    token_fetcher: Optional[Callable[[], str]] = None,  # optional
    auth_scheme: str = "Token",  # can easily change to Bearer in the future
    sleep_fn: Callable[[float], None] = time.sleep,  # overridable in tests
    **request_kwargs,  # pass-through for requests.Session.request
) -> Optional[requests.Response]:
    merged_headers = {**(session.headers or {}), **(headers or {})}

    if not backoff_seconds:
        raise ValueError("backoff_seconds must contain at least one delay value")

    # If token_fetcher is provided but Authorization is missing, fetch once
    refreshed = False
    if token_fetcher and "Authorization" not in merged_headers:
        tok = token_fetcher()
        if tok:
            merged_headers["Authorization"] = f"{auth_scheme} {tok}"

    try:
        last = None
        for i, delay in enumerate(backoff_seconds):
            resp = session.request(
                method=method.upper(),
                url=url,
                params=params,
                json=json,
                headers=merged_headers or None,
                verify=verify,
                timeout=timeout,
                **request_kwargs,   # can forward extras (allow_redirects, stream, etc.)
            )
            code = resp.status_code
            last = resp

            if 200 <= code < 300:
                return resp
            if code in (400, 404):
                return resp
            if code in (401, 403) and token_fetcher and not refreshed:
                tok = token_fetcher()
                if tok:
                    merged_headers["Authorization"] = f"Token {tok}"
                refreshed = True
                sleep_fn(delay)
                continue
            if 500 <= code < 600:
                # Only sleep if there’s another attempt
                if i < len(backoff_seconds) - 1:
                    sleep_fn(delay)
                    continue
            return resp  # other statuses returned as-is
        return last

    except requests.exceptions.ConnectTimeout as e:
        logger.warning("ConnectTimeout: %s", e)
    except requests.exceptions.ReadTimeout as e:
        logger.warning("ReadTimeout: %s", e)
    except requests.exceptions.Timeout as e:
        logger.warning("Timeout: %s", e)
    except requests.exceptions.ConnectionError as e:
        logger.warning("ConnectionError: %s", e)
    except requests.exceptions.RequestException as e:
        logger.warning("RequestException: %s", e)
    return None


def get(session: requests.Session, url: str, **kwargs) -> Optional[requests.Response]:
    """
    Send a POST request using the provided requests.Session.

    This is a thin wrapper around `_request` that applies standard retry and
    error-handling logic for POST calls. The `data` argument is sent as JSON
    in the request body.

    Args:
        session (requests.Session): A configured requests session (e.g., from
            `make_session`).
        url (str): The target URL for the POST request.
        data (dict, optional): A dictionary of data to serialize as JSON in the
            request body. Defaults to an empty dict.
        **kwargs: Additional keyword arguments forwarded to `_request`
            (e.g., headers, timeout, verify, backoff_seconds, token_fetcher).

    Returns:
        Optional[requests.Response]: The HTTP response object if a request was
        successfully made, or None if a network exception occurred.
    """
    return _request(session, "GET", url, **kwargs)


def post(
    session: requests.Session, url: str, *, data=None, **kwargs
) -> Optional[requests.Response]:
    """
    Send a POST request using the provided requests.Session.

    This is a thin wrapper around `_request` that applies standard retry and
    error-handling logic for POST calls. The `data` argument is sent as JSON
    in the request body.

    Args:
        session (requests.Session): A configured requests session (e.g., from
            `make_session`).
        url (str): The target URL for the POST request.
        data (dict, optional): A dictionary of data to serialize as JSON in the
            request body. Defaults to an empty dict.
        **kwargs: Additional keyword arguments forwarded to `_request`
            (e.g., headers, timeout, verify, backoff_seconds, token_fetcher).

    Returns:
        Optional[requests.Response]: The HTTP response object if a request was
        successfully made, or None if a network exception occurred.
    """
    return _request(session, "POST", url, json=(data or {}), **kwargs)
