import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call

from oauth_browser import authorization_form, follow_authorization


class AuthorizationFormTests(unittest.TestCase):
    def form(self, page, current="https://muster.example/tenant/oauth/authorize"):
        return authorization_form(page, current, "https://muster.example/tenant", "https://dex.example/dex", "user", "password")

    def test_consent_uses_only_transaction_and_decision(self):
        action, fields = self.form("""<form method='post' action='/tenant/oauth/authorize'>
            <input value='a&amp;b' name='consent_id'><input name='password' value='untrusted'>
            <button name='decision' value='approve'>Approve</button></form>""")
        self.assertEqual(action, "https://muster.example/tenant/oauth/authorize")
        self.assertEqual(fields, {"consent_id": "a&b", "decision": "approve"})

    def test_password_form_stays_at_identity_provider(self):
        action, fields = self.form("""<form action='/dex/auth/local?state=a&amp;b=c' method='POST'>
            <input name='login'><input name='password'></form>""", "https://dex.example/dex/auth/local")
        self.assertEqual(action, "https://dex.example/dex/auth/local?state=a&b=c")
        self.assertEqual(fields, {"login": "user", "password": "password"})

    def test_refuses_unknown_and_foreign_forms(self):
        for page in (
            "<form method='post' action='https://attacker.example'><input name='consent_id' value='id'></form>",
            "<form method='post' action='/oauth/authorize'><input name='consent_id' value='id'></form>",
            "<form method='get' action='/tenant/oauth/authorize'><input name='consent_id' value='id'></form>",
            "<form method='post' action='/login'><input name='login'><input name='password'></form>",
            "<form method='post' action='/tenant/oauth/authorize'><input name='unknown'></form>",
        ):
            with self.subTest(page=page), self.assertRaises(AssertionError):
                self.form(page)

    def test_refuses_foreign_page_with_legitimate_action(self):
        with self.assertRaises(AssertionError):
            self.form("""<form method='post' action='https://muster.example/tenant/oauth/authorize'>
                <input name='consent_id' value='id'></form>""", "https://attacker.example")

    def test_refuses_foreign_password_action(self):
        with self.assertRaisesRegex(AssertionError, "unexpected password endpoint"):
            self.form("""<form method="post" action="https://attacker.example/login">
                <input name="login"><input name="password"></form>""", "https://dex.example/dex/login")


class AuthorizationFlowTests(unittest.TestCase):
    issuer = "https://dex.example/dex"
    callback = "http://127.0.0.1:18763/callback"
    login_page = """<form method="post" action="/dex/login"><input name="login"><input name="password"></form>"""

    def response(self, status=200, page="", location=""):
        return SimpleNamespace(status_code=status, text=page, headers={"Location": location})

    def follow(self, session, initial, base="https://muster.example"):
        return follow_authorization(session, initial, self.callback, base, self.issuer, "user", "password")

    def test_consent_before_or_after_login_and_without_consent(self):
        for prefix in ("", "/tenant"):
            for consent_position in ("before", "after", "none"):
                with self.subTest(prefix=prefix, consent_position=consent_position):
                    base = "https://muster.example" + prefix
                    authorize = base + "/oauth/authorize"
                    initial = authorize + "?state=expected"
                    provider_callback = base + "/oauth/callback"
                    complete = self.callback + "?state=expected&code=authorization-code"
                    consent_page = f"""<form method="post" action="{authorize}">
                        <input name="consent_id" value="transaction-id"></form>"""
                    consent = self.response(page=consent_page)
                    login = self.response(page=self.login_page)
                    session = Mock()
                    get_login = call.get(self.issuer + "/login", allow_redirects=False, timeout=30)
                    post_login = call.post(self.issuer + "/login", data={"login": "user", "password": "password"},
                                           headers={"Origin": "https://dex.example"}, allow_redirects=False, timeout=30)
                    post_consent = call.post(authorize, data={"consent_id": "transaction-id", "decision": "approve"},
                                             headers={"Origin": "https://muster.example"}, allow_redirects=False, timeout=30)
                    get_callback = call.get(provider_callback, allow_redirects=False, timeout=30)
                    expected_calls = [call.get(initial, allow_redirects=False, timeout=30)]
                    if consent_position == "before":
                        session.get.side_effect = [consent, login, self.response(302, location=complete)]
                        session.post.side_effect = [self.response(303, location=self.issuer + "/login"),
                                                    self.response(302, location=provider_callback)]
                        expected_calls += [post_consent, get_login, post_login, get_callback]
                    elif consent_position == "after":
                        session.get.side_effect = [self.response(302, location=self.issuer + "/login"), login, consent]
                        session.post.side_effect = [self.response(302, location=provider_callback), self.response(303, location=complete)]
                        expected_calls += [get_login, post_login, get_callback, post_consent]
                    else:
                        session.get.side_effect = [self.response(302, location=self.issuer + "/login"), login,
                                                   self.response(302, location=complete)]
                        session.post.side_effect = [self.response(302, location=provider_callback)]
                        expected_calls += [get_login, post_login, get_callback]
                    self.assertEqual(self.follow(session, initial, base), complete)
                    self.assertEqual(session.mock_calls, expected_calls)

    def test_processes_page_returned_by_post_without_fetching_it_again(self):
        session = Mock()
        session.get.return_value = self.response(page=self.login_page)
        session.post.side_effect = [self.response(page=self.login_page), self.response(302, location=self.callback + "?code=code")]
        self.assertEqual(self.follow(session, self.issuer + "/login"), self.callback + "?code=code")
        session.get.assert_called_once_with(self.issuer + "/login", allow_redirects=False, timeout=30)
        self.assertEqual(session.post.call_count, 2)

    def test_callback_path_must_match_exactly(self):
        session = Mock()
        session.get.return_value = self.response(404)
        with self.assertRaisesRegex(AssertionError, "404"):
            self.follow(session, self.callback + "/unexpected?code=code")
        session.get.assert_called_once()

    def test_refuses_password_form_after_foreign_redirect(self):
        session = Mock()
        session.get.side_effect = [self.response(302, location="https://attacker.example/login"),
                                   self.response(page=self.login_page)]
        with self.assertRaisesRegex(AssertionError, "unexpected password endpoint"):
            self.follow(session, "https://muster.example/oauth/authorize")
        session.post.assert_not_called()

    def test_limits_redirect_loop(self):
        session = Mock()
        session.get.return_value = self.response(302, location="/oauth/authorize")
        with self.assertRaisesRegex(AssertionError, "never reached the redirect URI"):
            self.follow(session, "https://muster.example/oauth/authorize")
        self.assertEqual(session.get.call_count, 15)


if __name__ == "__main__":
    unittest.main()
