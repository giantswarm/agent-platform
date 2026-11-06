"""Forms accepted by the ATS OAuth browser driver."""

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
