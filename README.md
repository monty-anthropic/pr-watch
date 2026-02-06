# PR Watch

macOS menu bar app that shows your open GitHub PRs with CI/review status.

## Features

- **Menu bar badge** — shows PR count with status-aware icon (red if CI failing, etc.)
- **Click to expand** — see each PR with title, repo, CI status, review state
- **Watch extra PRs** — add teammate PRs via URL (menu bar dialog or CLI)
- **Agent-friendly** — all PR data exposed as JSON at `~/.pr-watch/prs.json`
- **Auto-refresh** — polls GitHub every 2 minutes via GraphQL
- **Auto-cleanup** — merged/closed watched PRs are removed automatically

## Setup

```bash
cd ~/code/pr-watch
bash setup.sh
```

## CLI (`prw`)

```bash
prw                    # Show all PRs (summary)
prw list               # Same as above
prw json               # Raw JSON (pipe to jq, agents, etc.)
prw add <url>          # Watch a PR
prw remove <url|num>   # Stop watching
prw watching           # List watched PR URLs
prw refresh            # Restart the app
prw status             # Show app status
```

## Agent Integration

Point any agent at `~/.pr-watch/prs.json` to read the current state:

```
cat ~/.pr-watch/prs.json | jq '.all_prs[] | {title, url, ci_label, review_label}'
```

Or use the CLI:
```
prw json | jq '.all_prs[] | {title, url, ci_label, review_label}'
```

## Config

Edit `~/.pr-watch/config.json`:

```json
{
  "refresh_interval_seconds": 120,
  "my_prs_query": "is:pr is:open author:@me",
  "watched_prs": [
    "https://github.com/org/repo/pull/123"
  ]
}
```

## Manage

```bash
# Stop
launchctl unload ~/Library/LaunchAgents/com.monty.pr-watch.plist

# Start
launchctl load ~/Library/LaunchAgents/com.monty.pr-watch.plist

# Uninstall
bash uninstall.sh
```

## Logs

```bash
tail -f ~/.pr-watch/pr-watch.log
tail -f ~/.pr-watch/pr-watch.err.log
```
