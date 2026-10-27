# Publishing to Notion

This procedure maintains existing Notion pages from repository documents. Read the authority rules first, inspect the full document, then publish only the regions whose differences have been settled. The script provides section replacement, not an atomic whole-page transaction.

## Decide which content may change

An authored document owns its publishable body. An imported document, marked `imported: true`, preserves its authors' prose: wording differences resolve toward the page. A later repository decision can publish outward only when the owner has settled that exception. The converter's imported flag permits literal unsupported syntax; it does not establish authority to overwrite imported prose.

Local blocks and Obsidian comments never publish. A heading marked `<!-- manual -->` gives the page ownership of that section and its descendants. Verification reports those differences separately. A nested page or database is another document and remains outside the replacement.

Frontmatter never publishes. Reviewer properties never change. The script does not currently check or update the page's title property: a body heading update is a different operation. A requested page rename needs the hand procedure below.

### Rename a page by hand

The page title is the one property this workflow owns and the one the script cannot write. `verify` says so in its closing line, so a rename that stops at the body heading passes every check while the page keeps its old name.

1. Rename the document here: the frontmatter `title`, the body title heading, and the filename if the slug changed. Keep the filing code out of both titles.
2. Publish the body title with `apply <doc> @preamble`, which carries the heading, or with `apply <doc> "<title heading>"` where the title is its own region.
3. Open the page and edit the title property to the same words. Nothing in this repository can do that step.
4. Report the title separately from the `verify` result, naming the old and new titles, because no command will show that it happened.

## Check the connection and run the tests

The scripts live in the notion-sync checkout, so run `scripts/...` commands from that directory or give the full path to the script. The document argument can be any path. The token is stored at `~/.config/compendium/notion_token.json`. If it is missing, run:

```bash
python3 scripts/notion_auth.py url
python3 scripts/notion_auth.py exchange '<the http://localhost/?code=... URL>'
```

The redirect is `http://localhost`. A browser error there is expected; copy the address containing the authorization code. The client secret comes from `NOTION_CLIENT_SECRET` or a prompt. Never print or commit the token.

Before maintaining the tooling, run the suites:

```bash
python3 scripts/test_notion_publish.py
python3 -m unittest discover -s scripts -p 'test_*.py'
```

Discover the whole directory. The command tests and the converter's toggle tests live beside each other, so naming one file runs a fraction of them. Run the first command on its own as well: the parser file reports each of its checks by name, while discovery collects them as a single case and prints only the first failure's detail.

The command tests use an in-memory API with pagination, ordered children, rejected chunks, read-back failures, and concurrent edits. They do not prove that a real workspace accepts a write.

## Inspect the whole document

```bash
python3 scripts/notion_publish.py verify documentation/rfc/006-api-validation.md
python3 scripts/notion_publish.py manual documentation/rfc/006-api-validation.md
```

`verify` compares the preamble, every heading and the prose before its next heading. This includes the introduction of a parent section that also has subsections. It checks managed heading order, unclaimed blocks, nested structure, formatting, table settings, and links. Reads follow pagination and are reused within one report.

A nonzero result means incomplete synchronization or a blocker. A zero result means the managed body matches the converter's supported representation. Manual sections and preserved child pages/databases are reported separately. Page properties, the page title property, comments, and edits made during the report are outside that result.

`verify <doc> --with-comments` also lists the page's open threads and names each one waiting on you. A page can match the repository while a reviewer still waits for an answer, so a sync that closes a review round runs this form. The threads never change the exit code. The flag adds the full comment scan described below, about four minutes on a 195-block page.

`manual` lists every region as matching, differing, missing, manual, or blocked. It is a read-only classification. Apply still needs to check comment access and removed links. `coverage` is an additional heading/preamble diagnostic, not a completion check.

A heading-order failure needs a separate repair. Applying a matching section does not move it. Inspect the exact sections involved and preserve comments, manual content, and nested documents when correcting their order; the command line has no general reorder operation.

An unresolved repository link is a failure even when its label matches plain text on the page. Establish the target's real `published_url` before calling the document synced. A link into `knowledge/` or `performance/` is not one of these: those domains carry no `published_url` field, so the link flattens to plain text and is not reported.

## Read and answer the page's comments

```bash
python3 scripts/notion_publish.py comments docs/my-document.md
python3 scripts/notion_publish.py reply <doc> "<discussion id>" "<answer>"
python3 scripts/notion_publish.py reply <doc> "<discussion id>" "<answer>" --yes
```

`comments` lists every open thread on the page under the heading it sits in, in page order, with each comment's author and date. A thread whose last comment is not yours is marked `[waiting on you]`, and the closing line counts them. That is the work list a review leaves behind.

The listing is open threads only, because the API does not return resolved ones. Two things follow. The list is work rather than history, and a resolved thread leaves it without anything being recorded here. A block also stops refusing writes once its thread is resolved, so resolving in Notion is the ordinary way to clear a paragraph for publishing.

The scan is slow and cannot be made fast. Notion answers comments for one block at a time, nothing in a block says whether it carries a thread, and there is no page-wide comment endpoint, so the command asks every block and every descendant in turn. The lint pipeline page, 195 top-level blocks, takes about four minutes. One comment can answer to more than one block: a thread on a table comes back for the table and again for each row. The first block to report a comment keeps it, so a table thread is listed once, against the table.

`reply` posts into an existing discussion. Without `--yes` it prints the thread and the answer and sends nothing. It checks that the discussion belongs to this document's page before posting, because a discussion id from another document is still a valid id, and posting to the wrong one puts your words under someone else's work. Both comment capabilities, read and insert, are off by default on a connection, and a call without them returns 403 rather than a validation error.

**Resolving a thread is not available**, and neither is starting one anchored to a phrase. Notion's comment guide states both: a connection cannot resolve comments, cannot retrieve resolved ones, and cannot anchor a new discussion to a selected range of text inside a block. The reference adds that a comment may be created on a whole page or a whole block, which this repository has not tried; `reply` only answers a discussion that already exists. Resolving stays a hand action in Notion.

| Operation | Available | Where it is used here |
|---|---|---|
| list open threads | yes, one block at a time | `comments` |
| reply into an existing thread | yes, with the insert capability | `reply` |
| comment on a whole page or block | yes, per the reference, untried here | nothing |
| anchor a new thread to a phrase | no | nothing |
| read a resolved thread | no | nothing |
| resolve or unresolve | no | done by hand in Notion |

### Publishing under a thread

A thread belongs to one block. A reviewer starts it in one of two ways: on the whole block, or on a phrase highlighted inside it. Most review comments are on a phrase. What `apply` does to the block, and which kind of thread it carries, decides what happens to the thread.

| What the write does to the commented block | Thread on the whole block | Thread on a phrase |
|---|---|---|
| leaves it unchanged | stays; the block is not checked | stays; the block is not checked |
| rewrites it, same block type | stays, because the block keeps its ID | leaves the page: still open, but shown nowhere and returned by no listing |
| deletes it, or changes its type | deleted with the block | deleted with the block |

A phrase thread leaves the page on any rewrite of its block, even when the highlighted phrase is still in the new text. The API returns a block's text without its comment highlights, so the rewrite cannot carry them. The API also returns both kinds of thread in the same shape, so the script cannot tell which kind a block carries.

A commented block therefore refuses any write by default. Before passing `--allow-comment-edit` to rewrite it, answer each thread with `reply`, and expect a phrase thread to disappear: a reviewer following up has to start a new one. Deleting a commented block has no flag; resolve the thread in Notion first, or keep the block in the source. A failed comment read refuses in every case, because an unreadable thread is an unknown one.

## Plan and apply a region

```bash
python3 scripts/notion_publish.py plan documentation/rfc/006-api-validation.md "## Scope"
python3 scripts/notion_publish.py apply documentation/rfc/006-api-validation.md "## Scope"
```

Use `@preamble` for prose between the body title and the first section. Use `--preamble` on a heading to replace only its introduction, ending at the first subsection at any depth.

Repeated subsection names require their parent path:

```bash
python3 scripts/notion_publish.py plan document.md "## Parent > ### Details"
python3 scripts/notion_publish.py apply document.md "## Parent > ### Details"
```

The selector must identify one repository heading and one page heading. An ambiguous bare name refuses. An existing toggle's content is written inside its heading, and a heading marked `<!-- toggle -->` inside a section is written as a toggle heading with its own section nested in it. Changing an existing heading between toggle and plain, or changing its depth, still refuses; repair the container on the page first. `insert` can create a new toggle section. Content nested more than two levels below a block refuses before any write, because Notion accepts two levels per request.

Plan prints the text diff and blockers without writing. Apply compares structured content. Unsupported nested structures, captions, mentions, and colours require a deliberate manual decision. Underline is converted from `==text==` and compared like any other annotation.

Apply works block by block. It matches the converted blocks to the live ones the way a text diff matches lines, then gives each block one of four outcomes:

| Outcome | When | What the block keeps |
|---|---|---|
| kept | the block is identical | everything; it is not written or read |
| rewritten in place | it matches a new block of the same type, table width and toggle setting, and its text differs | its ID and any whole-block thread; a phrase thread leaves the page |
| created | a new block matches nothing on the page | nothing; it is new |
| deleted | a live block matches nothing in the source | nothing; its threads go with it |

Identical blocks are matched first. Inside a run that differs, blocks of the same type are matched in order, so a paragraph split in two keeps its ID on the first half and creates the second. Children are matched the same way inside their parent: a list item that gains a child keeps its ID, and a table row added to a table of the same width is created inside the existing table. A heading-only change updates the heading without touching the body. `plan` prints the counts before anything is written.

Two changes still delete and recreate a block, because Notion cannot update the setting in place: a table whose column count changed, and a heading that gained or lost its toggle. The table, its rows and any thread on them are deleted, so a commented table refuses until the thread is resolved.

The guards read only the blocks the write touches. A rewritten block is checked for comments, links and unrepresentable content. A deleted block is checked with all its descendants. A kept block keeps everything it carries, so an unresolved comment on an untouched paragraph does not stop the section around it from publishing. A parent whose children change must itself be a structure the converter writes; a paragraph nested under a list item on the page refuses rather than being removed. A failed comment read refuses before writing. A protected page/database inside a deleted block cannot be deleted by `--allow-flatten`.

The write order keeps the old content on the page for as long as possible: create and read back the new blocks, rewrite the matched ones, check the doomed blocks again, then delete.

## Missing and obsolete sections

| Command | Effect |
|---|---|
| `insert <doc> "<heading>" "<before>"` | preview a missing section before an existing heading |
| `insert ... --yes` | create the missing section after passing the write guards |
| `drop <doc> "<heading>"` | preview removal of a section absent from the publishable repository body |
| `drop ... --yes` | remove that section after checking links, comments, and nested content |

`apply <doc> @preamble` can create a missing introduction at the start of the page, including when there is no body-title block. That request uses Notion API version `2026-03-11` with `position: {"type": "start"}`. Other requests retain the existing API version. The separate `insert` command still requires a preceding block; a missing section before every existing block needs a page repair.

| Flag | Boundary |
|---|---|
| `--preamble` | replace only the selected section's introduction |
| `--allow-flatten` | permit a reported lossy conversion; never permit deletion of a nested page/database |
| `--allow-link-loss` | permit reported unresolved or removed links for this write |
| `--allow-comment-edit` | rewrite a commented block in place; a whole-block thread stays and a phrase thread leaves the page; never permits deleting a commented block |
| `--with-comments` | `verify` also lists open threads; it does not change the exit code |
| `--yes` | execute the previewed insert or drop |

These flags do not make verification pass or transfer ownership of manual content. Use them only for an intended, reviewed change. Never treat an imported document's wording as disposable because a write can be forced.

## Known Notion limitations

These come from Notion's API, not from this repository's tooling. Each one is a boundary the procedure works around rather than a defect to fix here.

| Limitation | What it means here |
|---|---|
| a connection cannot resolve or reopen a thread | resolving is a hand action in Notion; `reply` can answer, never close |
| resolved threads are not returned | `comments` is a work list, not a review history; nothing records a resolved thread |
| a new thread cannot be anchored to a phrase | the tooling only answers threads that already exist |
| no page-wide comment endpoint; comments are read one block at a time | `comments`, `verify --with-comments` and every comment guard scan block by block, about four minutes for 195 top-level blocks |
| a block's text comes back without its comment highlights, and phrase and whole-block threads come back in the same shape | a rewrite drops every highlight in the block, so a phrase thread leaves the page and every listing while staying open, even when the phrase survives; tested on a scratch page on 2026-10-08 |
| comment read and insert are off by default on a connection | a missing capability returns 403; every guard treats that as a refusal |
| a table's column count cannot be updated | a column change deletes and recreates the table, with any thread on it |
| a heading's toggle setting cannot be updated | a toggle change refuses; repair the heading on the page first |
| one request accepts two levels of children | deeper content refuses before any write |
| one append takes at most 100 blocks and 500KB | larger runs go in chunks; a region over the size limit refuses |
| an append response may include existing blocks, or only its first page | created blocks are identified from full before-and-after reads |
| no transaction across requests | an interrupted write can leave duplicates or a partial deletion; see the recovery table below |

## Close every sync with a manual check

Manual sections are the part of a page no command writes, so a sync that ends at `verify` leaves them to memory. After the final `verify` of every sync, before reporting the sync as done:

1. List each region that `verify` reports under `MANUAL (page-owned)`, and each local block inside those sections that is meant for manual entry, such as drafted rows for a Notion database.
2. For each one, state what changed in the repository since the page was last synced, from the repository history since `last_synced` and any uncommitted changes. Say plainly when nothing changed.
3. Ask the user whether they have updated those parts of the page, and wait for the answer.
4. Report the answer for each part: updated, not yet updated, or no update needed. Record `last_synced` for the automated sections as usual, and state the manual status separately. Never describe a page as fully synced while a manual part is unconfirmed.

## Recover from an interrupted write

New blocks are created in chunks of at most 100, read back, and compared before old blocks are deleted. The script checks the old content again before deletion. Payload limits are checked before the create phase, including nested arrays and total request bytes.

Run writes to the same page sequentially. Append responses can include existing siblings or only the first response page. The tool identifies created blocks through complete before-and-after child reads and checks their position; it never treats every returned block as a new block eligible for rollback. Unexpected concurrent creations stop the write for inspection.

| Failure | State and next action |
|---|---|
| Rejected append chunk | known new blocks are rolled back; old blocks remain; inspect any reported rollback leftovers |
| Timeout or uncertain server result during append | no automatic replay; old blocks remain; inspect the reported IDs and page for duplicates |
| New content fails read-back | old blocks remain; inspect the new blocks before retrying |
| Old content changed during the write | no deletion; reconcile the concurrent edit and inspect new blocks |
| Delete fails partway through | new content remains alongside remaining old blocks; use the reported IDs to inspect and recover |

Rate-limit responses use `Retry-After` with bounded attempts. Transport retries are limited to idempotent reads/deletes; an append can have succeeded even when its response was lost.

The API has no transaction across these requests. A process interruption can leave duplicates or a partly completed deletion. No command should describe that state as unchanged or fully synced. Run `verify` after recovery and after the final apply. Record `last_synced` only after the relevant checks pass, with any manual/title checks stated separately.

## Keep the repair scripts in scope

`notion_sync.py` repairs whitespace in matching code blocks only. It now reads through the same local-content filter. Its `check` result is not document verification.


## References

- [Working with comments](https://developers.notion.com/docs/working-with-comments)
- [Notion request limits and retry policy](https://developers.notion.com/reference/request-limits)
- [Append block children](https://developers.notion.com/reference/patch-block-children)
- [Block objects](https://developers.notion.com/reference/block)
