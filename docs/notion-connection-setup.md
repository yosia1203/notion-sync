# Run the Notion connection

This guide is for the **connection owner**: the one person who creates the team's Notion connection, holds its client secret, and turns each teammate's approval into a token. Teammates follow the README's setup and never see the secret.

A connection is the app that people sign in through. Notion gives it a **client ID** and a **client secret** when you create it. The tool needs the secret at exactly one step, when a one-time code from a sign-in is traded for a token. Because only you hold the secret, you do that step for each teammate.

This guide follows Notion's developer documentation. Where the documentation says nothing, this guide says so instead of guessing. Notion renames menus from time to time, so if a label differs, trust the screen.

## Contents

- [What exists and who holds it](#what-exists-and-who-holds-it)
- [Create the connection](#create-the-connection)
- [Copy and store the client ID and secret](#copy-and-store-the-client-id-and-secret)
- [Sign yourself in](#sign-yourself-in)
- [Make a teammate's token](#make-a-teammates-token)
- [Rotate the secret](#rotate-the-secret)
- [Add the connection to pages](#add-the-connection-to-pages)
- [If something goes wrong](#if-something-goes-wrong)
- [What is not verified](#what-is-not-verified)

## What exists and who holds it

| Thing | What it is | Who holds it |
|---|---|---|
| Client ID | The public name of the connection. Not a secret. | Anyone. Give it to teammates. |
| Client secret | The connection's password. | You, in your password manager. |
| Token | A personal key to the pages one person approved. | That person, in `~/.config/notion-sync/notion_token.json`. |

## Create the connection

Do this once.

1. Sign in to Notion and open the Developer portal's connections page: [app.notion.com/developers/connections](https://app.notion.com/developers/connections).
2. Create a new **public connection**. The tool signs in with OAuth, which is what a public connection provides. If Notion tells you to create an internal connection first and convert it, do that.
3. Under the **OAuth configuration** section, set the **Redirect URI** to exactly `http://localhost`. Do not add a port or a trailing slash. Notion sends each person back to it after they approve, and the browser then fails to load it. That is expected: the one-time code is in the address bar.
4. Set the **Installation scope**. The choices are "Any workspace" and "Selected workspaces only". Choose **Selected workspaces only** and select the workspace that holds your documents. Notion states that this cannot be changed after creation.
5. Fill in whatever else the form asks for, and submit the connection.

## Copy and store the client ID and secret

1. Open your connection on the connections page and open its **Configuration** tab.
2. Copy the client ID and the client secret.
3. Save the secret in your password manager **now**. After creation the Configuration tab may offer only a rotate option and no way to view the secret again. Do not rely on seeing it later.

Keep the secret out of everywhere else: not in `~/.zshrc`, a repository, a note, a chat message, or a message to Claude. Do not type it inline, as in `NOTION_CLIENT_SECRET=... python3 ...`, because that line is kept in your shell history. The tool asks for it at a hidden prompt and never writes it to disk.

The client ID is not a secret. Keep it where you can paste it to teammates. For yourself, a shell profile entry saves typing:

```bash
echo 'export NOTION_CLIENT_ID="<client ID>"' >> ~/.zshrc
```

## Sign yourself in

You need a token of your own to publish. From the tool's folder:

```bash
cd ~/dev/notion-sync
python3 scripts/notion_auth.py url
```

Open the link, approve, and select your pages. Copy the whole address the browser fails to load (it starts with `http://localhost/?code=`), then run:

```bash
python3 scripts/notion_auth.py exchange '<the address you copied>'
```

Paste the secret at the prompt. You see `Token stored at ...` and the workspace name.

## Make a teammate's token

The teammate follows the README: they open the sign-in link, approve, and send you the address they copied.

1. Give them the link. Either send the output of `python3 scripts/notion_auth.py url`, or send the client ID and let them print it.
2. When their address arrives, trade it for a token in a **separate file**, so yours is not replaced:

   ```bash
   cd ~/dev/notion-sync
   python3 scripts/notion_auth.py exchange '<address from the teammate>' --out ~/teammate-token.json
   ```

   Quote the address, because it contains `&`. Paste the secret at the prompt. Without `--out`, the command overwrites your own token.
3. Send the file to the teammate through a private channel, then delete your copy and the message. The token gives access to the pages they approved.

A code is single-use and expires quickly. Run `exchange` as soon as the address arrives. If it fails with `invalid_grant`, ask the teammate to approve again.

## Rotate the secret

Rotate if the secret leaked, or if it was lost and you need a new one. Notion warns that rotating revokes the connection's existing tokens, so every person, you included, signs in again with the steps above. Save the new secret in your password manager before you close the page.

## Add the connection to pages

The tool can read and write only pages the connection has been added to. For each page:

1. Open the page in Notion.
2. Open the page's **⋯** menu and choose **Connections**.
3. Add the connection.

A person can do this only on pages they have full access to. A page selected on the consent screen already has it. Without this step every command fails with a 404.

## If something goes wrong

| Symptom | Likely cause |
|---|---|
| Notion refuses the sign-in link | The redirect URI is not exactly `http://localhost`, or the client ID is wrong. |
| `invalid_grant` on `exchange` | The code was used or expired. The teammate approves again. |
| `exchange` fails with an authentication error | The secret does not belong to the client ID, or it was rotated. |
| A teammate's `manual` returns 404 | The connection was not added to the page. See [above](#add-the-connection-to-pages). |
| The consent screen shows no pages to select | The teammate needs full access to the page in Notion first. |

## What is not verified

Neither Notion's documentation nor testing here settles these. If you find the answers, add them.

- whether a workspace guest can approve the connection
- whether a token expires, and whether the tool needs the secret again to renew it. The token file includes a refresh token, but the tool does not use it.
- how to view the secret again after creation
- whether Notion records edits under the connection or under the person who approved
- whether a connection must be approved before people can sign in through it
