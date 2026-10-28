---
name: notion-sync
description: Use when the user wants to set up the Notion sync, check that it is connected, publish or verify a markdown document against its Notion page, read or answer comments on a page, or understand why a sync was refused. It operates the notion-sync scripts. For writing a document so it converts cleanly, also read the writing-for-notion skill.
---

# Operate the Notion sync

This skill runs the `notion-sync` scripts for the user. They sync specific local markdown files to their Notion pages, one way and one section at a time. The scripts live in a separate `notion-sync` checkout, not next to the user's documents. The documents need not be in a git repository.

Publishing changes a page other people read and comment on. Treat each write as outward-facing: show what will change and get a clear yes first.

## Find the scripts

1. Ask the user where their `notion-sync` checkout is, unless you already know. A common place is `~/dev/notion-sync`.
2. Confirm it: `ls <checkout>/scripts/notion_publish.py`.
3. Run every command as `python3 <checkout>/scripts/notion_publish.py <command> <doc> ...`. The document path is relative to the current directory or absolute. It does not need to be inside the checkout.

The checkout's `README.md` is the full reference. Read it when a command or error is unfamiliar.

## Check the connection

The token is `~/.config/notion-sync/notion_token.json`.

- Check that it exists with `test -f ~/.config/notion-sync/notion_token.json`. Never print, read aloud, or copy the file's contents.
- If it is missing, walk the user through setup. The team shares one Notion connection, owned by the connection owner, who alone holds the client secret. A teammate gets a token by relay: they approve the owner's sign-in link, the owner trades the approval for a token, and the owner sends the file back. The checkout's `docs/notion-connection-setup.md` is the owner's guide. The user runs the commands, because the client secret and the token must never pass through this conversation:
  1. Teammate: open the owner's sign-in link, or print it with `NOTION_CLIENT_ID=<ID from the owner> python3 <checkout>/scripts/notion_auth.py url`. The ID is not a secret.
  2. Teammate: approve, and select the pages to publish to. The browser then fails to load `http://localhost/?code=...`. That is expected. They copy the whole address and send it to the owner privately. They do not run `exchange`: they do not have the secret.
  3. Owner: `python3 <checkout>/scripts/notion_auth.py exchange '<address>' --out ~/teammate-token.json`, pasting the secret at the hidden prompt. Without `--out` it replaces the owner's own token. The owner sends the file privately.
  4. Teammate: save it as `~/.config/notion-sync/notion_token.json` and run `chmod 600` on it.
- Never ask the user to paste the client secret, the token, or a password into chat. If they offer it, tell them not to and point at the prompt in their own terminal.
- A code is single-use and short-lived. After `invalid_grant`, the teammate approves again and the owner exchanges the fresh address at once.

Prove the connection works with a read-only command on a document that has a page: `manual <doc>`. A classified list of regions means it works.

## Sync a document

Work one region at a time. Do not batch a whole document into one write.

1. **Preconditions.** Open the document. It must have `published_url` in its frontmatter. If not, stop: the page has to be created first (Notion import), and the user records its address. Do not create pages through the script. The shared connection must also have been added to the page (the page's **⋯** menu, **Connections**). Notion hides a page from a connection that was never added, so a 404 almost always means this. Tell the user to add it; you cannot do it for them.
2. **Inspect.** Run `verify <doc>` and `manual <doc>`. Both are read-only. Report which regions differ, which are manual, and which are blocked.
3. **Settle the direction.** For an authored document the local file wins. For one marked `imported: true` the page wins: differences in wording are not to be overwritten. If the file and the page disagree on content and it is not obvious which is right, ask the user. Do not choose the newer one.
4. **Plan.** For each region that differs, run `plan <doc> "<heading>"` and show the user what it would create and delete.
5. **Apply.** After the user agrees, run `apply <doc> "<heading>"`. Do the regions one by one.
6. **Verify.** Run `verify <doc>` again. A nonzero exit means the sync is not finished. Say so plainly.
7. **Close with the manual check.** List the sections marked `<!-- manual -->`, say what changed in the document that might affect them, and ask the user whether they have updated them. The script never writes those sections.

Report the paths and regions you wrote, the `verify` result, and the page title separately. The script cannot write the page title, so a rename always needs the user's hand in Notion.

## Rules that do not bend

- **Do not pass `--allow-flatten`, `--allow-link-loss` or `--allow-comment-edit` on your own.** Each one accepts a loss. Run without it, read the refusal, tell the user what would be lost, and use the flag only on their explicit yes for that write.
- **`--allow-comment-edit` deserves extra care.** A thread on a highlighted phrase leaves the page when its block is rewritten and cannot be read back. Say this before the user agrees.
- **Do not write reviewer properties** (status, reviewers, review date and similar). The script does not, and you must not work around it through another tool.
- **Do not edit frontmatter** except to record `published_url` when the user gives you the address.
- **Do not rewrite imported prose.** Record rendering defects in the document's own notes, not in the text.
- **Do not delete a section from a page** (`drop`) or insert one (`insert`) without showing the preview and getting a yes. Both preview first and need `--yes` to run.
- **Do not delete or resolve comments.** The API cannot resolve them. The user does that in Notion.
- **A refusal is information.** Messages start with `REFUSING:` or `WRITE OUTCOME UNKNOWN:`. Read them. After an unknown outcome, run `verify` before any retry.

## Read and answer comments

- `comments <doc>` lists open threads under the heading they sit on and marks those waiting on the user. It is slow on a long page (about four minutes for 195 blocks). Say so before you run it.
- `reply <doc> "<discussion id>" "<text>"` previews. Add `--yes` only after the user approves the exact wording. Replies are posted under the user's name.

## Writing a document for Notion

When the task is to write or revise the document itself, read the `writing-for-notion` skill and the checkout's README section on conventions. Use only the supported markdown. Anything unsupported belongs in a `<!-- manual -->` section, never imitated with HTML.

## When something goes wrong

- Look the error up in the README's troubleshooting table first.
- `REFUSING: ambiguous ... heading`: qualify with the parent, `"## Parent > ### Details"`.
- Unresolved link: the linked document has no `published_url`. Ask the user whether to publish that page first or remove the link.
- A write stopped halfway: run `verify`, then `plan` on the affected region. Repeating `apply` is safe because unchanged blocks are left alone.
