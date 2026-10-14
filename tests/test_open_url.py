import dataclasses
import json
import webbrowser
from types import SimpleNamespace

import pytest

from nova.agent import Agent
from nova.tools import web
from nova.tools.registry import Registry

SEARCH = "https://www.youtube.com/results?search_query=death+bed+powfu"


@pytest.fixture
def opened(monkeypatch):
    """Every link handed to the browser. Nothing really opens."""
    links = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: links.append(url) or True)
    return links


@pytest.fixture
def registry(cfg):
    registry = Registry()
    web.register(registry, cfg)
    return registry


def open_url(registry, url):
    return registry.invoke("open_url", {"url": url})


def test_opens_the_page_in_the_browser(registry, opened):
    out = open_url(registry, SEARCH)
    assert opened == [SEARCH]
    assert SEARCH in out and not out.startswith("ERROR")


def test_address_without_a_scheme_opens_over_https(registry, opened):
    open_url(registry, "youtube.com/results?search_query=powfu")
    assert opened == ["https://youtube.com/results?search_query=powfu"]


def test_spaces_and_accents_are_encoded_for_the_browser(registry, opened):
    open_url(registry, "https://www.youtube.com/results?search_query=death bed café")
    assert opened == ["https://www.youtube.com/results?search_query=death%20bed%20caf%C3%A9"]


def test_an_encoded_link_is_left_alone(registry, opened):
    open_url(registry, SEARCH + "%28feat.%29")
    assert opened == [SEARCH + "%28feat.%29"]


@pytest.mark.parametrize(
    "link", ["file:///etc/passwd", "ftp://example.com/notes.txt", "javascript:alert(1)"]
)
def test_only_web_pages_reach_the_browser(registry, opened, link):
    open_url(registry, SEARCH)
    open_url(registry, link)
    assert opened[:1] == [SEARCH]
    assert all(url.startswith(("http://", "https://")) for url in opened)


def test_says_so_when_no_browser_opens(registry, monkeypatch):
    tried = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: tried.append(url) and False)
    out = open_url(registry, SEARCH)
    assert tried == [SEARCH]
    assert out.startswith("ERROR")


class OneCallLLM:
    """Asks for a single open_url call, then answers."""

    def __init__(self, url):
        call = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(name="open_url", arguments=json.dumps({"url": url})),
        )
        self.replies = [
            SimpleNamespace(content="", tool_calls=[call]),
            SimpleNamespace(content="Done.", tool_calls=None),
        ]

    def chat(self, messages, tools, **kwargs):
        return self.replies.pop(0)


class Decline:
    def __init__(self):
        self.asked = []

    def confirm(self, tool_name, description):
        self.asked.append((tool_name, description))
        return False


def test_asks_first_and_a_no_keeps_the_browser_closed(cfg, registry, opened):
    approver = Decline()
    agent = Agent(
        cfg=dataclasses.replace(cfg, confirm_dangerous=True),
        llm=OneCallLLM(SEARCH),
        registry=registry,
        approver=approver,
        stream=False,
    )
    agent.run("search it up on youtube")
    assert opened == []
    assert [name for name, _ in approver.asked] == ["open_url"]
    assert SEARCH in approver.asked[0][1]
