#!/usr/bin/env python3
"""
Fetch all PR conversation comments + reviews + review threads (inline threads)
for the PR associated with the current git branch, by shelling out to:

  gh api graphql

Requires:
  - `gh auth login` already set up
  - current branch has an associated (open) PR

Usage:
  python fetch_comments.py > pr_comments.json
  python fetch_comments.py --format markdown
  python fetch_comments.py --format markdown --unresolved-only
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import textwrap
from typing import Any

QUERY = """\
query(
  $owner: String!,
  $repo: String!,
  $number: Int!,
  $commentsCursor: String,
  $reviewsCursor: String,
  $threadsCursor: String
) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      number
      url
      title
      state

      # Top-level "Conversation" comments (issue comments on the PR)
      comments(first: 100, after: $commentsCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          body
          url
          createdAt
          updatedAt
          author { login }
        }
      }

      # Review submissions (Approve / Request changes / Comment), with body if present
      reviews(first: 100, after: $reviewsCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          state
          body
          url
          submittedAt
          author { login }
        }
      }

      # Inline review threads (grouped), includes resolved state
      reviewThreads(first: 100, after: $threadsCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          diffSide
          startLine
          startDiffSide
          originalLine
          originalStartLine
          resolvedBy { login }
          comments(first: 100) {
            nodes {
              id
              body
              url
              createdAt
              updatedAt
              author { login }
            }
          }
        }
      }
    }
  }
}
"""


def _run(cmd: list[str], stdin: str | None = None) -> str:
    p = subprocess.run(cmd, input=stdin, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{p.stderr}")
    return p.stdout


def _run_json(cmd: list[str], stdin: str | None = None) -> dict[str, Any]:
    out = _run(cmd, stdin=stdin)
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse JSON from command output: {e}\nRaw:\n{out}") from e


def _ensure_gh_authenticated() -> None:
    try:
        _run(["gh", "auth", "status"])
    except RuntimeError:
        print("run `gh auth login` to authenticate the GitHub CLI", file=sys.stderr)
        raise RuntimeError("gh auth status failed; run `gh auth login` to authenticate the GitHub CLI") from None


def gh_pr_view_json(fields: str) -> dict[str, Any]:
    # fields is a comma-separated list like: "number,headRepositoryOwner,headRepository"
    return _run_json(["gh", "pr", "view", "--json", fields])


def get_current_pr_ref() -> tuple[str, str, int]:
    """
    Resolve the PR for the current branch (whatever gh considers associated).
    Works for cross-repo PRs too, by reading head repository owner/name.
    """
    pr = gh_pr_view_json("number,headRepositoryOwner,headRepository")
    owner = pr["headRepositoryOwner"]["login"]
    repo = pr["headRepository"]["name"]
    number = int(pr["number"])
    return owner, repo, number


def gh_api_graphql(
    owner: str,
    repo: str,
    number: int,
    comments_cursor: str | None = None,
    reviews_cursor: str | None = None,
    threads_cursor: str | None = None,
) -> dict[str, Any]:
    """
    Call `gh api graphql` using -F variables, avoiding JSON blobs with nulls.
    Query is passed via stdin using query=@- to avoid shell newline/quoting issues.
    """
    cmd = [
        "gh",
        "api",
        "graphql",
        "-F",
        "query=@-",
        "-F",
        f"owner={owner}",
        "-F",
        f"repo={repo}",
        "-F",
        f"number={number}",
    ]
    if comments_cursor:
        cmd += ["-F", f"commentsCursor={comments_cursor}"]
    if reviews_cursor:
        cmd += ["-F", f"reviewsCursor={reviews_cursor}"]
    if threads_cursor:
        cmd += ["-F", f"threadsCursor={threads_cursor}"]

    return _run_json(cmd, stdin=QUERY)


def fetch_all(owner: str, repo: str, number: int) -> dict[str, Any]:
    conversation_comments: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    review_threads: list[dict[str, Any]] = []

    comments_cursor: str | None = None
    reviews_cursor: str | None = None
    threads_cursor: str | None = None

    pr_meta: dict[str, Any] | None = None

    while True:
        payload = gh_api_graphql(
            owner=owner,
            repo=repo,
            number=number,
            comments_cursor=comments_cursor,
            reviews_cursor=reviews_cursor,
            threads_cursor=threads_cursor,
        )

        if "errors" in payload and payload["errors"]:
            raise RuntimeError(f"GitHub GraphQL errors:\n{json.dumps(payload['errors'], indent=2)}")

        pr = payload["data"]["repository"]["pullRequest"]
        if pr_meta is None:
            pr_meta = {
                "number": pr["number"],
                "url": pr["url"],
                "title": pr["title"],
                "state": pr["state"],
                "owner": owner,
                "repo": repo,
            }

        c = pr["comments"]
        r = pr["reviews"]
        t = pr["reviewThreads"]

        conversation_comments.extend(c.get("nodes") or [])
        reviews.extend(r.get("nodes") or [])
        review_threads.extend(t.get("nodes") or [])

        comments_cursor = c["pageInfo"]["endCursor"] if c["pageInfo"]["hasNextPage"] else None
        reviews_cursor = r["pageInfo"]["endCursor"] if r["pageInfo"]["hasNextPage"] else None
        threads_cursor = t["pageInfo"]["endCursor"] if t["pageInfo"]["hasNextPage"] else None

        if not (comments_cursor or reviews_cursor or threads_cursor):
            break

    assert pr_meta is not None
    return {
        "pull_request": pr_meta,
        "conversation_comments": conversation_comments,
        "reviews": reviews,
        "review_threads": review_threads,
    }


def _sort_thread_key(thread: dict[str, Any]) -> tuple[str, int, str]:
    comments = thread.get("comments", {}).get("nodes") or []
    created_at = comments[0].get("createdAt", "") if comments else ""
    line = thread.get("line") or thread.get("originalLine") or thread.get("startLine") or 0
    path = thread.get("path") or ""
    return path, int(line), created_at


def _body_text(text: str | None) -> str:
    stripped = (text or "").strip()
    return stripped if stripped else "(no body)"


def _indent_block(text: str, prefix: str) -> str:
    return textwrap.indent(text, prefix)


def _thread_location(thread: dict[str, Any]) -> str:
    path = thread.get("path") or "(no path)"
    line = thread.get("line") or thread.get("originalLine") or thread.get("startLine")
    return f"{path}:{line}" if line else path


def _thread_status(thread: dict[str, Any]) -> str:
    labels: list[str] = []
    labels.append("resolved" if thread.get("isResolved") else "unresolved")
    if thread.get("isOutdated"):
        labels.append("outdated")
    return ", ".join(labels)


def _format_review_thread(thread: dict[str, Any], number: int) -> str:
    comments = thread.get("comments", {}).get("nodes") or []
    lines = [f"{number}. [{_thread_status(thread)}] `{_thread_location(thread)}`"]
    if comments and comments[0].get("url"):
        lines.append(f"   URL: {comments[0]['url']}")
    lines.append("   Thread:")
    for comment in comments:
        author = (comment.get("author") or {}).get("login") or "unknown"
        created_at = comment.get("createdAt") or "unknown time"
        lines.append(f"   - {author} @ {created_at}")
        lines.append(_indent_block(_body_text(comment.get("body")), "     "))
    return "\n".join(lines)


def _format_conversation_comment(comment: dict[str, Any], number: int) -> str:
    author = (comment.get("author") or {}).get("login") or "unknown"
    created_at = comment.get("createdAt") or "unknown time"
    lines = [f"{number}. [conversation] {author} @ {created_at}"]
    if comment.get("url"):
        lines.append(f"   URL: {comment['url']}")
    lines.append("   Body:")
    lines.append(_indent_block(_body_text(comment.get("body")), "     "))
    return "\n".join(lines)


def _format_review(review: dict[str, Any], number: int) -> str:
    author = (review.get("author") or {}).get("login") or "unknown"
    submitted_at = review.get("submittedAt") or "unknown time"
    state = review.get("state") or "UNKNOWN"
    lines = [f"{number}. [review:{state.lower()}] {author} @ {submitted_at}"]
    if review.get("url"):
        lines.append(f"   URL: {review['url']}")
    lines.append("   Body:")
    lines.append(_indent_block(_body_text(review.get("body")), "     "))
    return "\n".join(lines)


def to_markdown(result: dict[str, Any], unresolved_only: bool = False) -> str:
    pr = result["pull_request"]
    threads = sorted(result.get("review_threads") or [], key=_sort_thread_key)
    unresolved_threads = [thread for thread in threads if not thread.get("isResolved")]
    resolved_threads = [thread for thread in threads if thread.get("isResolved")]
    conversation_comments = result.get("conversation_comments") or []
    reviews = [review for review in (result.get("reviews") or []) if (review.get("body") or "").strip()]

    lines = [
        f"# PR #{pr['number']} — {pr['title']}",
        "",
        f"- Repo: `{pr['owner']}/{pr['repo']}`",
        f"- State: `{pr['state']}`",
        f"- URL: {pr['url']}",
    ]

    counter = 1

    if unresolved_threads:
        lines.extend(["", f"## Unresolved review threads ({len(unresolved_threads)})", ""])
        for thread in unresolved_threads:
            lines.append(_format_review_thread(thread, counter))
            lines.append("")
            counter += 1

    if not unresolved_only and conversation_comments:
        lines.extend(["", f"## Conversation comments ({len(conversation_comments)})", ""])
        for comment in conversation_comments:
            lines.append(_format_conversation_comment(comment, counter))
            lines.append("")
            counter += 1

    if not unresolved_only and reviews:
        lines.extend(["", f"## Review summaries with body ({len(reviews)})", ""])
        for review in reviews:
            lines.append(_format_review(review, counter))
            lines.append("")
            counter += 1

    if not unresolved_only and resolved_threads:
        lines.extend(["", f"## Resolved review threads ({len(resolved_threads)})", ""])
        for thread in resolved_threads:
            lines.append(_format_review_thread(thread, counter))
            lines.append("")
            counter += 1

    if counter == 1:
        lines.extend(["", "No comments or review threads found."])

    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch GitHub PR comments and review threads for the current branch")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument(
        "--unresolved-only",
        action="store_true",
        help="Only include unresolved review threads in markdown output",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    _ensure_gh_authenticated()
    owner, repo, number = get_current_pr_ref()
    result = fetch_all(owner, repo, number)
    if args.format == "markdown":
        print(to_markdown(result, unresolved_only=args.unresolved_only), end="")
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
