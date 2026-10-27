"""Tests for notion_auth.py: where the client ID comes from, and what `url` prints.

    python3 scripts/test_notion_auth.py

Nothing here touches the network or needs a token.
"""

import contextlib
import io
import os
import sys
import unittest
import urllib.parse
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import notion_auth as auth


class ClientId(unittest.TestCase):
    def test_environment_wins_and_is_trimmed(self):
        with patch.dict(os.environ, {"NOTION_CLIENT_ID": "  abc-123 "}), patch("builtins.input") as ask:
            self.assertEqual(auth.client_id(), "abc-123")
        ask.assert_not_called()

    def test_prompts_when_unset(self):
        with patch.dict(os.environ, {}, clear=True), patch("builtins.input", return_value=" typed-id "):
            self.assertEqual(auth.client_id(), "typed-id")

    def test_empty_answer_stops_the_run(self):
        with patch.dict(os.environ, {}, clear=True), patch("builtins.input", return_value=""):
            with self.assertRaises(SystemExit):
                auth.client_id()

    def test_there_is_no_built_in_id(self):
        self.assertFalse(hasattr(auth, "CLIENT_ID"))


class UrlCommand(unittest.TestCase):
    def test_link_carries_this_persons_id_and_the_bare_localhost_redirect(self):
        out = io.StringIO()
        with patch.dict(os.environ, {"NOTION_CLIENT_ID": "my-own-id"}), contextlib.redirect_stdout(out):
            auth.cmd_url()
        link = next(l for l in out.getvalue().splitlines() if l.startswith(auth.AUTHORIZE))
        q = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
        self.assertEqual(q["client_id"], ["my-own-id"])
        self.assertEqual(q["redirect_uri"], ["http://localhost"])
        self.assertEqual(q["owner"], ["user"])


if __name__ == "__main__":
    unittest.main()
