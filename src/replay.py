import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


class ReplaySession(requests.Session):
    """Serves recorded GET responses by URL path and refuses every other request.

    Used by `main.py --replay` to run a paper cycle with no network at all.
    """

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.responses: dict[str, Any] = json.loads(path.read_text())

    def request(  # type: ignore[override]
        self, method: str, url: str, *args: Any, **kwargs: Any
    ) -> requests.Response:
        route = urlparse(url).path.split("/trade-api/v2", 1)[-1]
        if method != "GET" or route not in self.responses:
            raise RuntimeError(f"replay has no recorded response for {method} {route}")
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(self.responses[route]).encode()
        return response
