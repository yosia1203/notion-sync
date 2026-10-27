# Set up the Notion connection

This guide is for the person who owns the team's Notion connection. You do it once. Teammates never need it: they follow the [README](../README.md#set-up) and ask you for the client secret.

A connection is the app that people sign in through. Notion issues it a **client ID** and a **client secret** when it is created. This tool uses OAuth, so each teammate signs in through the connection and gets a personal token of their own.

Notion's documentation is the source for the steps below. Where the documentation is silent, this guide says so instead of guessing. Notion renames menus from time to time, so if a label differs, trust the screen.

## Contents

- [Check whether you already have one](#check-whether-you-already-have-one)
- [Create the connection](#create-the-connection)
- [Find the client ID and secret](#find-the-client-id-and-secret)
- [Point the tool at the connection](#point-the-tool-at-the-connection)
- [Share the secret with the team](#share-the-secret-with-the-team)
- [Test with one teammate](#test-with-one-teammate)
- [What the documentation does not say](#what-the-documentation-does-not-say)

## Check whether you already have one

1. Sign in to Notion and open the Developer portal's connections page: [app.notion.com/developers/connections](https://app.notion.com/developers/connections).
2. If the team's connection is listed, skip to [Find the client ID and secret](#find-the-client-id-and-secret).
3. If this tool already works for you, a connection exists. Its client ID is the `CLIENT_ID` value in `scripts/notion_auth.py`. Match it against the IDs on the Configuration tab to find the right one.

## Create the connection

Skip this if the connection exists.

1. On the connections page, create a new **public connection**. The tool signs people in with OAuth, which is what a public connection provides.
2. Under the **OAuth configuration** section, set the **Redirect URI** to exactly `http://localhost`. The tool uses that address. Notion sends people back to it with a `code` in the address bar, and the browser then fails to load it. That is expected.
3. Set the **Installation scope**. The choices are "Any workspace" and "Selected workspaces only". Notion states that this cannot be changed after creation. Pick the narrowest scope that covers the workspaces your teammates sign in from, since you cannot widen or narrow it later.
4. Fill in whatever else the form requires, and submit the connection.

Notion says the connection's **Authorization URL** field appears once it is submitted for review. This tool builds its own sign-in link from the client ID, so you do not need that field. Whether a connection must be approved before teammates can use it is not stated in the documentation, so the [test below](#test-with-one-teammate) is the real check.

## Find the client ID and secret

1. Open the connection on the connections page.
2. Open its **Configuration** tab. The client ID and the client secret are listed there.
3. Copy the secret when you need to hand it to someone. Notion keeps it on this page, so you do not need to store a copy of your own beyond the password manager.

Notion's guidance is to keep these out of source code and version control. Treat the secret like a password.

## Point the tool at the connection

The client ID is built into the tool. If you created a new connection, or the IDs differ, open `scripts/notion_auth.py` and replace the `CLIENT_ID` value with the one from the Configuration tab. Commit and push the change so teammates get it with `git pull`. The client ID is not a secret.

The token request sends the ID and the secret together as HTTP Basic authentication, which is why the tool asks for the secret and nothing else.

## Share the secret with the team

- Put the secret in your team password manager and share it with each teammate individually.
- Do not put it in the repository, in chat, in a ticket, or in an email.
- Tell them it is for one terminal prompt and does not need saving.

## Test with one teammate

Before you announce the tool, have one teammate follow the [README setup](../README.md#set-up) from the start. It passes when they:

1. Open the sign-in link and see Notion's consent screen. It asks them to choose which pages the connection may access.
2. Finish `exchange` and see `Token stored at ...` and your workspace name.
3. Run `python3 scripts/notion_publish.py manual <doc>` on a document with a `published_url` and get a list of regions.

Only the person who authorizes can use the pages they shared, and a person can share only pages they have full access to. If a teammate cannot see a page in the picker, they lack full access to it.

| Symptom | Likely cause |
|---|---|
| Notion refuses the sign-in link | The teammate's workspace is outside the connection's installation scope, or the redirect URI is not exactly `http://localhost`. |
| `invalid_grant` on `exchange` | The one-time code was used or expired. Run `url` again. |
| Authorized, but `manual` returns 404 | The page was not shared with the connection during sign-in. Sign in again and select it. |
| Teammate sees no pages to select | They need full access to the page in Notion first. |

## What the documentation does not say

Notion's authorization guide does not state:

- how to regenerate or rotate the client secret. If the secret leaks, look for a regenerate option on the Configuration tab and tell the team. If there is none, you would have to create a new connection and update `CLIENT_ID`.
- whether a connection must be approved before other users can authorize it.
- who in a workspace is allowed to create a public connection.
- which required fields the creation form has.

If you find the answers, add them here.
