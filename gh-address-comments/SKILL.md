---
name: gh-address-comments
description: Use when addressing review comments or review threads on the open GitHub PR for the current branch and the exact comment text or full thread context matters for choosing and applying fixes.
metadata:
  short-description: Address comments in a GitHub PR review
---

# PR Comment Handler

Guide to find the open PR for the current branch, inspect its review threads with `gh`, and address the selected comments without losing thread context. Run all `gh` commands with elevated network access.

Prereq: ensure `gh` is authenticated (for example, run `gh auth login` once), then run `gh auth status` with escalated permissions (include workflow/repo scopes) so `gh` commands succeed. If sandboxing blocks `gh auth status`, rerun it with `sandbox_permissions=require_escalated`.

Important: `scripts/fetch_comments.py` is relative to this skill directory, not the repo under review. Invoke the script via the skill path you loaded, not from the target repo root.

## 1) Inspect comments needing attention
- Run the helper script from this skill directory.
- Prefer the human-readable format so you can see the exact comment wording and full thread transcript:
  - `python /absolute/path/to/gh-address-comments/scripts/fetch_comments.py --format markdown --unresolved-only`
- If you need everything, rerun without `--unresolved-only`.
- If the helper script is unavailable, fall back to `gh api graphql`, but still collect the exact comment text and full thread replies before summarizing anything.

## 2) Present threads to the user
- Number the review threads/comments you plan to discuss.
- For **every** numbered item, include:
  - file path + line (if present)
  - unresolved/resolved/outdated state
  - the **exact full text** of the comment, or the **full thread transcript** if there are replies
  - a short “likely fix” summary
- Do **not** reduce items to bare numbers with vague summaries.
- If the thread is long, you may quote the whole thread in a fenced block and then add a one-line summary below it.

Recommended format:

```markdown
2. `common/middleware.py:97` — unresolved
   Comment/thread:
   > The updated comment on lines 96-97 explains why exceptions aren't raised here...
   Likely fix: rewrite the inline comment so it explicitly says `_authenticate()` returns `None` and handling continues in `self.get_response(request)`.
```

## 3) If the user chooses comments
- Echo the selected item with its number **and** the file path + comment text/thread before making changes.
- Apply the fix.
- Verify the change with the relevant test/lint commands.

## 4) After finishing one comment, keep context in the next prompt
- When asking what to tackle next, restate the remaining options with context.
- Never say only: `If you want, I can move on to comment 2 now.`
- Instead say something like:
  - `Next remaining thread: 2. common/middleware.py:97 — "The updated comment on lines 96-97 explains why exceptions aren't raised here..."`
- If the user asked to see the full wording, paste the full comment or full thread again.
- If numbering changes after a refresh, explicitly show the refreshed numbered list before asking the user to choose.

## 5) Notes
- If `gh` hits auth/rate issues mid-run, prompt the user to re-authenticate with `gh auth login`, then retry.
- If you resolve multiple comments in one session, keep a short running status list: addressed, still open, and not yet investigated.
