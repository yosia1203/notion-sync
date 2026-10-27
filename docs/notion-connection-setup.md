# Create your own Notion connection

Everyone who uses this tool creates a Notion connection of their own. Nothing is shared: your connection has its own client ID and its own client secret, and only you hold them. You do this once.

A connection is the app that you sign in through. Notion gives it a **client ID** and a **client secret** when you create it. The tool uses them once, to sign you in and to create your personal token. After that, the token is all it needs.

This guide follows Notion's developer documentation. Where the documentation says nothing, this guide says so instead of guessing. Notion renames menus from time to time, so if a label differs, trust the screen.

## Contents

- [What you end up with](#what-you-end-up-with)
- [Before you start](#before-you-start)
- [Create the connection](#create-the-connection)
- [Copy the client ID and secret](#copy-the-client-id-and-secret)
- [Store them](#store-them)
- [Sign in](#sign-in)
- [Add the connection to your pages](#add-the-connection-to-your-pages)
- [If something goes wrong](#if-something-goes-wrong)
- [What the documentation does not say](#what-the-documentation-does-not-say)

## What you end up with

| Thing | What it is | Where it lives |
|---|---|---|
| Client ID | The public name of your connection. Not a secret. | Your shell profile, as `NOTION_CLIENT_ID`. |
| Client secret | The connection's password. | Your password manager. Typed once at a prompt. |
| Token | Your personal key to Notion. Created when you sign in. | `~/.config/notion-sync/notion_token.json`, written by the tool. |

## Before you start

- A Notion account in the workspace that holds the pages you will publish to.
- Permission to create connections there. Notion's help page says only workspace owners can create them, though members have created them in practice. If Notion will not let you, ask a workspace owner. Do not ask them to share their secret.

## Create the connection

1. Sign in to Notion and open the Developer portal's connections page: [app.notion.com/developers/connections](https://app.notion.com/developers/connections).
2. Create a new **public connection**. The tool signs in with OAuth, which is what a public connection provides. If Notion tells you to create an internal connection first and convert it, do that.
3. Under the **OAuth configuration** section, set the **Redirect URI** to exactly `http://localhost`. Do not add a port or a trailing slash. The tool uses this address. Notion sends you back to it after you approve, and the browser then fails to load it. That is expected: the one-time code is in the address bar.
4. Set the **Installation scope**. The choices are "Any workspace" and "Selected workspaces only". Choose **Selected workspaces only** and select the workspace that holds your documents. Notion states that this cannot be changed after creation.
5. Fill in whatever else the form asks for. A name that says whose it is, such as `notion-sync <your name>`, helps if someone else looks at the list.
6. Save or submit the connection. Notion says a connection's **Authorization URL** field appears once it is submitted for review. The tool builds its own sign-in link, so you do not need that field. If Notion makes you wait for an approval before you can sign in, tell the person who set this tool up.

## Copy the client ID and secret

1. Open your connection on the connections page.
2. Open its **Configuration** tab. The client ID and the client secret are listed there.
3. Copy both. Save the secret in your password manager now. The documentation does not say whether Notion shows it again later, so do not rely on it.

## Store them

**The client ID** is not a secret. Put it in your shell profile so the tool never has to ask. For zsh:

```bash
echo 'export NOTION_CLIENT_ID="<your client ID>"' >> ~/.zshrc
```

Open a new terminal afterwards. For bash, use `~/.bashrc`. If you skip this, the tool asks for the ID each time you sign in.

**The client secret** goes in your password manager and nowhere else. When the tool asks for it, you paste it at a prompt that does not echo. The tool never writes it to disk.

- Do not put it in `~/.zshrc`, in a repository, in a note, in chat, or in a message to Claude.
- Do not type it inline, as in `NOTION_CLIENT_SECRET=... python3 ...`. That line is saved in your shell history.
- You need it only when you sign in. After that the token is what the tool uses. You will need it again to sign in on a new machine or after deleting the token.

**The token** is saved for you, with owner-only permissions, outside any project folder. Treat it like a password: do not share it or commit it.

## Sign in

From the tool's folder, print your sign-in link:

```bash
cd ~/dev/notion-sync
python3 scripts/notion_auth.py url
```

Open the link. Notion shows a consent screen. Approve it, and select the pages or database you will publish to. The browser then fails to load `http://localhost/?code=...`. Copy the whole address and run:

```bash
python3 scripts/notion_auth.py exchange '<the address you copied>'
```

Paste the client secret at the prompt. When it works you see `Token stored at ...` and the name of your workspace.

## Add the connection to your pages

The tool can read and write only pages your connection has been added to. For each page you publish to:

1. Open the page in Notion.
2. Open the page's **⋯** menu and choose **Connections**.
3. Add your connection.

You can do this only on pages you have full access to. A page you selected on the consent screen already has it. Without this step every command fails with a 404.

## If something goes wrong

| Symptom | Likely cause |
|---|---|
| Notion refuses the sign-in link | The redirect URI is not exactly `http://localhost`, or the client ID is wrong. |
| `invalid_grant` on `exchange` | The one-time code was used or expired. Run `url` again and approve again. |
| `exchange` fails with an authentication error | The client secret does not belong to the client ID you used. Check that both come from the same connection. |
| `manual` returns 404 | The connection was not added to the page. See [above](#add-the-connection-to-your-pages). |
| The consent screen shows no pages to select | You need full access to the page in Notion first. |

## What the documentation does not say

Notion's documentation does not state:

- how to regenerate or rotate the client secret. If yours leaks, look for a regenerate option on the Configuration tab. If there is none, create a new connection and sign in again.
- whether a connection must be approved before you can sign in through it.
- which fields the creation form requires.
- whether the secret can be viewed again after you first copy it.
- who in a workspace may create a connection. The help page and what people have seen in practice disagree.

If you find the answers, add them here.
