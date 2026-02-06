#!/usr/bin/env python3
"""
prw — CLI companion for PR Watch.

Usage:
  prw                    Show all watched PRs (summary)
  prw list               Show all PRs with details
  prw json               Dump raw JSON (for piping to agents)
  prw add <url>          Add a PR to watch
  prw remove <url|num>   Remove a watched PR
  prw refresh            Trigger a refresh (restarts the app)
  prw status             Show app status
  prw watching           List just the watched PR URLs
"""

import json
import os
import subprocess
import sys
from pathlib import Path

DATA_DIR = Path.home() / ".pr-watch"
CONFIG_FILE = DATA_DIR / "config.json"
PR_JSON_FILE = DATA_DIR / "prs.json"


def load_config():
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {"watched_prs": []}


def save_config(cfg):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def load_prs():
    if PR_JSON_FILE.exists():
        return json.loads(PR_JSON_FILE.read_text())
    return None


def format_pr(pr, indent="  "):
    ci = pr.get("ci_icon", "⚪")
    rv = pr.get("review_icon", "—")
    draft = " (draft)" if pr.get("isDraft") else ""
    title = pr.get("title", "?")[:60]
    repo = pr.get("repo_short", "?")
    num = pr.get("number", "?")
    url = pr.get("url", "")
    ci_label = pr.get("ci_label", "?")
    rv_label = pr.get("review_label", "?")

    lines = [
        f"{indent}{ci}{rv} {repo}#{num}: {title}{draft}",
        f"{indent}   CI: {ci_label} | Review: {rv_label}",
        f"{indent}   {url}",
    ]

    # Show failing checks
    failing = [c for c in pr.get("checks", [])
               if c.get("conclusion") in ("FAILURE", "failure", "ERROR", "error")]
    if failing:
        lines.append(f"{indent}   Failing: {', '.join(c['name'] for c in failing[:5])}")

    return "\n".join(lines)


def cmd_default():
    data = load_prs()
    if not data:
        print("No PR data yet. App may still be loading.")
        return

    total = data.get("total_count", 0)
    updated = data.get("last_updated", "?")
    print(f"PR Watch — {total} PRs (updated {updated})")
    print()

    my_prs = data.get("my_prs", [])
    if my_prs:
        print(f"My PRs ({len(my_prs)}):")
        for pr in my_prs:
            print(format_pr(pr))
            print()

    watched = data.get("watched_prs", [])
    if watched:
        print(f"Watching ({len(watched)}):")
        for pr in watched:
            print(format_pr(pr))
            print()

    if not my_prs and not watched:
        print("No open PRs found.")


def cmd_json():
    data = load_prs()
    if data:
        print(json.dumps(data, indent=2))
    else:
        print("{}")


def cmd_add(url):
    import re
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)", url.strip())
    if not m:
        print(f"Invalid PR URL: {url}")
        print("Expected: https://github.com/org/repo/pull/123")
        sys.exit(1)

    cfg = load_config()
    watched = cfg.get("watched_prs", [])
    if url in watched:
        print(f"Already watching: {url}")
        return

    watched.append(url.strip())
    cfg["watched_prs"] = watched
    save_config(cfg)
    print(f"Added: {url}")
    print("The menu bar will update on the next refresh cycle (or click Refresh).")


def cmd_remove(target):
    cfg = load_config()
    watched = cfg.get("watched_prs", [])

    to_remove = None
    # Try matching by URL or by PR number
    for url in watched:
        if target == url or target in url:
            to_remove = url
            break

    if to_remove:
        watched.remove(to_remove)
        cfg["watched_prs"] = watched
        save_config(cfg)
        print(f"Removed: {to_remove}")
    else:
        print(f"Not found: {target}")
        if watched:
            print("Currently watching:")
            for u in watched:
                print(f"  {u}")


def cmd_watching():
    cfg = load_config()
    watched = cfg.get("watched_prs", [])
    if watched:
        for u in watched:
            print(u)
    else:
        print("Not watching any extra PRs.")


def cmd_refresh():
    plist = Path.home() / "Library/LaunchAgents/com.monty.pr-watch.plist"
    if plist.exists():
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        subprocess.run(["launchctl", "load", str(plist)], capture_output=True)
        print("Restarted PR Watch. Will refresh in a few seconds.")
    else:
        print("LaunchAgent not found. Run setup.sh first.")


def cmd_status():
    # Check if process is running
    result = subprocess.run(
        ["pgrep", "-f", "pr_watch.py"],
        capture_output=True, text=True,
    )
    running = result.returncode == 0

    data = load_prs()
    cfg = load_config()

    print("PR Watch Status")
    print(f"  Running: {'yes' if running else 'NO'}")
    print(f"  Config:  {CONFIG_FILE}")
    print(f"  Data:    {PR_JSON_FILE}")
    if data:
        print(f"  Last updated: {data.get('last_updated', '?')}")
        print(f"  My PRs: {len(data.get('my_prs', []))}")
        print(f"  Watched: {len(data.get('watched_prs', []))}")
    print(f"  Watched URLs in config: {len(cfg.get('watched_prs', []))}")
    print(f"  Refresh interval: {cfg.get('refresh_interval_seconds', 120)}s")


def main():
    args = sys.argv[1:]

    if not args:
        cmd_default()
    elif args[0] == "list":
        cmd_default()
    elif args[0] == "json":
        cmd_json()
    elif args[0] == "add" and len(args) >= 2:
        cmd_add(args[1])
    elif args[0] == "remove" and len(args) >= 2:
        cmd_remove(args[1])
    elif args[0] == "watching":
        cmd_watching()
    elif args[0] == "refresh":
        cmd_refresh()
    elif args[0] == "status":
        cmd_status()
    elif args[0] in ("-h", "--help", "help"):
        print(__doc__)
    else:
        print(f"Unknown command: {args[0]}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
