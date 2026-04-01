---
name: claude-usage-insight
description: Analyze local Claude Code usage from ~/.claude data. Use this skill when the user wants Claude Code daily or custom-range statistics, token breakdowns, by-hour summaries, project or task attribution, session rankings, or a local HTML report showing where usage went.
---

# Claude Usage Insight

## When To Use

Use this skill when the user wants to understand where Claude Code usage went across a time range, especially for:

- daily, weekly, monthly, or custom-range usage summaries
- token breakdowns by `input`, `output`, `cache creation`, `cache read`, and `subagent`
- top projects, top task types, top models, top sessions, or top active hours
- local HTML reports for a deeper review

## Default Behavior

**When the user invokes this skill without specifying a range or command, always run `report --preset today`.** This generates today's HTML report and auto-opens it in the browser. The report file is named by date (`claude-usage-2026-04-01.html`) and overwrites any previous report for the same date, so there's no file pileup.

Do NOT ask the user what range they want unless their request is ambiguous. Prefer sensible defaults:

- "看用量" / "usage" / no args → `report --preset today`
- "昨天" / "yesterday" → `report --preset yesterday`
- "这周" / "this week" → `report --last 7d`
- "这个月" / "this month" → `report --preset this-month`
- Explicit date range → `report --since ... --until ...`

## Workflow

1. Resolve the requested time range (or default to `today`).
2. Run `scripts/claude_usage_insight.py report` with the appropriate range flags.
3. The script writes an HTML file (named by date range, overwrites on re-run) and auto-opens it on macOS.
4. Present a brief in-chat summary of key numbers. Include the report file path.

If the user only wants a quick text answer (not a full report), use `summary` instead of `report`.

## Commands

### Report (default)

Generates an HTML report, writes it to `~/.claude/usage-data/reports/`, and auto-opens in browser.

```
python3 scripts/claude_usage_insight.py report --preset today
python3 scripts/claude_usage_insight.py report --last 7d
python3 scripts/claude_usage_insight.py report --since 2026-04-01 --until 2026-04-07
```

### Summary

Use for a quick text-only overview in chat.

```
python3 scripts/claude_usage_insight.py summary --preset today
python3 scripts/claude_usage_insight.py summary --last 7d
```

### Top

Use when the user asks for a ranked breakdown by one dimension.

```
python3 scripts/claude_usage_insight.py top --by project --preset today
python3 scripts/claude_usage_insight.py top --by task --last 30d
python3 scripts/claude_usage_insight.py top --by hour --metric requests --last 7d
```

Supported `--by` values: `project`, `task`, `model`, `hour`, `session`, `source`
Supported `--metric` values: `tokens`, `requests`, `sessions`

## File Naming

Reports are named by date range:
- Single day: `claude-usage-2026-04-01.html`
- Date range: `claude-usage-2026-04-01_to_2026-04-07.html`

Re-running for the same range overwrites the old file. No timestamp suffix, no pileup.

## Data Sources

- `~/.claude/projects/**/*.jsonl` for request-level token usage
- `~/.claude/usage-data/session-meta/*.json` for session metadata and tool counts
- `~/.claude/usage-data/facets/*.json` for semantic labels such as goals and outcomes
- `~/.claude/history.jsonl` as fallback for first-prompt text

## Notes

- Request-level usage is deduplicated by `sessionId + requestId + source`.
- Task attribution prefers `facets.goal_categories` and falls back to prompt heuristics.
- This is an approximation tool, not authoritative billing data.
