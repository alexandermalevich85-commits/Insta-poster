"""Resolve stash-pop conflicts in pending_posts.json and history.json.

Called by .github/workflows/publish.yml after `git stash pop`. If two
concurrent runs raced, git left conflict markers in these files. This
script reads both sides via `git show :2:<file>` (ours, our stashed
update) and `:3:<file>` (theirs, the freshly pulled remote), merges
them semantically, writes the result, and runs `git add` on it.

Merge rules:
  pending_posts.json — union by post id. If a post appears on both
    sides, prefer the more-progressed status (published > scheduled >
    pending > failed); on a tie, prefer ours.
  history.json — union by ig_media_id. Order: theirs first, then any
    of ours not already present, sorted by date.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

STATUS_RANK = {"published": 3, "scheduled": 2, "pending": 1, "failed": 0}


def git_show_stage(stage: int, path: str) -> str | None:
    r = subprocess.run(
        ["git", "show", f":{stage}:{path}"],
        capture_output=True, text=True,
    )
    return r.stdout if r.returncode == 0 else None


def unmerged_paths() -> list[str]:
    r = subprocess.run(
        ["git", "ls-files", "--unmerged"],
        capture_output=True, text=True, check=True,
    )
    paths = set()
    for line in r.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            paths.add(parts[1])
    return sorted(paths)


def merge_pending(ours: list[dict], theirs: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {p["id"]: p for p in theirs}
    for p in ours:
        existing = by_id.get(p["id"])
        if existing is None:
            by_id[p["id"]] = p
            continue
        ours_rank = STATUS_RANK.get(p.get("status", ""), -1)
        theirs_rank = STATUS_RANK.get(existing.get("status", ""), -1)
        # Prefer ours on tie — we're the run that just published.
        if ours_rank >= theirs_rank:
            by_id[p["id"]] = p
    # Preserve original order: theirs first, then ours-only entries.
    seen = set()
    out: list[dict] = []
    for p in theirs:
        out.append(by_id[p["id"]])
        seen.add(p["id"])
    for p in ours:
        if p["id"] not in seen:
            out.append(by_id[p["id"]])
            seen.add(p["id"])
    return out


def merge_history(ours: list[dict], theirs: list[dict]) -> list[dict]:
    seen_mids = {e.get("ig_media_id") for e in theirs if e.get("ig_media_id")}
    merged = list(theirs)
    for e in ours:
        mid = e.get("ig_media_id")
        if mid and mid in seen_mids:
            continue
        merged.append(e)
        if mid:
            seen_mids.add(mid)
    merged.sort(key=lambda x: x.get("date", ""))
    return merged


HANDLERS = {
    "pending_posts.json": merge_pending,
    "history.json": merge_history,
}


def resolve(path: str) -> bool:
    handler = HANDLERS.get(path)
    if handler is None:
        return False
    ours_raw = git_show_stage(2, path)
    theirs_raw = git_show_stage(3, path)
    if ours_raw is None or theirs_raw is None:
        print(f"[safe_merge] {path}: missing stage 2 or 3, skipping")
        return False
    try:
        ours = json.loads(ours_raw)
        theirs = json.loads(theirs_raw)
    except json.JSONDecodeError as e:
        print(f"[safe_merge] {path}: one side is invalid JSON ({e}); skipping")
        return False
    merged = handler(ours, theirs)
    Path(path).write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", path], check=True)
    print(f"[safe_merge] {path}: merged {len(ours)} ours + {len(theirs)} theirs -> {len(merged)}")
    return True


def main() -> int:
    paths = unmerged_paths()
    if not paths:
        print("[safe_merge] no unmerged paths")
        return 0
    print(f"[safe_merge] unmerged: {paths}")
    unresolved = [p for p in paths if not resolve(p)]
    if unresolved:
        print(f"[safe_merge] could not resolve: {unresolved}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
