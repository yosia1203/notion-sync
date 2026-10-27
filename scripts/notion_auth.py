#!/usr/bin/env python3
"""Obtain and store a Notion OAuth token for the documentation sync.

Two steps, run in order:

    python3 scripts/notion_auth.py url
    python3 scripts/notion_auth.py exchange '<the http://localhost/?code=... URL>'

The redirect URI registered on the connection is bare `http://localhost`, so the
browser lands on a page that cannot load. That is expected: the authorization code
is in the address bar, and this script takes it from there rather than running a
server on a privileged port.

Each person signs in through a Notion connection of their own. The connection's
client ID is read from NOTION_CLIENT_ID, or prompted for. It is not a secret.

The client secret is read from NOTION_CLIENT_SECRET, or prompted for without echo.
It is never written to disk. The access token is written outside the repository,
readable only by the owner.
"""

import base64
import getpass
import json
import os
import stat
import sys
import urllib.parse
import urllib.request

REDIRECT_URI = "http://localhost"
TOKEN_PATH = os.path.expanduser("~/.config/notion-sync/notion_token.json")
AUTHORIZE = "https://api.notion.com/v1/oauth/authorize"
TOKEN_URL = "https://api.notion.com/v1/oauth/token"


def client_id():
    """The ID of the signed-in person's own connection. It names the connection and
    is not a secret, so it may be typed, exported, or kept in a shell profile."""
    found = (os.environ.get("NOTION_CLIENT_ID") or "").strip()
    if not found:
        found = input("Notion client ID (from your connection's Configuration tab): ").strip()
    if not found:
        sys.exit("No client ID supplied. Set NOTION_CLIENT_ID or type it at the prompt.")
    return found


def cmd_url():
    q = urllib.parse.urlencode({
        "client_id": client_id(),
        "response_type": "code",
        "owner": "user",
        "redirect_uri": REDIRECT_URI,
    })
    print(f"\nOpen this, approve, and grant access to the pages or databases you will publish to:\n\n{AUTHORIZE}?{q}\n")
    print("The browser will then fail to load http://localhost/?code=...  That is expected.")
    print("Copy the whole address bar and pass it to:  notion_auth.py exchange '<url>'\n")


def extract_code(arg):
    if arg.startswith("http"):
        qs = urllib.parse.urlparse(arg).query
        code = urllib.parse.parse_qs(qs).get("code", [None])[0]
        if not code:
            sys.exit("No ?code= found in that URL. Paste the full redirected address.")
        return code
    return arg.strip()


def cmd_exchange(arg):
    code = extract_code(arg)
    secret = os.environ.get("NOTION_CLIENT_SECRET") or getpass.getpass("Notion client secret (not echoed): ")
    if not secret:
        sys.exit("No client secret supplied.")

    basic = base64.b64encode(f"{client_id()}:{secret}".encode()).decode()
    body = json.dumps({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }).encode()

    req = urllib.request.Request(TOKEN_URL, data=body, method="POST", headers={
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    })
    try:
        with urllib.request.urlopen(req) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        sys.exit(f"Token exchange failed ({e.code}).\n{detail}\n\n"
                 "A 400 with 'invalid_grant' usually means the code was already used or has expired: "
                 "codes are single-use and short-lived, so re-run 'url' and approve again.")

    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    with open(TOKEN_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    os.chmod(TOKEN_PATH, stat.S_IRUSR | stat.S_IWUSR)

    ws = payload.get("workspace_name")
    print(f"\nToken stored at {TOKEN_PATH} (owner-only).")
    print(f"Workspace: {ws}")
    print(f"Bot id:    {payload.get('bot_id')}")
    print("\nThe token itself is not printed. Nothing was written to the repository.\n")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("url", "exchange"):
        sys.exit(__doc__)
    if sys.argv[1] == "url":
        cmd_url()
    else:
        if len(sys.argv) < 3:
            sys.exit("Usage: notion_auth.py exchange '<redirected URL or code>'")
        cmd_exchange(sys.argv[2])
