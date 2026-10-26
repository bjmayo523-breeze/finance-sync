import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def payload():
    return json.loads((FIXTURES / "simplefin_accounts.json").read_text())


class FakeResponse:
    def __init__(self, status=200, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, get=None, post=None):
        self._get, self._post = get, post
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self._get(url, **kw)

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self._post(url, **kw)
