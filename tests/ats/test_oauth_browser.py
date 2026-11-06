import unittest

from oauth_browser import authorization_form


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


if __name__ == "__main__":
    unittest.main()
