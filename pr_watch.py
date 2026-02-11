#!/usr/bin/env python3
"""
PR Watch — macOS menu bar app for watching GitHub PRs.

Shows a count badge in the menu bar. Click to see each PR with title,
repo, CI status, and review state. Supports adding extra PRs to watch
beyond your own authored ones.

Exposes all PR data as JSON at ~/.pr-watch/prs.json for agent consumption.
"""

import json
import logging
import logging.handlers
import re
import subprocess
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread

import rumps

# ── Paths ──────────────────────────────────────────────────────────────
DATA_DIR = Path.home() / ".pr-watch"
CONFIG_FILE = DATA_DIR / "config.json"
PR_JSON_FILE = DATA_DIR / "prs.json"

# ── Defaults ───────────────────────────────────────────────────────────
LOG_FILE = DATA_DIR / "pr-watch-debug.log"

def _setup_logger() -> logging.Logger:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("pr-watch")
    logger.setLevel(logging.DEBUG)
    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=2,
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)
    return logger

log = _setup_logger()

DEFAULT_CONFIG = {
    "refresh_interval_seconds": 20,
    "my_prs_query": "is:pr is:open author:@me",
    "watched_prs": [],  # list of PR URLs
}

GRAPHQL_QUERY = """
{
  search(query: "%QUERY%", type: ISSUE, first: 50) {
    nodes {
      ... on PullRequest {
        number
        title
        url
        isDraft
        state
        createdAt
        updatedAt
        mergeable
        repository {
          nameWithOwner
        }
        reviewDecision
        commits(last: 1) {
          nodes {
            commit {
              statusCheckRollup {
                state
                contexts(first: 100) {
                  nodes {
                    ... on CheckRun {
                      name
                      conclusion
                      status
                    }
                    ... on StatusContext {
                      context
                      state
                    }
                  }
                }
              }
            }
          }
        }
        mergeQueueEntry { state position }
        autoMergeRequest { enabledAt }
        reviews(last: 10) {
          nodes {
            state
            author { login }
          }
        }
        labels(first: 10) {
          nodes { name }
        }
      }
    }
  }
}
"""

PR_DETAIL_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      number
      title
      url
      isDraft
      state
      createdAt
      updatedAt
      mergeable
      author { login }
      repository {
        nameWithOwner
      }
      reviewDecision
      commits(last: 1) {
        nodes {
          commit {
            statusCheckRollup {
              state
              contexts(first: 100) {
                nodes {
                  ... on CheckRun {
                    name
                    conclusion
                    status
                  }
                  ... on StatusContext {
                    context
                    state
                  }
                }
              }
            }
          }
        }
      }
      mergeQueueEntry { state position }
      autoMergeRequest { enabledAt }
      reviews(last: 10) {
        nodes {
          state
          author { login }
        }
      }
      labels(first: 10) {
        nodes { name }
      }
    }
  }
}
"""


# ── Status helpers ─────────────────────────────────────────────────────
def combined_icon(ci_state: str | None, review_decision: str | None, pr_state: str = "OPEN", in_merge_queue: bool = False, mergeable: str | None = None) -> str:
    """Single icon reflecting the overall PR status (CI + review combined)."""
    # Merged or closed
    if pr_state == "MERGED":
        return "✅"
    if pr_state == "CLOSED":
        return "⚫"
    # In merge queue
    if in_merge_queue:
        return "🚀"
    # Merge conflicts
    if mergeable == "CONFLICTING":
        return "⚔️"
    # CI failing/erroring always takes priority
    if ci_state in ("FAILURE", "ERROR"):
        return "❌"
    # Changes requested is a blocker
    if review_decision == "CHANGES_REQUESTED":
        return "🔴"
    # CI still running
    if ci_state in ("PENDING", "EXPECTED"):
        return "🟡"
    # CI green but needs review — not done yet
    if ci_state == "SUCCESS" and review_decision in ("REVIEW_REQUIRED", None):
        return "🟣"
    # CI green + approved — ready to merge
    if ci_state == "SUCCESS" and review_decision == "APPROVED":
        return "🟢"
    return "⚪"


def ci_icon(state: str | None) -> str:
    return {
        "SUCCESS": "✅",
        "FAILURE": "❌",
        "ERROR": "❌",
        "PENDING": "🟡",
        "EXPECTED": "🟡",
        None: "⚪",
    }.get(state, "⚪")


def review_icon(decision: str | None) -> str:
    return {
        "APPROVED": "✅",
        "CHANGES_REQUESTED": "🔴",
        "REVIEW_REQUIRED": "👀",
        None: "—",
    }.get(decision, "—")


def ci_label(state: str | None) -> str:
    return {
        "SUCCESS": "CI green",
        "FAILURE": "CI failing",
        "ERROR": "CI error",
        "PENDING": "CI running",
        None: "No CI",
    }.get(state, "Unknown")


def review_label(decision: str | None) -> str:
    return {
        "APPROVED": "Approved",
        "CHANGES_REQUESTED": "Changes requested",
        "REVIEW_REQUIRED": "Needs review",
        None: "No reviews",
    }.get(decision, "Unknown")


def parse_pr_url(url: str) -> tuple[str, str, int] | None:
    """Extract (owner, repo, number) from a GitHub PR URL."""
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)", url.strip().rstrip("/"))
    if m:
        return m.group(1), m.group(2), int(m.group(3))
    return None


def normalize_url(url: str) -> str:
    """Normalize a PR URL by stripping trailing slashes."""
    return url.strip().rstrip("/")


def time_ago(iso_str: str) -> str:
    """Convert ISO timestamp to relative time string."""
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    delta = datetime.now(timezone.utc) - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        m = seconds // 60
        return f"{m}m ago"
    elif seconds < 86400:
        h = seconds // 3600
        return f"{h}h ago"
    else:
        d = seconds // 86400
        return f"{d}d ago"


# ── Config ─────────────────────────────────────────────────────────────
def load_config() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        # Backfill any missing keys
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    else:
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


# ── GitHub API ─────────────────────────────────────────────────────────
def run_gh(*args: str, input_data: str | None = None) -> str | None:
    """Run a gh CLI command and return stdout, or None on failure."""
    cmd_summary = " ".join(args[:3])
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=30,
            input=input_data,
        )
        if result.returncode == 0:
            return result.stdout
        else:
            log.warning("gh error (cmd=%s): %s", cmd_summary, result.stderr.strip())
            return None
    except subprocess.TimeoutExpired:
        log.error("gh timeout after 30s (cmd=%s)", cmd_summary)
        return None
    except Exception as e:
        log.error("gh exception (cmd=%s): %s", cmd_summary, e)
        return None


def fetch_my_prs(query: str) -> list[dict]:
    """Fetch authored PRs via GraphQL search."""
    gql = GRAPHQL_QUERY.replace("%QUERY%", query.replace('"', '\\"'))
    raw = run_gh("api", "graphql", "-f", f"query={gql}")
    if not raw:
        return []
    try:
        data = json.loads(raw)
        nodes = data.get("data", {}).get("search", {}).get("nodes", [])
        return [normalize_pr(n, source="authored") for n in nodes if n]
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Parse error: {e}")
        return []


def fetch_single_pr(owner: str, repo: str, number: int) -> dict | None:
    """Fetch a single PR by owner/repo/number."""
    raw = run_gh(
        "api", "graphql",
        "-f", f"query={PR_DETAIL_QUERY}",
        "-F", f"owner={owner}",
        "-F", f"repo={repo}",
        "-F", f"number={number}",
    )
    if not raw:
        return None
    try:
        data = json.loads(raw)
        pr = data.get("data", {}).get("repository", {}).get("pullRequest")
        if pr:
            return normalize_pr(pr, source="watched")
        return None
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Parse error for {owner}/{repo}#{number}: {e}")
        return None


def _fetch_mergeable_rest(pr_url: str) -> str | None:
    """Fetch mergeable status via REST API (more reliable than GraphQL)."""
    # Extract owner/repo/number from URL
    parsed = parse_pr_url(pr_url)
    if not parsed:
        return None
    owner, repo, number = parsed
    raw = run_gh("pr", "view", str(number), "--repo", f"{owner}/{repo}", "--json", "mergeable")
    if raw:
        try:
            return json.loads(raw).get("mergeable")
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def normalize_pr(node: dict, source: str = "authored") -> dict:
    """Normalize a GraphQL PR node into a flat dict."""
    # CI state
    ci_state = None
    commits = node.get("commits", {}).get("nodes", [])
    if commits:
        rollup = commits[0].get("commit", {}).get("statusCheckRollup")
        if rollup:
            ci_state = rollup.get("state")

    # Check details (individual checks)
    checks = []
    if commits:
        rollup = commits[0].get("commit", {}).get("statusCheckRollup")
        if rollup:
            for ctx in rollup.get("contexts", {}).get("nodes", []):
                if "name" in ctx:  # CheckRun
                    checks.append({
                        "name": ctx["name"],
                        "status": ctx.get("status"),
                        "conclusion": ctx.get("conclusion"),
                    })
                elif "context" in ctx:  # StatusContext
                    checks.append({
                        "name": ctx["context"],
                        "status": ctx.get("state"),
                        "conclusion": ctx.get("state"),
                    })

    # Reviews
    review_decision = node.get("reviewDecision")
    reviews = []
    for r in node.get("reviews", {}).get("nodes", []):
        reviews.append({
            "state": r.get("state"),
            "author": r.get("author", {}).get("login"),
        })

    # Labels
    labels = [l["name"] for l in node.get("labels", {}).get("nodes", [])]

    # Merge queue
    mq = node.get("mergeQueueEntry")
    in_merge_queue = mq is not None
    merge_queue_position = mq.get("position") if mq else None

    # Mergeable — GraphQL often returns UNKNOWN; fall back to REST
    mergeable = node.get("mergeable")
    if mergeable in ("UNKNOWN", None) and node.get("url"):
        mergeable = _fetch_mergeable_rest(node["url"]) or mergeable

    repo = node.get("repository", {}).get("nameWithOwner", "")

    return {
        "number": node.get("number"),
        "title": node.get("title", ""),
        "url": node.get("url", ""),
        "repo": repo,
        "repo_short": repo.split("/")[-1] if "/" in repo else repo,
        "isDraft": node.get("isDraft", False),
        "state": node.get("state", "OPEN"),
        "createdAt": node.get("createdAt"),
        "updatedAt": node.get("updatedAt"),
        "mergeable": mergeable,
        "in_merge_queue": in_merge_queue,
        "merge_queue_position": merge_queue_position,
        "ci_state": ci_state,
        "ci_icon": ci_icon(ci_state),
        "ci_label": ci_label(ci_state),
        "review_decision": review_decision,
        "review_icon": review_icon(review_decision),
        "review_label": review_label(review_decision),
        "status_icon": combined_icon(ci_state, review_decision, node.get("state", "OPEN"), in_merge_queue, node.get("mergeable")),
        "checks": checks,
        "reviews": reviews,
        "labels": labels,
        "source": source,
        "author": node.get("author", {}).get("login") if "author" in node else None,
    }


def save_pr_data(my_prs: list[dict], watched_prs: list[dict]):
    """Write all PR data to JSON for agent consumption."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    output = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "total_count": len(my_prs) + len(watched_prs),
        "my_prs": my_prs,
        "watched_prs": watched_prs,
        "all_prs": my_prs + watched_prs,
    }
    with open(PR_JSON_FILE, "w") as f:
        json.dump(output, f, indent=2)


# ── Menu Bar App ───────────────────────────────────────────────────────
class PRWatchApp(rumps.App):
    def __init__(self):
        super().__init__("PR", quit_button=None)
        log.info("PRWatchApp starting (pid=%d, thread=%s)", __import__("os").getpid(), threading.current_thread().name)
        self.config_data = load_config()
        self.my_prs: list[dict] = []
        self.watched_prs: list[dict] = []
        self.icon = None  # Use text-only title
        self._fetch_pending = True
        self._fetching = False
        self._needs_rebuild = False
        self._consecutive_failures = 0
        self._error_title = None  # set from bg thread, applied on main thread

        self.title = "⏳"

        self._refresh_interval = self.config_data.get("refresh_interval_seconds", 120)
        self._last_fetch_time = 0.0

        # Keep a strong reference to the NSStatusItem to prevent GC
        self._status_item_ref = None
        self._tick_count = 0

        # Persistent worker thread — wakes on signal instead of spawning new threads
        self._fetch_event = Event()
        self._worker = Thread(target=self._worker_loop, daemon=True, name="pr-watch-worker")
        self._worker.start()

        # Listen for display configuration changes (monitor plug/unplug, wake, etc.)
        self._register_display_notifications()

        # Single tick timer runs on main thread
        self._tick_timer = rumps.Timer(self._tick, 1)
        self._tick_timer.start()

    def _ensure_status_item(self):
        """Re-assert NSStatusItem reference to prevent GC from killing it."""
        try:
            from AppKit import NSStatusBar, NSVariableStatusItemLength
            if self._status_item_ref is None:
                self._status_item_ref = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
                log.info("status item reference captured")
            # Verify the status item is still valid by checking its button
            button = self._status_item_ref.button()
            if button is None:
                log.warning("status item button is None — re-creating")
                self._status_item_ref = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        except Exception as e:
            log.error("status item health check failed: %s", e)

    def _register_display_notifications(self):
        """Register for display config change notifications (monitor plug/unplug, wake)."""
        try:
            from AppKit import NSApplicationDidChangeScreenParametersNotification
            from Foundation import NSNotificationCenter
            NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
                self, "_onDisplayChanged:",
                NSApplicationDidChangeScreenParametersNotification, None,
            )
            log.info("registered for display change notifications")
        except Exception as e:
            log.error("failed to register display notifications: %s", e)

    def _onDisplayChanged_(self, notification):
        """Called when display configuration changes — re-assert status item."""
        log.info("display configuration changed — refreshing status item")
        self._ensure_status_item()
        self._needs_rebuild = True

    def _tick(self, _sender):
        """Main-thread tick."""
        import time as _time
        now = _time.time()
        self._tick_count += 1

        # Periodic status item health check (every 60s)
        if self._tick_count % 60 == 0:
            self._ensure_status_item()

        # Rebuild menu if background fetch completed
        if self._needs_rebuild:
            self._needs_rebuild = False
            # Apply error title from bg thread safely on main thread
            if self._error_title:
                log.warning("setting error title on main thread: %s", self._error_title)
                self.title = self._error_title
                self._error_title = None
            try:
                self._rebuild_menu()
            except Exception as e:
                import traceback
                log.error("rebuild error: %s\n%s", e, traceback.format_exc())

        # Signal the worker thread if a fetch is needed
        if not self._fetching:
            if self._fetch_pending or (now - self._last_fetch_time >= self._refresh_interval):
                self._fetch_pending = False
                self._fetching = True
                self._last_fetch_time = now
                self._fetch_event.set()

    def _worker_loop(self):
        """Persistent worker thread — waits for signal, fetches, repeats."""
        log.info("worker thread started")
        while True:
            self._fetch_event.wait()
            self._fetch_event.clear()
            self._do_fetch()

    def _do_fetch(self):
        """Background thread: fetch data, set flag for main-thread rebuild."""
        import traceback
        log.debug("fetch started")
        try:
            self.config_data = load_config()

            query = self.config_data.get("my_prs_query", DEFAULT_CONFIG["my_prs_query"])
            self.my_prs = fetch_my_prs(query)
            dismissed = {normalize_url(u) for u in self.config_data.get("dismissed_prs", [])}

            self.watched_prs = []
            for url in self.config_data.get("watched_prs", []):
                if normalize_url(url) in dismissed:
                    continue
                parsed = parse_pr_url(url)
                if parsed:
                    owner, repo, number = parsed
                    pr = fetch_single_pr(owner, repo, number)
                    if pr and not any(p["url"] == pr["url"] for p in self.my_prs):
                        self.watched_prs.append(pr)

            save_pr_data(self.my_prs, self.watched_prs)
            self._needs_rebuild = True
            self._consecutive_failures = 0
            log.debug("fetch ok — %d authored, %d watched", len(self.my_prs), len(self.watched_prs))

        except Exception:
            self._consecutive_failures += 1
            log.error("fetch failed (consecutive=%d):\n%s", self._consecutive_failures, traceback.format_exc())
            self._error_title = "⚠️"
            self._needs_rebuild = True  # let main thread set the title safely
        finally:
            self._fetching = False

    def _rebuild_menu(self):
        """Rebuild the entire dropdown menu. Runs on main thread."""
        log.debug("rebuild_menu started (thread=%s)", threading.current_thread().name)
        all_prs = self.my_prs + self.watched_prs
        open_prs = [p for p in all_prs if p.get("state") == "OPEN"]
        total = len(open_prs)

        failing = sum(1 for p in open_prs if p["ci_state"] in ("FAILURE", "ERROR"))
        needs_attn = sum(1 for p in open_prs if p["review_decision"] == "CHANGES_REQUESTED")

        if failing > 0:
            self.title = f"𝓟𝓡𝓼 ❌{total}"
        elif needs_attn > 0:
            self.title = f"𝓟𝓡𝓼 🔴{total}"
        else:
            self.title = f"𝓟𝓡𝓼 {total}"

        # Wipe the old menu
        self.menu.clear()

        # ── Header ────────────────────────────────────────
        ts = datetime.now().strftime("%H:%M")
        info = rumps.MenuItem(f"Updated {ts}  ·  ⌥-click to dismiss")
        info.set_callback(None)
        self.menu.add(info)
        self.menu.add(rumps.MenuItem("+ Add", callback=self._on_add_pr))
        self.menu.add(rumps.separator)

        # ── My PRs ────────────────────────────────────────
        if self.my_prs:
            header = rumps.MenuItem("Mine")
            header.set_callback(None)
            self.menu.add(header)
            for pr in self.my_prs:
                self._add_pr_items(pr)

        # ── Watched PRs ───────────────────────────────────
        if self.watched_prs:
            self.menu.add(rumps.separator)
            header = rumps.MenuItem("Watching")
            header.set_callback(None)
            self.menu.add(header)
            for pr in self.watched_prs:
                self._add_pr_items(pr)

        # ── Footer ───────────────────────────────────────
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Quit", callback=rumps.quit_application))

    def _add_pr_items(self, pr: dict):
        """Add flat menu items for a single PR (no nested submenus)."""
        icon = pr.get("status_icon", pr["ci_icon"])
        draft = " [draft]" if pr["isDraft"] else ""
        title_text = pr["title"][:55]
        updated = time_ago(pr["updatedAt"]) if pr.get("updatedAt") else ""
        is_done = pr.get("state") in ("MERGED", "CLOSED")

        # Main line: clickable, opens PR in browser
        state_suffix = ""
        if pr.get("state") == "MERGED":
            state_suffix = " — merged"
        elif pr.get("state") == "CLOSED":
            state_suffix = " — closed"
        main_label = f"{icon}  {pr['repo_short']}#{pr['number']}: {title_text}{draft}{state_suffix}"
        open_item = rumps.MenuItem(main_label, callback=self._make_open_cb(pr["url"]))
        self.menu.add(open_item)

        # Alternate item: shown when Option is held — only for watched PRs
        if pr.get("source") == "watched":
            from AppKit import NSAlternateKeyMask
            dismiss_label = f"     ✕  Dismiss #{pr['number']}"
            dismiss_item = rumps.MenuItem(dismiss_label, callback=self._make_dismiss_cb(pr["url"], "watched"))
            dismiss_item._menuitem.setAlternate_(True)
            dismiss_item._menuitem.setKeyEquivalentModifierMask_(NSAlternateKeyMask)
            self.menu.add(dismiss_item)

        if not is_done:
            # Detail line: CI + review status + author + time + failing checks
            parts = []
            if pr.get("in_merge_queue"):
                pos = pr.get("merge_queue_position")
                parts.append(f"Merge queue #{pos}" if pos is not None else "Merge queue")
            elif pr.get("mergeable") == "CONFLICTING":
                parts.append("Has conflicts")
            else:
                parts.extend([pr["ci_label"], pr["review_label"]])
            if pr.get("source") == "watched" and pr.get("author"):
                parts.append(f"by {pr['author']}")
            if updated:
                parts.append(updated)
            failing_checks = [
                c for c in pr.get("checks", [])
                if c.get("conclusion") in ("FAILURE", "failure", "ERROR", "error")
            ]
            if failing_checks:
                names = ", ".join(c["name"] for c in failing_checks[:3])
                parts.append(f"✕ {names}")
            detail_label = f"     {' · '.join(parts)}"
            detail = rumps.MenuItem(detail_label)
            detail.set_callback(None)
            self.menu.add(detail)


    def _make_open_cb(self, url: str):
        def cb(_):
            webbrowser.open(url)
        return cb

    def _make_dismiss_cb(self, url: str, source: str):
        def cb(_):
            norm = normalize_url(url)
            if source == "watched":
                self.config_data["watched_prs"] = [
                    u for u in self.config_data.get("watched_prs", []) if normalize_url(u) != norm
                ]
            self.config_data.setdefault("dismissed_prs", []).append(norm)
            save_config(self.config_data)
            self._fetch_pending = True
        return cb


    def _on_add_pr(self, _):
        # Use osascript for the dialog — it reliably appears in front
        script = '''
        tell application "System Events"
            display dialog "Paste a GitHub PR URL to watch:" ¬
                default answer "https://github.com/org/repo/pull/123" ¬
                with title "PR Watch" ¬
                buttons {"Cancel", "Add"} default button "Add"
            return text returned of result
        end tell
        '''
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                return  # User cancelled
            url = result.stdout.strip()
            if parse_pr_url(url):
                if url not in self.config_data.get("watched_prs", []):
                    self.config_data.setdefault("watched_prs", []).append(url)
                    save_config(self.config_data)
                    self._fetch_pending = True
            elif url and url != "https://github.com/org/repo/pull/123":
                subprocess.run(["osascript", "-e",
                    'display alert "Invalid URL" message "Paste a URL like: https://github.com/org/repo/pull/123"'])
        except subprocess.TimeoutExpired:
            pass



if __name__ == "__main__":
    log.info("=== pr-watch starting ===")
    app = PRWatchApp()
    app.run()
    log.info("=== pr-watch exited ===")  # if we ever get here, that's interesting
