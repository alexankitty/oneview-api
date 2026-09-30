"""
ThreatDown OneView (MSP) API client.

    from oneview_api import OneViewClient

    api = OneViewClient.from_config("config.json")
    for site in api.get_sites():
        endpoints = api.get_endpoints(site.account_id)

Requires: pip install requests
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, NamedTuple

import requests

__all__ = [
    "ApiError", "Config", "Endpoint", "MatchResult", "OneViewClient", "Site",
    "endpoint_id", "endpoint_label", "endpoint_names", "match_names", "parse_names",
]

BASE_URL = "https://api.threatdown.com"
TOKEN_PATH = "/oneview/oauth2/token"
SITES_PATH = "/oneview/v1/sites"
ENDPOINTS_SEARCH_PATH = "/oneview/v1/endpoints"
ENDPOINT_DELETE_PATH = "/oneview/v1/accounts/{account_id}/endpoints/{endpoint_id}"

SCOPE = "read write execute"
PAGE_SIZE = 2000
MAX_RETRIES = 5

NAME_KEYS = ("name", "host_name", "hostname", "display_name",
             "fully_qualified_host_name", "alias")

Endpoint = dict[str, Any]


@dataclass(frozen=True)
class Site:
    name: str
    account_id: str


class MatchResult(NamedTuple):
    matched: list[tuple[str, Endpoint]]
    ambiguous: list[tuple[str, list[Endpoint]]]
    not_found: list[str]


class ApiError(RuntimeError):
    pass


@dataclass
class Config:
    client_id: str = ""
    client_secret: str = ""
    base_url: str = BASE_URL
    timeout: float = 60

    @classmethod
    def load(cls, path: str | Path) -> Config:
        path = Path(path)
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)


def _check(resp: requests.Response, what: str) -> None:
    if not resp.ok:
        raise ApiError(f"{what} failed: HTTP {resp.status_code} "
                       f"({resp.request.method} {resp.url}): {resp.text[:300]}")


class OneViewClient:
    def __init__(self, base_url: str = BASE_URL, timeout: float = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    @classmethod
    def from_config(cls, config: Config | str | Path) -> OneViewClient:
        """Build a client from a Config or config.json path and authenticate it."""
        if not isinstance(config, Config):
            config = Config.load(config)
        if not config.client_id or not config.client_secret:
            raise ApiError("Config is missing client_id or client_secret.")
        client = cls(config.base_url, config.timeout)
        client.authenticate(config.client_id, config.client_secret)
        return client

    def _request_token(self, client_id: str, client_secret: str,
                       scope: str | None) -> requests.Response:
        data = {"grant_type": "client_credentials"}
        if scope:
            data["scope"] = scope
        return self.session.post(self.base_url + TOKEN_PATH,
                                 auth=(client_id, client_secret),
                                 data=data, timeout=self.timeout)

    def authenticate(self, client_id: str, client_secret: str) -> None:
        resp = self._request_token(client_id, client_secret, SCOPE)
        if resp.status_code == 400:
            # Client may not have every scope; let the server grant what it has.
            resp = self._request_token(client_id, client_secret, None)
        _check(resp, "Token request")
        token = resp.json()["access_token"]
        # No session-wide Content-Type: the API rejects it on body-less requests.
        self.session.headers["Authorization"] = f"Bearer {token}"

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        for attempt in range(1, MAX_RETRIES + 1):
            resp = self.session.request(method, self.base_url + path,
                                        timeout=self.timeout, **kwargs)
            if resp.status_code != 429 or attempt == MAX_RETRIES:
                return resp
            time.sleep(2 * attempt)
        raise AssertionError("unreachable")

    def get_sites(self) -> list[Site]:
        resp = self._request("GET", SITES_PATH)
        _check(resp, "List sites")
        sites = []
        for s in resp.json().get("sites", []):
            account_id = s.get("account_id") or s.get("nebula_account_id")
            if account_id:
                name = s.get("company_name") or s.get("name") or account_id
                sites.append(Site(name=name, account_id=account_id))
        return sorted(sites, key=lambda s: s.name.lower())

    def get_endpoints(self, account_id: str) -> list[Endpoint]:
        endpoints: list[Endpoint] = []
        cursor = ""
        while True:
            body = {"account_ids": [account_id], "page_size": PAGE_SIZE,
                    "next_cursor": cursor}
            resp = self._request("POST", ENDPOINTS_SEARCH_PATH, json=body)
            _check(resp, "List endpoints")
            data = resp.json()
            if "endpoints" not in data:
                raise ApiError(f"List endpoints: unexpected response: {resp.text[:500]}")
            endpoints.extend(data["endpoints"])
            cursor = data.get("next_cursor") or ""
            if not cursor:
                return endpoints

    def delete_endpoint(self, account_id: str, endpoint_id: str,
                        unregister_isolated: bool = False) -> None:
        path = ENDPOINT_DELETE_PATH.format(account_id=account_id,
                                           endpoint_id=endpoint_id)
        resp = self._request("DELETE", path,
                             json={"unregister_isolated": unregister_isolated})
        _check(resp, "Delete endpoint")


def _sources(ep: Endpoint) -> list[dict[str, Any]]:
    return [ep] + [v for v in ep.values() if isinstance(v, dict)]


def endpoint_names(ep: Endpoint) -> set[str]:
    names = set()
    for src in _sources(ep):
        for key in NAME_KEYS:
            val = src.get(key)
            if isinstance(val, str) and val.strip():
                v = val.strip().lower()
                names.add(v)
                names.add(v.split(".")[0])
    return names


def endpoint_id(ep: Endpoint) -> str | None:
    for src in (ep, ep.get("machine"), ep.get("agent")):
        if isinstance(src, dict):
            value = src.get("id") or src.get("machine_id")
            if value:
                return str(value)
    return None


def endpoint_label(ep: Endpoint) -> str:
    for src in _sources(ep):
        for key in NAME_KEYS:
            if src.get(key):
                return str(src[key])
    return str(endpoint_id(ep))


def parse_names(text: str) -> list[str]:
    for sep in ",;\t":
        text = text.replace(sep, " ")
    seen: set[str] = set()
    out = []
    for token in text.split():
        if token.lower() not in seen:
            seen.add(token.lower())
            out.append(token)
    return out


def match_names(names: Iterable[str], endpoints: Iterable[Endpoint]) -> MatchResult:
    index: dict[str, list[Endpoint]] = {}
    for ep in endpoints:
        for n in endpoint_names(ep):
            index.setdefault(n, []).append(ep)

    result = MatchResult([], [], [])
    for name in names:
        unique = {eid: ep for ep in index.get(name.lower(), [])
                  if (eid := endpoint_id(ep))}
        if len(unique) == 1:
            result.matched.append((name, next(iter(unique.values()))))
        elif unique:
            result.ambiguous.append((name, list(unique.values())))
        else:
            result.not_found.append(name)
    return result
