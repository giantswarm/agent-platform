"""Headless authorization flow for ATS."""

from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


class OAuthForms(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self.current = {"action": attributes.get("action", ""),
                            "method": attributes.get("method", "get").lower(), "inputs": {}}
        elif tag == "input" and self.current is not None and attributes.get("name"):
            self.current["inputs"][attributes["name"]] = attributes.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form" and self.current is not None:
            self.forms.append(self.current)
            self.current = None


def origin(url):
    parsed = urlsplit(url)
    return parsed.scheme, parsed.netloc


def authorization_form(page, current_url, authorization_base, issuer_url, user, password):
    parser = OAuthForms()
    parser.feed(page)
    for form in parser.forms:
        if form["method"] != "post":
            continue
        action = urljoin(current_url, form["action"])
        fields = form["inputs"]
        if fields.get("consent_id"):
            expected = authorization_base.rstrip("/") + "/oauth/authorize"
            assert origin(current_url) == origin(expected) and action == expected, "unexpected consent endpoint"
            return action, {"consent_id": fields["consent_id"], "decision": "approve"}
        if "login" in fields and "password" in fields:
            assert origin(current_url) == origin(issuer_url) and origin(action) == origin(issuer_url), "unexpected password endpoint"
            return action, {"login": user, "password": password}
    raise AssertionError("OAuth page has no recognized consent or login form")


def follow_authorization(session, url, callback, authorization_base, issuer_url, user, password):
    response = None
    for _ in range(15):
        if urlsplit(url)._replace(query="", fragment="") == urlsplit(callback)._replace(query="", fragment=""):
            return url
        if response is None:
            response = session.get(url, allow_redirects=False, timeout=30)
        if response.status_code in (301, 302, 303, 307, 308):
            url = urljoin(url, response.headers["Location"])
            response = None
            continue
        assert response.status_code == 200, f"{url} -> {response.status_code}: {response.text[:300]}"
        post_url, fields = authorization_form(response.text, url, authorization_base, issuer_url, user, password)
        scheme, authority = origin(url)
        response = session.post(post_url, data=fields, headers={"Origin": f"{scheme}://{authority}"},
                                allow_redirects=False, timeout=30)
        url = post_url
    raise AssertionError(f"OAuth flow never reached the redirect URI: {url}")
