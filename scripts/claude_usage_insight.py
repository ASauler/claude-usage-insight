#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from collections import Counter, defaultdict
from html import escape
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc

# Pricing per 1M tokens (USD) by model family — Claude 4.6 era (2026)
MODEL_PRICING = {
    "opus": {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50},
    "sonnet": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    "haiku": {"input": 1.0, "output": 5.0, "cache_write": 1.25, "cache_read": 0.10},
}
DEFAULT_PRICING = MODEL_PRICING["opus"]


def _model_family(model_name: str) -> str:
    low = model_name.lower()
    if "haiku" in low:
        return "haiku"
    if "sonnet" in low:
        return "sonnet"
    return "opus"

TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)

PRESETS = {
    "today",
    "yesterday",
    "last-7d",
    "last-30d",
    "this-month",
    "last-month",
}

CATEGORY_DISPLAY = {
    "build_fix": "开发 / 修复",
    "research": "调研 / 分析",
    "docs_content": "文档 / 内容",
    "design_product": "设计 / 产品",
    "ops_env": "环境 / 运维",
    "auth_config": "账号 / 配置",
    "question": "快速提问",
    "prompt_content": "Prompt / 内容组织",
    "other": "其他",
}

GOAL_CATEGORY_MAP = {
    "bug_fix": "build_fix",
    "code_changes": "build_fix",
    "continue_implementation": "build_fix",
    "integrate_real_api": "build_fix",
    "code_modification_then_reverted": "build_fix",
    "codebase_exploration": "research",
    "project_analysis": "research",
    "strategic_analysis": "research",
    "data_analysis": "research",
    "design_research": "research",
    "documentation_creation": "docs_content",
    "documentation_generation": "docs_content",
    "project_planning_documentation": "docs_content",
    "document_review": "docs_content",
    "content_organization": "docs_content",
    "web_page_generation": "design_product",
    "design_planning": "design_product",
    "design_discussion": "design_product",
    "strategic_redesign": "design_product",
    "system_administration": "ops_env",
    "project_setup": "ops_env",
    "repository_management": "ops_env",
    "repository_cloning": "ops_env",
    "knowledge_question": "question",
    "quick_question": "question",
    "prompt_writing": "prompt_content",
    "creative_brainstorming": "prompt_content",
    "stop_current_work": "other",
}

HEURISTIC_RULES = [
    ("auth_config", ["auth", "login", "logout", "token", "apikey", "api key", "base_url", "base url", "授权", "登录", "登出", "配置", "密钥"]),
    ("ops_env", ["ssh", "docker", "deploy", "server", "ecs", "tmux", "install", "upgrade", "升级", "环境", "运维", "端口", "代理", "/mcp", "mcp", "clone", "git clone", "拉下来"]),
    ("build_fix", ["bug", "fix", "error", "debug", "报错", "修", "排查", "不对", "失败", "实现", "功能", "改一下", "集成", "api", "修复", "接入"]),
    ("research", ["research", "analyze", "analysis", "review", "compare", "调研", "分析", "看看", "看一下", "看下", "对比", "复盘", "扫一下", "了解", "统计", "usage", "token", "消耗"]),
    ("docs_content", ["readme", "doc", "docs", "document", "report", "总结", "文档", "说明", "写一个", "名片", "介绍", "汇总"]),
    ("design_product", ["design", "ui", "ux", "页面", "设计", "交互", "产品", "信息架构", "布局"]),
    ("prompt_content", ["prompt", "口头禅", "文案", "内容", "整理", "brainstorm", "头脑风暴"]),
    ("question", ["是什么", "啥", "怎么", "why", "what", "help", "?", "？"]),
]

TRIVIAL_PROMPTS = {
    "hi",
    "ok",
    "okay",
    "yes",
    "no",
    "?",
    "？",
    "1",
    "2",
    "3",
    "继续",
    "可以",
    "要的",
    "对的",
    "都行",
    "行",
    "好的",
    "嗯",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze local Claude Code usage from ~/.claude data.",
    )
    parser.add_argument(
        "--claude-dir",
        default="~/.claude",
        help="Claude data directory. Defaults to ~/.claude",
    )
    parser.add_argument(
        "--timezone",
        help="IANA timezone name. Defaults to your local timezone.",
    )

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--preset", choices=sorted(PRESETS))
    common.add_argument("--since", help="Start date or datetime, for example 2026-04-01")
    common.add_argument("--until", help="End date or datetime, for example 2026-04-07")
    common.add_argument("--last", help="Relative range such as 24h, 72h, 7d")
    common.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format for CLI responses.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    summary_parser = subparsers.add_parser("summary", parents=[common])
    summary_parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Limit for ranked sections in the summary.",
    )

    top_parser = subparsers.add_parser("top", parents=[common])
    top_parser.add_argument(
        "--by",
        required=True,
        choices=("project", "task", "model", "hour", "session", "source"),
        help="Dimension to rank.",
    )
    top_parser.add_argument(
        "--metric",
        default="tokens",
        choices=("tokens", "requests", "sessions"),
        help="Metric to rank by.",
    )
    top_parser.add_argument("--limit", type=int, default=10)

    report_parser = subparsers.add_parser("report", parents=[common])
    report_parser.add_argument(
        "--output",
        help="Optional output HTML path. Defaults under ~/.claude/usage-data/reports/",
    )

    return parser.parse_args()


def get_timezone(name: str | None) -> dt.tzinfo:
    if name:
        return ZoneInfo(name)
    local_tz = dt.datetime.now().astimezone().tzinfo
    return local_tz or UTC


def parse_isoish(value: str, local_tz: dt.tzinfo, *, end_of_day: bool) -> dt.datetime:
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        parsed_date = dt.date.fromisoformat(text)
        parsed_dt = dt.datetime.combine(parsed_date, dt.time.min, tzinfo=local_tz)
        if end_of_day:
            parsed_dt += dt.timedelta(days=1)
        return parsed_dt.astimezone(UTC)

    normalized = text.replace("Z", "+00:00")
    parsed_dt = dt.datetime.fromisoformat(normalized)
    if parsed_dt.tzinfo is None:
        parsed_dt = parsed_dt.replace(tzinfo=local_tz)
    return parsed_dt.astimezone(UTC)


def parse_last_spec(value: str) -> dt.timedelta:
    match = re.fullmatch(r"(\d+)([hdw])", value.strip().lower())
    if not match:
        raise ValueError("last must look like 24h, 72h, or 7d")
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "h":
        return dt.timedelta(hours=amount)
    if unit == "d":
        return dt.timedelta(days=amount)
    return dt.timedelta(weeks=amount)


def resolve_range(args: argparse.Namespace, local_tz: dt.tzinfo) -> dict[str, Any]:
    now_local = dt.datetime.now(tz=local_tz)
    mode_count = int(bool(args.preset)) + int(bool(args.last)) + int(bool(args.since or args.until))
    if mode_count > 1:
        raise ValueError("Use only one of preset, last, or since/until.")

    if mode_count == 0:
        args.preset = "today"

    if args.preset:
        current_date = now_local.date()
        if args.preset == "today":
            start_local = dt.datetime.combine(current_date, dt.time.min, tzinfo=local_tz)
            end_local = start_local + dt.timedelta(days=1)
        elif args.preset == "yesterday":
            end_local = dt.datetime.combine(current_date, dt.time.min, tzinfo=local_tz)
            start_local = end_local - dt.timedelta(days=1)
        elif args.preset == "last-7d":
            end_local = now_local
            start_local = end_local - dt.timedelta(days=7)
        elif args.preset == "last-30d":
            end_local = now_local
            start_local = end_local - dt.timedelta(days=30)
        elif args.preset == "this-month":
            start_local = dt.datetime.combine(current_date.replace(day=1), dt.time.min, tzinfo=local_tz)
            end_local = now_local
        else:
            this_month_start = dt.datetime.combine(current_date.replace(day=1), dt.time.min, tzinfo=local_tz)
            last_month_end = this_month_start
            last_month_date = (this_month_start - dt.timedelta(days=1)).date().replace(day=1)
            start_local = dt.datetime.combine(last_month_date, dt.time.min, tzinfo=local_tz)
            end_local = last_month_end
        label = args.preset
        return {
            "start_utc": start_local.astimezone(UTC),
            "end_utc": end_local.astimezone(UTC),
            "label": label,
        }

    if args.last:
        delta = parse_last_spec(args.last)
        end_local = now_local
        start_local = end_local - delta
        return {
            "start_utc": start_local.astimezone(UTC),
            "end_utc": end_local.astimezone(UTC),
            "label": f"last {args.last}",
        }

    start_utc = parse_isoish(args.since, local_tz, end_of_day=False) if args.since else dt.datetime(1970, 1, 1, tzinfo=UTC)
    end_utc = parse_isoish(args.until, local_tz, end_of_day=True) if args.until else now_local.astimezone(UTC)
    if end_utc <= start_utc:
        raise ValueError("until must be after since")
    label = f"{args.since or 'beginning'} -> {args.until or 'now'}"
    return {"start_utc": start_utc, "end_utc": end_utc, "label": label}


def parse_timestamp(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_loose_json(text: str) -> dict[str, Any] | None:
    stripped = text.lstrip("\ufeff\r\n\t ")
    decoder = json.JSONDecoder()
    try:
        parsed, _ = decoder.raw_decode(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def iter_jsonl(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                yield line_number, json.loads(raw)
            except json.JSONDecodeError:
                continue


def load_history_index(claude_dir: Path) -> dict[str, dict[str, Any]]:
    history_path = claude_dir / "history.jsonl"
    history: dict[str, dict[str, Any]] = {}
    if not history_path.exists():
        return history
    for _, item in iter_jsonl(history_path):
        session_id = item.get("sessionId")
        if not session_id:
            continue
        timestamp = item.get("timestamp", 0)
        display = item.get("display") or ""
        row = history.setdefault(
            session_id,
            {
                "timestamp": timestamp,
                "first_timestamp": timestamp,
                "last_timestamp": timestamp,
                "display": display,
                "first_display": display,
                "last_display": display,
                "displays": [],
                "project": item.get("project"),
                "message_count": 0,
            },
        )
        row["message_count"] += 1
        if display:
            row["displays"].append(display)
        if row.get("project") is None and item.get("project"):
            row["project"] = item.get("project")
        if timestamp <= row["first_timestamp"]:
            row["first_timestamp"] = timestamp
            row["timestamp"] = timestamp
            row["display"] = display
            row["first_display"] = display
        if timestamp >= row["last_timestamp"]:
            row["last_timestamp"] = timestamp
            row["last_display"] = display
    return history


def load_session_meta(claude_dir: Path) -> dict[str, dict[str, Any]]:
    meta_dir = claude_dir / "usage-data" / "session-meta"
    result: dict[str, dict[str, Any]] = {}
    if not meta_dir.exists():
        return result
    for path in sorted(meta_dir.glob("*.json")):
        parsed = parse_loose_json(path.read_text(encoding="utf-8"))
        if not parsed:
            continue
        session_id = parsed.get("session_id")
        if isinstance(session_id, str):
            result[session_id] = parsed
    return result


def load_facets(claude_dir: Path) -> dict[str, dict[str, Any]]:
    facets_dir = claude_dir / "usage-data" / "facets"
    result: dict[str, dict[str, Any]] = {}
    if not facets_dir.exists():
        return result
    for path in sorted(facets_dir.glob("*.json")):
        parsed = parse_loose_json(path.read_text(encoding="utf-8"))
        if not parsed:
            continue
        session_id = parsed.get("session_id")
        if isinstance(session_id, str):
            result[session_id] = parsed
    return result


def extract_tool_stats(content: Any) -> tuple[set[str], int]:
    names: set[str] = set()
    if not isinstance(content, list):
        return names, 0
    tool_call_count = 0
    for part in content:
        if isinstance(part, dict) and part.get("type") == "tool_use":
            tool_call_count += 1
            tool_name = part.get("name")
            if isinstance(tool_name, str):
                names.add(tool_name)
    return names, tool_call_count


def normalize_project_label(project_path: str | None) -> str:
    if not project_path:
        return "(unknown)"
    project_text = project_path.strip()
    if not project_text:
        return "(unknown)"
    if "/Desktop/project/" in project_text:
        return project_text.split("/Desktop/project/", 1)[1]
    return Path(project_text).name or project_text


def _extract_user_text(item: dict[str, Any]) -> str:
    msg = item.get("message", {})
    if isinstance(msg, str):
        return msg[:80]
    content = msg.get("content", []) if isinstance(msg, dict) else []
    if isinstance(content, str):
        return content[:80]
    for part in content if isinstance(content, list) else []:
        if isinstance(part, dict) and part.get("type") == "text":
            return (part.get("text") or "")[:80]
    return ""


def _extract_action_summary(message: dict[str, Any]) -> str:
    content = message.get("content", [])
    for part in content if isinstance(content, list) else []:
        if isinstance(part, dict) and part.get("type") == "tool_use":
            name = part.get("name", "")
            inp = part.get("input", {})
            hint = ""
            if isinstance(inp, dict):
                hint = inp.get("description", "") or inp.get("prompt", "") or inp.get("command", "") or inp.get("query", "") or inp.get("pattern", "") or ""
            return f"{name}({hint[:30]})" if hint else name
    for part in content if isinstance(content, list) else []:
        if isinstance(part, dict) and part.get("type") == "text":
            return (part.get("text") or "")[:50]
    return ""


def scan_requests(claude_dir: Path, time_range: dict[str, Any], local_tz: dt.tzinfo) -> list[dict[str, Any]]:
    projects_dir = claude_dir / "projects"
    requests: dict[tuple[str, str, str], dict[str, Any]] = {}
    if not projects_dir.exists():
        return []

    # Single pass: uuid_index populated as we go (user turns precede assistant turns in JSONL)
    uuid_index: dict[str, dict[str, Any]] = {}

    for path in sorted(projects_dir.rglob("*.jsonl")):
        source = "subagent" if "subagents" in path.parts else "main"
        parent_session_id = path.parent.parent.name if source == "subagent" else None
        for line_number, item in iter_jsonl(path):
            uid = item.get("uuid")
            if uid:
                uuid_index[uid] = item
            if item.get("type") != "assistant":
                continue
            message = item.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            timestamp = parse_timestamp(item.get("timestamp"))
            if timestamp is None:
                continue
            if timestamp < time_range["start_utc"] or timestamp >= time_range["end_utc"]:
                continue
            session_id = item.get("sessionId")
            if not isinstance(session_id, str):
                continue
            root_session_id = parent_session_id or session_id
            request_id = item.get("requestId") or message.get("id") or item.get("uuid") or f"{path}:{line_number}"
            key = (session_id, source, str(request_id))

            # Extract parent user text and action summary
            parent_uid = item.get("parentUuid")
            parent_item = uuid_index.get(parent_uid, {}) if parent_uid else {}
            parent_user_text = _extract_user_text(parent_item) if parent_item.get("type") == "user" else ""
            action_summary = _extract_action_summary(message)

            request = requests.setdefault(
                key,
                {
                    "session_id": root_session_id,
                    "raw_session_id": session_id,
                    "source": source,
                    "request_id": str(request_id),
                    "timestamp": timestamp,
                    "project_path": item.get("cwd"),
                    "model": message.get("model") or "(unknown)",
                    "tool_names": set(),
                    "tool_call_count": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "parent_user_text": parent_user_text,
                    "action_summary": action_summary,
                },
            )
            if timestamp < request["timestamp"]:
                request["timestamp"] = timestamp
            if not request.get("project_path") and item.get("cwd"):
                request["project_path"] = item.get("cwd")
            if not request.get("parent_user_text") and parent_user_text:
                request["parent_user_text"] = parent_user_text
            if not request.get("action_summary") and action_summary:
                request["action_summary"] = action_summary
            for field in TOKEN_FIELDS:
                request[field] = max(request[field], int(usage.get(field, 0) or 0))
            tool_names, tool_call_count = extract_tool_stats(message.get("content"))
            request["tool_names"].update(tool_names)
            request["tool_call_count"] = max(request["tool_call_count"], tool_call_count)

    rows = []
    for request in requests.values():
        request["total_tokens"] = sum(request[field] for field in TOKEN_FIELDS)
        request["local_dt"] = request["timestamp"].astimezone(local_tz)
        request["hour"] = request["local_dt"].hour
        request["project_label"] = normalize_project_label(request.get("project_path"))
        request["tool_names"] = sorted(request["tool_names"])
        rows.append(request)
    rows.sort(key=lambda row: row["timestamp"])
    return rows


def map_goal_category(raw_category: str) -> str:
    return GOAL_CATEGORY_MAP.get(raw_category, "other")


def classify_prompt(text: str) -> str:
    lowered = text.lower()
    for bucket, keywords in HEURISTIC_RULES:
        if any(keyword in lowered for keyword in keywords):
            return bucket
    return "other"


def is_meaningful_prompt(text: str) -> bool:
    normalized = text.strip().lower()
    if not normalized:
        return False
    if normalized in TRIVIAL_PROMPTS:
        return False
    if normalized.startswith("/"):
        return False
    if re.fullmatch(r"\d+", normalized):
        return False
    return True


def compute_task_weights(
    session_id: str,
    session_record: dict[str, Any],
    facet_record: dict[str, Any] | None,
    history_record: dict[str, Any] | None,
) -> dict[str, float]:
    goal_categories = facet_record.get("goal_categories") if isinstance(facet_record, dict) else None
    if isinstance(goal_categories, dict) and goal_categories:
        weighted = Counter()
        total_weight = 0.0
        for raw_category, raw_weight in goal_categories.items():
            try:
                numeric_weight = float(raw_weight)
            except (TypeError, ValueError):
                numeric_weight = 1.0
            numeric_weight = max(numeric_weight, 1.0)
            weighted[map_goal_category(str(raw_category))] += numeric_weight
            total_weight += numeric_weight
        if total_weight > 0:
            return {bucket: value / total_weight for bucket, value in weighted.items()}

    prompt = ""
    underlying_goal = str(facet_record.get("underlying_goal") or "") if isinstance(facet_record, dict) else ""
    if session_record.get("first_prompt"):
        prompt = str(session_record["first_prompt"])
    elif isinstance(history_record, dict) and history_record.get("display"):
        prompt = str(history_record["display"])
    elif underlying_goal:
        prompt = underlying_goal
    weighted = Counter()
    if isinstance(history_record, dict):
        for raw_prompt in history_record.get("displays", []):
            prompt_text = str(raw_prompt).strip()
            if is_meaningful_prompt(prompt_text):
                cat = classify_prompt(prompt_text)
                if cat != "other":
                    weighted[cat] += 1
    if not weighted and prompt:
        cat = classify_prompt(prompt)
        if cat == "other" and underlying_goal and is_meaningful_prompt(underlying_goal):
            cat = classify_prompt(underlying_goal)
        if cat != "other":
            weighted[cat] += 1
    total_weight = float(sum(weighted.values()))
    if total_weight <= 0:
        return {"other": 1.0}
    return {bucket: value / total_weight for bucket, value in weighted.items()}


def build_sessions(
    requests: list[dict[str, Any]],
    session_meta: dict[str, dict[str, Any]],
    facets: dict[str, dict[str, Any]],
    history: dict[str, dict[str, Any]],
    local_tz: dt.tzinfo,
) -> list[dict[str, Any]]:
    sessions: dict[str, dict[str, Any]] = {}

    for request in requests:
        session_id = request["session_id"]
        session = sessions.setdefault(
            session_id,
            {
                "session_id": session_id,
                "project_path": request.get("project_path"),
                "project_label": request["project_label"],
                "request_count": 0,
                "main_request_count": 0,
                "subagent_request_count": 0,
                "tool_names": set(),
                "tool_call_count": 0,
                "models": set(),
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "total_tokens": 0,
            },
        )
        if not session.get("project_path") and request.get("project_path"):
            session["project_path"] = request["project_path"]
            session["project_label"] = request["project_label"]
        session["request_count"] += 1
        if request["source"] == "subagent":
            session["subagent_request_count"] += 1
        else:
            session["main_request_count"] += 1
        session["tool_names"].update(request["tool_names"])
        session["tool_call_count"] += request["tool_call_count"]
        session["models"].add(request["model"])
        for field in TOKEN_FIELDS:
            session[field] += request[field]
        session["total_tokens"] += request["total_tokens"]

    # Pre-compute per-session min timestamp to avoid O(n·m) inner loop
    session_min_ts: dict[str, dt.datetime] = {}
    for req in requests:
        sid = req["session_id"]
        if sid not in session_min_ts or req["timestamp"] < session_min_ts[sid]:
            session_min_ts[sid] = req["timestamp"]

    enriched_rows = []
    for session_id, session in sessions.items():
        meta = session_meta.get(session_id, {})
        facet = facets.get(session_id, {})
        history_row = history.get(session_id, {})

        project_path = meta.get("project_path") or session.get("project_path") or history_row.get("project")
        start_time = parse_timestamp(meta.get("start_time")) or session_min_ts.get(session_id)
        local_start = start_time.astimezone(local_tz) if start_time else None
        tool_counts = meta.get("tool_counts") if isinstance(meta.get("tool_counts"), dict) else {}
        user_message_count = int(meta.get("user_message_count", 0) or history_row.get("message_count", 0) or 0)
        assistant_message_count = int(meta.get("assistant_message_count", 0) or session["main_request_count"] or 0)
        tool_call_count = sum(int(value) for value in tool_counts.values()) if tool_counts else session["tool_call_count"]
        prompt = meta.get("first_prompt") or history_row.get("first_display") or history_row.get("display") or facet.get("underlying_goal") or ""
        task_weights = compute_task_weights(session_id, meta, facet, history_row)

        has_meta = bool(meta)

        enriched = {
            **session,
            "project_path": project_path,
            "project_label": normalize_project_label(project_path),
            "start_time": start_time,
            "local_start": local_start,
            "duration_minutes": int(meta.get("duration_minutes", 0) or 0),
            "user_message_count": user_message_count,
            "assistant_message_count": assistant_message_count,
            "tool_call_count": tool_call_count,
            "tool_counts": tool_counts,
            "has_meta": has_meta,
            "files_modified": int(meta.get("files_modified", 0) or 0),
            "lines_added": int(meta.get("lines_added", 0) or 0),
            "lines_removed": int(meta.get("lines_removed", 0) or 0),
            "tool_errors": int(meta.get("tool_errors", 0) or 0),
            "user_interruptions": int(meta.get("user_interruptions", 0) or 0),
            "uses_task_agent": bool(meta.get("uses_task_agent", False)),
            "uses_mcp": bool(meta.get("uses_mcp", False)),
            "uses_web_search": bool(meta.get("uses_web_search", False)),
            "uses_web_fetch": bool(meta.get("uses_web_fetch", False)),
            "first_prompt": str(prompt),
            "underlying_goal": facet.get("underlying_goal") or "",
            "brief_summary": facet.get("brief_summary") or "",
            "outcome": facet.get("outcome") or "",
            "session_type": facet.get("session_type") or "",
            "goal_categories": facet.get("goal_categories") or {},
            "task_weights": task_weights,
            "task_primary": max(task_weights.items(), key=lambda item: item[1])[0] if task_weights else "other",
            "tool_names": sorted(session["tool_names"]),
            "models": sorted(session["models"]),
        }
        enriched_rows.append(enriched)

    enriched_rows.sort(key=lambda row: row["start_time"] or dt.datetime(1970, 1, 1, tzinfo=UTC))
    return enriched_rows


def metric_value_for_session(session: dict[str, Any], metric: str) -> float:
    if metric == "tokens":
        return float(session["total_tokens"])
    if metric == "requests":
        return float(session["request_count"])
    return 1.0


def aggregate_top(
    requests: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    *,
    by: str,
    metric: str,
) -> list[dict[str, Any]]:
    totals: defaultdict[str, float] = defaultdict(float)
    details: defaultdict[str, dict[str, Any]] = defaultdict(dict)

    if by == "task":
        for session in sessions:
            base_value = metric_value_for_session(session, metric)
            for bucket, weight in session["task_weights"].items():
                totals[bucket] += base_value * weight
                details[bucket]["label"] = CATEGORY_DISPLAY.get(bucket, bucket)
    elif by == "session":
        for session in sessions:
            sid = session["session_id"]
            prompt_hint = shorten_text(session["first_prompt"], 30) if session["first_prompt"] else ""
            display_label = f"{session['project_label']}: {prompt_hint}" if prompt_hint else session["project_label"]
            if metric == "tokens":
                totals[sid] += session["total_tokens"]
            elif metric == "requests":
                totals[sid] += session["request_count"]
            else:
                totals[sid] += 1
            details[sid] = {
                "label": display_label,
                "project_label": session["project_label"],
                "prompt": session["first_prompt"],
            }
    else:
        for request in requests:
            if by == "project":
                label = request["project_label"]
            elif by == "model":
                label = request["model"]
            elif by == "hour":
                label = f"{request['hour']:02d}:00"
            elif by == "source":
                label = request["source"]
            else:
                raise ValueError(f"Unsupported top dimension: {by}")

            if metric == "tokens":
                totals[label] += request["total_tokens"]
            elif metric == "requests":
                totals[label] += 1
            elif metric == "sessions":
                continue
            details[label]["label"] = label

        if metric == "sessions" and by in {"project", "model", "source"}:
            seen_pairs = set()
            for session in sessions:
                if by == "project":
                    labels = [session["project_label"]]
                elif by == "model":
                    labels = session["models"] or ["(unknown)"]
                else:
                    labels = []
                    if session["main_request_count"]:
                        labels.append("main")
                    if session["subagent_request_count"]:
                        labels.append("subagent")
                for label in labels:
                    key = (label, session["session_id"])
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)
                    totals[label] += 1
                    details[label]["label"] = label

    ordered = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    rows = []
    for label, value in ordered:
        row = {"key": label, "value": value, **details.get(label, {})}
        rows.append(row)
    return rows


def aggregate_by_hour(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    per_hour = {hour: {"tokens": 0, "requests": 0} for hour in range(24)}
    for request in requests:
        bucket = per_hour[request["hour"]]
        bucket["tokens"] += request["total_tokens"]
        bucket["requests"] += 1
    return [{"hour": hour, **per_hour[hour]} for hour in range(24)]


def aggregate_by_period(requests: list[dict[str, Any]], time_range: dict[str, Any], local_tz: dt.tzinfo) -> dict[str, Any]:
    """Auto-select granularity based on time span and aggregate accordingly."""
    start = time_range["start_utc"]
    end = time_range["end_utc"]
    span_days = (end - start).total_seconds() / 86400

    if span_days <= 1.5:
        # Hourly for single day
        buckets = aggregate_by_hour(requests)
        return {"granularity": "hour", "buckets": buckets}

    if span_days > 45:
        # Weekly for ranges over ~6 weeks
        return _aggregate_weekly(requests, start, end, local_tz)

    # Daily for anything between 1.5 and 45 days
    per_day: dict[str, dict[str, int]] = {}
    for req in requests:
        day_key = req["local_dt"].strftime("%m/%d")
        b = per_day.setdefault(day_key, {"label": day_key, "tokens": 0, "requests": 0})
        b["tokens"] += req["total_tokens"]
        b["requests"] += 1
    # Fill missing days
    cur = start.astimezone(local_tz).date()
    end_date = end.astimezone(local_tz).date()
    all_days = []
    while cur < end_date:
        key = cur.strftime("%m/%d")
        all_days.append(per_day.get(key, {"label": key, "tokens": 0, "requests": 0}))
        cur += dt.timedelta(days=1)
    return {"granularity": "day", "buckets": all_days}


def _aggregate_weekly(requests: list[dict[str, Any]], start: dt.datetime, end: dt.datetime, local_tz: dt.tzinfo) -> dict[str, Any]:
    """Aggregate into ISO weeks for long ranges."""
    per_week: dict[str, dict[str, Any]] = {}
    for req in requests:
        local_d = req["local_dt"].date()
        # Monday of the week
        week_start = local_d - dt.timedelta(days=local_d.weekday())
        key = week_start.isoformat()
        if key not in per_week:
            label = week_start.strftime("%m/%d")
            per_week[key] = {"label": label, "tokens": 0, "requests": 0, "_sort": week_start}
        per_week[key]["tokens"] += req["total_tokens"]
        per_week[key]["requests"] += 1

    # Fill missing weeks
    cur = start.astimezone(local_tz).date()
    cur = cur - dt.timedelta(days=cur.weekday())  # snap to Monday
    end_date = end.astimezone(local_tz).date()
    all_weeks = []
    while cur < end_date:
        key = cur.isoformat()
        if key in per_week:
            bucket = per_week[key]
            del bucket["_sort"]
            all_weeks.append(bucket)
        else:
            all_weeks.append({"label": cur.strftime("%m/%d"), "tokens": 0, "requests": 0})
        cur += dt.timedelta(days=7)
    return {"granularity": "week", "buckets": all_weeks}


def aggregate_heatmap(requests: list[dict[str, Any]], time_range: dict[str, Any], local_tz: dt.tzinfo) -> dict[str, Any]:
    """Build a date × hour heatmap matrix for multi-day views, filling missing days."""
    cells: dict[tuple[str, int], int] = defaultdict(int)
    for req in requests:
        day = req["local_dt"].strftime("%m/%d")
        hour = req["hour"]
        cells[(day, hour)] += req["total_tokens"]
    # Fill all days in range, not just days with data
    start_date = time_range["start_utc"].astimezone(local_tz).date()
    end_date = time_range["end_utc"].astimezone(local_tz).date()
    dates = []
    cur = start_date
    while cur < end_date:
        dates.append(cur.strftime("%m/%d"))
        cur += dt.timedelta(days=1)
    max_val = max(cells.values()) if cells else 1
    return {"dates": dates, "cells": cells, "max_val": max_val}


def aggregate_tool_calls(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: Counter = Counter()
    for session in sessions:
        tool_counts = session.get("tool_counts") or {}
        if tool_counts:
            for name, count in tool_counts.items():
                totals[name] += int(count)
        elif session.get("tool_names"):
            meta_tool_count = session.get("tool_call_count", 0)
            if meta_tool_count and len(session["tool_names"]) == 1:
                totals[session["tool_names"][0]] += meta_tool_count
            else:
                for name in session["tool_names"]:
                    totals[name] += 1
    rows = [{"tool": tool, "calls": value} for tool, value in totals.most_common()]
    return rows


def aggregate_tool_tokens(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate token usage by tool name from request-level data."""
    tool_tokens: defaultdict[str, int] = defaultdict(int)
    tool_calls: defaultdict[str, int] = defaultdict(int)
    for req in requests:
        names = req.get("tool_names", [])
        if not names:
            continue
        # Distribute this request's tokens across the tools it called
        per_tool = req["total_tokens"] / len(names) if names else 0
        for name in names:
            tool_tokens[name] += int(per_tool)
            tool_calls[name] += 1
    rows = []
    for tool, tokens in sorted(tool_tokens.items(), key=lambda x: -x[1]):
        rows.append({"tool": tool, "tokens": tokens, "calls": tool_calls[tool]})
    return rows


def build_summary_payload(
    requests: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    time_range: dict[str, Any],
    local_tz: dt.tzinfo,
    limit: int,
) -> dict[str, Any]:
    totals = {field: sum(request[field] for request in requests) for field in TOKEN_FIELDS}
    total_tokens = sum(totals.values())
    request_count = len(requests)
    session_count = len(sessions)
    user_messages = sum(session["user_message_count"] for session in sessions)
    tool_calls = sum(session["tool_call_count"] for session in sessions)
    subagent_requests = sum(session["subagent_request_count"] for session in sessions)
    main_requests = sum(session["main_request_count"] for session in sessions)

    by_hour = aggregate_by_hour(requests)
    activity = aggregate_by_period(requests, time_range, local_tz)
    heatmap = aggregate_heatmap(requests, time_range, local_tz) if activity["granularity"] != "hour" else None
    top_projects = aggregate_top(requests, sessions, by="project", metric="tokens")[:limit]
    top_tasks = aggregate_top(requests, sessions, by="task", metric="tokens")[:limit]
    top_models = aggregate_top(requests, sessions, by="model", metric="tokens")[:limit]
    top_hours = sorted(by_hour, key=lambda row: (-row["tokens"], row["hour"]))[:limit]
    top_sessions = aggregate_top(requests, sessions, by="session", metric="tokens")[:limit]
    tool_mix = aggregate_tool_calls(sessions)[:limit]
    tool_tokens = aggregate_tool_tokens(requests)[:20]

    # Request-level insights
    session_insights: dict[str, dict[str, Any]] = {}
    for session in sessions:
        sid = session["session_id"]
        session_reqs = [r for r in requests if r["session_id"] == sid]
        if not session_reqs:
            continue
        total_cache_write = sum(r["cache_creation_input_tokens"] for r in session_reqs)
        total_cache_read = sum(r["cache_read_input_tokens"] for r in session_reqs)
        total_input = total_cache_write + total_cache_read + sum(r["input_tokens"] for r in session_reqs)
        cache_hit_rate = total_cache_read / total_input if total_input > 0 else 0
        cold_starts = sum(1 for r in session_reqs if r["cache_read_input_tokens"] == 0 and r["total_tokens"] > 5000)
        max_single = max(r["total_tokens"] for r in session_reqs)
        avg_per_req = session["total_tokens"] / len(session_reqs) if session_reqs else 0
        session_insights[sid] = {
            "cache_hit_rate": cache_hit_rate,
            "cold_starts": cold_starts,
            "max_single_request": max_single,
            "avg_tokens_per_req": avg_per_req,
            "request_count": len(session_reqs),
        }

    def _format_cold_detail(c: dict[str, Any]) -> str:
        time = c["time"]
        tokens = format_number(c["cache_write"], compact=True)
        source = "↳子任务" if c["source"] == "subagent" else ""
        query = shorten_text(c.get("query", ""), 35)
        action = shorten_text(c.get("action", ""), 25)
        parts = [f"{time}"]
        if source:
            parts.append(source)
        parts.append(f"{tokens}")
        if query:
            parts.append(f'"{query}"')
        if action and action != query:
            parts.append(f"→ {action}")
        return " ".join(parts)

    # Collect cold start details per session, with query + action context
    cold_start_details: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for request in requests:
        if request["cache_read_input_tokens"] == 0 and request["total_tokens"] > 5000:
            sid = request["session_id"]
            tool_names = request.get("tool_names", [])
            first_tool = tool_names[0] if tool_names else ""
            query = request.get("parent_user_text", "")
            action = request.get("action_summary", first_tool)
            cold_start_details[sid].append({
                "time": request["local_dt"].strftime("%H:%M"),
                "cache_write": request["cache_creation_input_tokens"],
                "source": request["source"],
                "query": query,
                "action": action,
            })

    # Build structured insights: one row per session with issues
    insights = []
    # Pre-index requests by session for max request lookup
    requests_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for req in requests:
        requests_by_session[req["session_id"]].append(req)

    for session in sorted(sessions, key=lambda s: s["total_tokens"], reverse=True):
        sid = session["session_id"]
        si = session_insights.get(sid)
        if not si:
            continue
        cold_list = cold_start_details.get(sid, [])
        has_cold = len(cold_list) >= 3
        has_max = si.get("max_single_request", 0) > 300000
        if not has_cold and not has_max:
            continue

        project_label = session["project_label"]
        prompt_hint = shorten_text(session["first_prompt"], 40) if session["first_prompt"] else ""

        # Cold starts info
        cold_info = None
        if has_cold:
            total_cw = sum(c["cache_write"] for c in cold_list)
            cost = total_cw * DEFAULT_PRICING["cache_write"] / 1_000_000
            main_cold = [c for c in cold_list if c["source"] == "main"]
            sub_cold = [c for c in cold_list if c["source"] == "subagent"]
            parts = []
            if main_cold:
                parts.append(f"主 {len(main_cold)}")
            if sub_cold:
                parts.append(f"子 {len(sub_cold)}")
            cold_info = {
                "count": len(cold_list),
                "breakdown": " / ".join(parts),
                "tokens": format_number(total_cw, compact=True),
                "cost": f"${cost:.1f}",
                "details": [_format_cold_detail(c) for c in cold_list[:5]],
            }

        # Max request info with token breakdown
        max_info = None
        if has_max:
            session_reqs = requests_by_session.get(sid, [])
            if session_reqs:
                max_req = max(session_reqs, key=lambda r: r["total_tokens"])
                tot = max_req["total_tokens"] or 1
                max_info = {
                    "total": format_number(max_req["total_tokens"], compact=True),
                    "input": format_percent(max_req["input_tokens"], tot),
                    "output": format_percent(max_req["output_tokens"], tot),
                    "cache_write": format_percent(max_req["cache_creation_input_tokens"], tot),
                    "cache_read": format_percent(max_req["cache_read_input_tokens"], tot),
                }

        insights.append({
            "project": project_label,
            "session": prompt_hint,
            "cold": cold_info,
            "max_req": max_info,
        })
        if len(insights) >= limit:
            break

    return {
        "range": {
            "label": time_range["label"],
            "start_local": time_range["start_utc"].astimezone(local_tz).isoformat(),
            "end_local": time_range["end_utc"].astimezone(local_tz).isoformat(),
            "timezone": str(local_tz),
        },
        "overview": {
            **totals,
            "total_tokens": total_tokens,
            "estimated_cost_usd": estimate_cost_total(requests),
            "session_count": session_count,
            "request_count": request_count,
            "user_message_count": user_messages,
            "tool_call_count": tool_calls,
            "main_request_count": main_requests,
            "subagent_request_count": subagent_requests,
        },
        "top_projects": top_projects,
        "top_tasks": top_tasks,
        "top_models": top_models,
        "top_hours": top_hours,
        "top_sessions": top_sessions,
        "tool_mix": tool_mix,
        "tool_tokens": tool_tokens,
        "insights": insights,
        "session_insights": session_insights,
        "by_hour": by_hour,
        "activity": activity,
        "heatmap": heatmap,
        "sessions": sessions,
        "_requests": requests,
    }


def estimate_cost_for_request(req: dict[str, Any]) -> float:
    pricing = MODEL_PRICING.get(_model_family(req.get("model", "")), DEFAULT_PRICING)
    return (
        req.get("input_tokens", 0) * pricing["input"]
        + req.get("output_tokens", 0) * pricing["output"]
        + req.get("cache_creation_input_tokens", 0) * pricing["cache_write"]
        + req.get("cache_read_input_tokens", 0) * pricing["cache_read"]
    ) / 1_000_000


def estimate_cost_total(requests: list[dict[str, Any]]) -> float:
    return sum(estimate_cost_for_request(r) for r in requests)


def format_number(value: float, compact: bool = True) -> str:
    n = int(round(value))
    if compact and abs(n) >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if compact and abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if compact and abs(n) >= 1_000:
        return f"{n / 1_000:.1f}k"
    return f"{n:,}"


def format_percent(numerator: float, denominator: float) -> str:
    if denominator <= 0:
        return "0.0%"
    return f"{(numerator / denominator) * 100:.1f}%"


def shorten_text(text: str, width: int = 70) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= width:
        return compact
    return compact[: width - 1].rstrip() + "…"


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    align = "| " + " | ".join(headers) + " |\n"
    divider = "| " + " | ".join("---" for _ in headers) + " |\n"
    body = "".join("| " + " | ".join(row) + " |\n" for row in rows)
    return align + divider + body


def render_summary_markdown(payload: dict[str, Any], limit: int) -> str:
    overview = payload["overview"]
    total_tokens = overview["total_tokens"]

    lines = []
    lines.append("**Overview**")
    lines.append(f"- Range: `{payload['range']['start_local']}` -> `{payload['range']['end_local']}`")
    cost = overview.get("estimated_cost_usd", 0)
    lines.append(f"- Total tokens: `{format_number(total_tokens)}`")
    lines.append(
        f"- Sessions / Requests / User msgs: `{overview['session_count']}` / `{overview['request_count']}` / `{overview['user_message_count']}`"
    )
    lines.append(
        f"- Tool calls / Main req / Subagent req: `{overview['tool_call_count']}` / `{overview['main_request_count']}` / `{overview['subagent_request_count']}`"
    )
    lines.append(
        f"- Token mix: `input {format_percent(overview['input_tokens'], total_tokens)}` · `output {format_percent(overview['output_tokens'], total_tokens)}` · `cache write {format_percent(overview['cache_creation_input_tokens'], total_tokens)}` · `cache read {format_percent(overview['cache_read_input_tokens'], total_tokens)}`"
    )

    if payload["top_projects"]:
        rows = []
        for row in payload["top_projects"][:limit]:
            rows.append(
                [
                    row["label"],
                    format_number(row["value"]),
                    format_percent(row["value"], total_tokens),
                ]
            )
        lines.append("")
        lines.append("**Top Projects**")
        lines.append(markdown_table(["Project", "Tokens", "Share"], rows).rstrip())

    if payload["top_hours"]:
        rows = []
        for row in payload["top_hours"][:limit]:
            rows.append(
                [
                    f"{row['hour']:02d}:00",
                    format_number(row["tokens"]),
                    format_number(row["requests"]),
                ]
            )
        lines.append("")
        lines.append("**Top Hours**")
        lines.append(markdown_table(["Hour", "Tokens", "Requests"], rows).rstrip())

    if payload.get("insights"):
        lines.append("")
        lines.append("**Insights**")
        for row in payload["insights"][:limit]:
            project = row.get("project", "?")
            session = row.get("session", "")
            cold = row.get("cold")
            cold_str = f"cold starts: {cold['count']} ({cold['tokens']})" if cold else "no cold starts"
            lines.append(f"- `{project}` {session} · {cold_str}")

    return "\n".join(lines)


def render_top_markdown(
    rows: list[dict[str, Any]],
    *,
    by: str,
    metric: str,
    limit: int,
    requests: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
) -> str:
    total = 0.0
    if metric == "tokens":
        total = sum(request["total_tokens"] for request in requests)
    elif metric == "requests":
        total = float(len(requests))
    else:
        total = float(len(sessions))

    headers = [by.capitalize(), metric.capitalize(), "Share"]
    table_rows = []
    for row in rows[:limit]:
        label = row.get("label") or row.get("key") or ""
        if by == "session":
            label = f"{label} ({row.get('project_label', '(unknown)')})"
        table_rows.append(
            [
                shorten_text(label, 72),
                format_number(row["value"]),
                format_percent(row["value"], total),
            ]
        )

    lines = [f"**Top {by.capitalize()} By {metric.capitalize()}**"]
    lines.append(markdown_table(headers, table_rows).rstrip())
    return "\n".join(lines)


def html_bar(value: float, maximum: float) -> str:
    width = 0.0 if maximum <= 0 else (value / maximum) * 100
    return (
        '<div class="ibar">'
        f'<div class="ibar-fill" style="width:{width:.2f}%"></div>'
        f'<span class="ibar-text">{format_number(value, compact=True)}</span>'
        "</div>"
    )


def generate_insights_html(insights: list[dict[str, Any]]) -> str:
    if not insights:
        return ""
    rows = []
    for item in insights:
        cold = item.get("cold")
        max_req = item.get("max_req")

        # Cold Starts column
        if cold:
            detail_items = "".join(f"<div>{escape(d)}</div>" for d in cold.get("details", []))
            detail_html = f'<div class="insight-detail">{detail_items}</div>' if detail_items else ""
            cold_html = (
                f"<strong>{cold['count']}</strong> ({cold['breakdown']})<br>"
                f"<span style='color:var(--text-secondary)'>{cold['tokens']} ≈ {cold['cost']}</span>"
                f"{detail_html}"
            )
        else:
            cold_html = "<span style='color:var(--muted)'>—</span>"

        # Max Request column
        if max_req:
            max_html = (
                f"<strong>{max_req['total']}</strong><br>"
                f"<span style='color:var(--text-secondary);font-size:11px'>"
                f"Cache Write {max_req['cache_write']} · Cache Read {max_req['cache_read']} · "
                f"Input {max_req['input']} · Output {max_req['output']}"
                f"</span>"
            )
        else:
            max_html = "<span style='color:var(--muted)'>—</span>"

        rows.append(
            "<tr>"
            f"<td style='white-space:nowrap'>{escape(item['project'])}</td>"
            f"<td class='ellip' style='max-width:240px;color:var(--text-secondary)'>{escape(item['session'])}</td>"
            f"<td>{cold_html}</td>"
            f"<td>{max_html}</td>"
            "</tr>"
        )
    return (
        '    <section class="section">\n'
        '      <div class="section-head"><h2>Insights</h2></div>\n'
        '      <table><thead><tr>'
        '<th>Project</th><th>Session</th>'
        '<th>Cold Starts</th><th>Max Request</th>'
        '</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>\n'
        '    </section>\n'
    )


def generate_tool_tokens_html(tool_tokens: list[dict[str, Any]], total_tokens: float) -> str:
    if not tool_tokens:
        return "<tr><td colspan='4' style='color:var(--muted)'>No tool usage data</td></tr>"
    max_tokens = max(row["tokens"] for row in tool_tokens) if tool_tokens else 1
    rows = []
    for row in tool_tokens:
        rows.append(
            "<tr>"
            f"<td>{escape(row['tool'])}</td>"
            f"<td class='r'>{format_number(row['tokens'], compact=True)}</td>"
            f"<td class='r'>{format_number(row['calls'])}</td>"
            f"<td>{html_bar(row['tokens'], max_tokens)}</td>"
            "</tr>"
        )
    return "".join(rows)


def render_report_html(payload: dict[str, Any]) -> str:
    overview = payload["overview"]
    total_tokens = overview["total_tokens"]
    activity = payload.get("activity", {})
    act_buckets = activity.get("buckets", [])
    act_gran = activity.get("granularity", "hour")
    act_max = max((b["tokens"] for b in act_buckets), default=0)
    top_projects = payload["top_projects"][:10]
    sessions = sorted(payload["sessions"], key=lambda row: row["total_tokens"], reverse=True)[:30]

    def rows_to_html(rows: list[dict[str, Any]], label_key: str = "label", total_override: float = 0) -> str:
        total_value = total_override or sum(row["value"] for row in rows) or 1
        maximum = max((row["value"] for row in rows), default=0)
        rendered = []
        for row in rows:
            label = escape(str(row.get(label_key) or row.get("label") or row.get("key") or ""))
            width = 0.0 if maximum <= 0 else (row["value"] / maximum) * 100
            rendered.append(
                "<tr>"
                f"<td>{label}</td>"
                f"<td class='r'>{format_number(row['value'], compact=True)}</td>"
                f"<td class='r' style='color:var(--text-secondary)'>{format_percent(row['value'], total_value)}</td>"
                f"<td><div class='ibar'><div class='ibar-fill' style='width:{width:.2f}%'></div>"
                f"<span class='ibar-text'>{format_number(row['value'], compact=True)}</span></div></td>"
                "</tr>"
            )
        return "".join(rendered)

    # Adaptive activity chart — use log scale when range > 100x to keep small bars visible
    chart_cells = []
    num_cols = len(act_buckets)
    bar_height = 160
    act_min_nonzero = min((b["tokens"] for b in act_buckets if b["tokens"] > 0), default=0)
    use_log = act_max > 0 and act_min_nonzero > 0 and (act_max / act_min_nonzero) > 100
    log_max = math.log1p(act_max) if use_log else 0

    # Decide label thinning: show every Nth label when there are too many columns
    if num_cols <= 14:
        label_step = 1
    elif num_cols <= 30:
        label_step = 2
    elif num_cols <= 60:
        label_step = 4
    else:
        label_step = 7

    for idx, b in enumerate(act_buckets):
        if act_max == 0:
            height = 0
        elif use_log:
            height = int((math.log1p(b["tokens"]) / log_max) * bar_height) if b["tokens"] > 0 else 0
        else:
            height = int((b["tokens"] / act_max) * bar_height)
        # Ensure nonzero values get at least a visible bar
        if b["tokens"] > 0 and height < 6:
            height = 6
        tok = format_number(b['tokens'])
        req = b['requests']
        if act_gran == "hour":
            label = f"{b['hour']:02d}"
        else:
            label = b.get("label", "")
        show_label = (idx % label_step == 0)
        display_label = escape(label) if show_label else ""
        chart_cells.append(
            f"<div class='bar-col'>"
            f"<div class='bar-stick' style='height:{height}px'>"
            f"<div class='popup'><div class='pop-val'>{tok}</div><div class='pop-sub'>{req} requests</div></div>"
            f"</div>"
            f"<div class='bar-label'>{display_label}</div>"
            "</div>"
        )
    gran_labels = {"hour": "Hourly", "day": "Daily", "week": "Weekly"}
    gran_subtitle = gran_labels.get(act_gran, "")

    # Contribution graph for multi-day (GitHub style: X=hours, Y=dates, horizontal)
    heatmap_html = ""
    hm = payload.get("heatmap")
    if hm and hm["dates"]:
        dates = hm["dates"]
        cells = hm["cells"]
        max_v = hm["max_val"] or 1
        # Show all 24 hours
        hours = list(range(24))
        # Build rows: each row = one date, columns = hours
        cg_rows = []
        for d in dates:
            row_cells = [f"<div class='cg-date'>{escape(d)}</div>"]
            for hour in hours:
                val = cells.get((d, hour), 0)
                if val == 0:
                    level = 0
                else:
                    ratio = val / max_v
                    if ratio < 0.2: level = 1
                    elif ratio < 0.4: level = 2
                    elif ratio < 0.6: level = 3
                    elif ratio < 0.8: level = 4
                    else: level = 5
                tok = format_number(val)
                row_cells.append(f"<div class='cg-c cg-{level}' title='{d} {hour:02d}:00 — {tok}'></div>")
            cg_rows.append(f"<div class='cg-row'>{''.join(row_cells)}</div>")
        # Hour header
        hour_hdr = ["<div class='cg-date'></div>"] + [f"<div class='cg-hour'>{h:02d}</div>" for h in hours]
        heatmap_html = (
            '    <section class="section">\n'
            '      <div class="section-head">\n'
            '        <h2>Activity Pattern</h2>\n'
            '        <div class="sub">Daily hour distribution</div>\n'
            '      </div>\n'
            f'      <div class="cg-wrap"><div class="cg" style="grid-template-columns:48px repeat({len(hours)}, 1fr)">\n'
            f'        <div class="cg-row cg-header">{"".join(hour_hdr)}</div>\n'
            f'        {"".join(cg_rows)}\n'
            '      </div></div>\n'
            '    </section>\n'
        )

    session_insights = payload.get("session_insights", {})
    session_rows = []
    for row in sessions:
        si = session_insights.get(row["session_id"], {})
        cold = si.get("cold_starts", 0)
        cold_str = f"<span class='warn'>{cold}</span>" if cold >= 3 else str(cold)
        time_str = row['local_start'].strftime("%m/%d %H:%M") if row['local_start'] else ""
        inp = row.get("input_tokens", 0)
        out = row.get("output_tokens", 0)
        cr = row.get("cache_read_input_tokens", 0)
        session_rows.append(
            "<tr>"
            f"<td>{escape(time_str)}</td>"
            f"<td>{escape(row['project_label'])}</td>"
            f"<td class='ellip'>{escape(shorten_text(row['first_prompt'], 40))}</td>"
            f"<td class='r'>{format_number(row['total_tokens'], compact=True)}</td>"
            f"<td class='r' style='color:var(--text-secondary)'>{format_number(inp, compact=True)}</td>"
            f"<td class='r' style='color:var(--text-secondary)'>{format_number(out, compact=True)}</td>"
            f"<td class='r' style='color:var(--text-secondary)'>{format_number(cr, compact=True)}</td>"
            f"<td class='r'>{cold_str}</td>"
            "</tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Claude Usage Insight</title>
  <style>
    :root {{
      --bg: #000000;
      --surface: #0a0a0a;
      --card: #0f0f0f;
      --card-border: #1a1a1a;
      --card-glow: rgba(59, 130, 246, 0.04);
      --text: #e4e4e7;
      --text-secondary: #a1a1aa;
      --muted: #52525b;
      --line: #1e1e1e;
      --accent: #3b82f6;
      --accent-dim: rgba(59, 130, 246, 0.12);
      --accent-glow: rgba(59, 130, 246, 0.25);
      --green: #22c55e;
      --amber: #f59e0b;
      --radius: 16px;
      --radius-sm: 10px;
    }}
    * {{ box-sizing: border-box; margin: 0; }}
    body {{
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif;
      line-height: 1.5;
      -webkit-font-smoothing: antialiased;
    }}
    .page {{ max-width: 1280px; margin: 0 auto; padding: 40px 28px 60px; }}

    /* Header */
    .header {{ margin-bottom: 32px; }}
    .header h1 {{
      font-size: 28px;
      font-weight: 700;
      letter-spacing: -0.03em;
      background: linear-gradient(135deg, #e4e4e7 0%, #a1a1aa 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }}
    .header .meta {{
      color: var(--muted);
      font-size: 13px;
      margin-top: 6px;
      font-weight: 400;
    }}

    /* Stat Cards */
    .stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      margin-bottom: 28px;
    }}
    .stat {{
      background: var(--card);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-sm);
      padding: 16px 18px;
      position: relative;
      overflow: hidden;
    }}
    .stat::before {{
      content: '';
      position: absolute;
      top: 0; left: 0; right: 0;
      height: 1px;
      background: linear-gradient(90deg, transparent, var(--accent-dim), transparent);
    }}
    .stat .label {{
      font-size: 11px;
      font-weight: 500;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 8px;
    }}
    .stat .value {{
      font-size: 28px;
      font-weight: 700;
      letter-spacing: -0.03em;
      color: var(--text);
    }}
    .stat .value.highlight {{
      color: var(--accent);
    }}

    /* Section */
    .section {{
      background: var(--card);
      border: 1px solid var(--card-border);
      border-radius: var(--radius);
      padding: 24px;
      margin-bottom: 16px;
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      margin-bottom: 20px;
    }}
    .section-head h2 {{
      font-size: 16px;
      font-weight: 600;
      letter-spacing: -0.01em;
    }}
    .section-head .sub {{
      font-size: 12px;
      color: var(--muted);
    }}

    /* Activity Chart */
    .chart {{
      display: grid;
      gap: 4px;
      align-items: end;
      min-height: 200px;
      padding: 10px 0;
    }}
    .bar-col {{
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 8px;
      position: relative;
    }}
    .bar-stick {{
      width: 100%;
      min-height: 4px;
      border-radius: 6px 6px 2px 2px;
      background: linear-gradient(180deg, #60a5fa 0%, #2563eb 100%);
      transition: all 0.2s ease;
      position: relative;
    }}
    .bar-col:hover .bar-stick {{
      background: linear-gradient(180deg, #93c5fd 0%, #3b82f6 100%);
      box-shadow: 0 0 20px var(--accent-glow), 0 0 40px rgba(59,130,246,0.1);
    }}
    .bar-col .popup {{
      display: none;
      position: absolute;
      bottom: calc(100% + 12px);
      left: 50%;
      transform: translateX(-50%);
      background: #18181b;
      border: 1px solid var(--card-border);
      border-radius: 10px;
      padding: 10px 14px;
      font-size: 12px;
      line-height: 1.6;
      white-space: nowrap;
      text-align: center;
      z-index: 10;
      box-shadow: 0 8px 24px rgba(0,0,0,0.5);
      pointer-events: none;
    }}
    .bar-col .popup .pop-val {{ font-weight: 600; font-size: 14px; }}
    .bar-col .popup .pop-sub {{ color: var(--text-secondary); }}
    .bar-col:hover .popup {{ display: block; }}
    .bar-label {{
      font-size: 11px;
      color: var(--muted);
      font-variant-numeric: tabular-nums;
    }}

    /* Heatmap */
    .cg-wrap {{
      overflow-x: auto;
      scrollbar-width: none;
    }}
    .cg-wrap::-webkit-scrollbar {{
      display: none;
    }}
    .cg {{
      display: grid;
      gap: 3px;
    }}
    .cg-row {{
      display: grid;
      grid-template-columns: subgrid;
      grid-column: 1 / -1;
      gap: 3px;
    }}
    .cg-header {{ margin-bottom: 2px; }}
    .cg-date {{
      font-size: 11px;
      color: var(--muted);
      display: flex;
      align-items: center;
      justify-content: flex-end;
      padding-right: 8px;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }}
    .cg-hour {{
      font-size: 10px;
      color: var(--muted);
      text-align: center;
    }}
    .cg-c {{
      height: 20px;
      border-radius: 3px;
      transition: transform 0.1s, box-shadow 0.15s;
      cursor: default;
    }}
    .cg-c:hover {{
      transform: scale(1.2);
      box-shadow: 0 0 8px var(--accent-glow);
      z-index: 2;
      position: relative;
    }}
    .cg-0 {{ background: #161616; }}
    .cg-1 {{ background: #0a2652; }}
    .cg-2 {{ background: #0f3d80; }}
    .cg-3 {{ background: #1657b5; }}
    .cg-4 {{ background: #2563eb; }}
    .cg-5 {{ background: #60a5fa; }}

    /* Tables */
    table {{ width: 100%; border-collapse: collapse; }}
    th {{
      font-size: 11px;
      font-weight: 500;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.04em;
      text-align: left;
      padding: 0 0 12px;
      border-bottom: 1px solid var(--line);
    }}
    th.r {{ text-align: right; }}
    td {{
      padding: 12px 0;
      font-size: 13px;
      border-bottom: 1px solid #111;
      vertical-align: middle;
    }}
    td.r {{ text-align: right; font-variant-numeric: tabular-nums; }}
    tr:hover td {{ background: rgba(255,255,255,0.015); }}
    .ellip {{ max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-secondary); }}

    /* Inline bar */
    .ibar {{
      position: relative;
      height: 20px;
      min-width: 140px;
      background: var(--accent-dim);
      border-radius: 99px;
      overflow: hidden;
    }}
    .ibar-fill {{
      position: absolute;
      inset: 0 auto 0 0;
      background: linear-gradient(90deg, #2563eb 0%, #60a5fa 100%);
      border-radius: 99px;
    }}
    .ibar-text {{
      position: relative;
      z-index: 1;
      padding: 0 10px;
      font-size: 11px;
      font-weight: 500;
      line-height: 20px;
      color: var(--text);
      font-variant-numeric: tabular-nums;
    }}

    /* Warning / Tooltip */
    .warn {{ color: var(--amber); font-weight: 600; }}

    /* Session cards */
    .s-list {{
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .s-card {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: var(--radius-sm);
      padding: 14px 18px;
      transition: border-color 0.15s;
    }}
    .s-card:hover {{
      border-color: var(--card-border);
    }}
    .s-head {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      margin-bottom: 4px;
    }}
    .s-project {{
      font-weight: 600;
      font-size: 14px;
    }}
    .s-time {{
      font-size: 12px;
      color: var(--muted);
      font-variant-numeric: tabular-nums;
    }}
    .s-prompt {{
      font-size: 13px;
      color: var(--text-secondary);
      margin-bottom: 10px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .s-metrics {{
      display: flex;
      gap: 16px;
      align-items: center;
      flex-wrap: wrap;
    }}
    .s-metric {{
      font-size: 13px;
      font-variant-numeric: tabular-nums;
    }}
    .s-metric-label {{
      font-size: 11px;
      color: var(--muted);
      margin-right: 4px;
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }}
    .alert-tag {{
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 4px;
      background: var(--accent-dim);
      color: var(--accent);
    }}
    .warn-tag {{
      background: rgba(245, 158, 11, 0.12);
      color: var(--amber);
    }}
    .tip {{ position: relative; cursor: help; border-bottom: 1px dotted var(--muted); }}
    .tip .tip-box {{
      display: none;
      position: absolute;
      bottom: calc(100% + 6px);
      left: 50%;
      transform: translateX(-50%);
      background: #18181b;
      border: 1px solid var(--card-border);
      border-radius: 10px;
      padding: 12px 16px;
      font-size: 12px;
      line-height: 1.6;
      width: 280px;
      white-space: normal;
      text-align: left;
      font-weight: 400;
      z-index: 20;
      box-shadow: 0 8px 24px rgba(0,0,0,0.5);
      text-transform: none;
      letter-spacing: 0;
    }}
    .tip:hover .tip-box {{ display: block; }}

    /* Insight detail rows */
    .insight-detail {{
      margin-top: 8px;
      padding-left: 0;
    }}
    .insight-detail div {{
      padding: 3px 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
      font-variant-numeric: tabular-nums;
    }}
  </style>
</head>
<body>
<div class="page">

  <div class="header">
    <h1>Claude Usage Insight</h1>
    <div class="meta">{escape(payload['range']['start_local'][:10])} ~ {escape(payload['range']['end_local'][:10])}</div>
  </div>

  <div class="stats">
    <div class="stat"><div class="label">Est. Cost</div><div class="value highlight">${overview.get('estimated_cost_usd', 0):.2f}</div></div>
    <div class="stat"><div class="label">Tokens</div><div class="value">{format_number(total_tokens)}</div></div>
    <div class="stat"><div class="label">Sessions</div><div class="value">{format_number(overview['session_count'])}</div></div>
    <div class="stat"><div class="label">Requests</div><div class="value">{format_number(overview['request_count'])}</div></div>
    <div class="stat"><div class="label">Cache Read</div><div class="value" style="color:var(--green)">{format_percent(overview['cache_read_input_tokens'], total_tokens)}</div></div>
  </div>

  <section class="section">
    <div class="section-head">
      <h2>Activity</h2>
      <div class="sub">{gran_subtitle} token volume</div>
    </div>
    <div class="chart" style="grid-template-columns:repeat({num_cols}, 1fr)">{''.join(chart_cells)}</div>
  </section>

{heatmap_html}

  <section class="section">
    <div class="section-head"><h2>Projects</h2></div>
    <table><thead><tr><th>Project</th><th class="r">Tokens</th><th class="r">Share</th><th style="min-width:140px"></th></tr></thead><tbody>{rows_to_html(top_projects, total_override=total_tokens)}</tbody></table>
  </section>

  <section class="section">
    <div class="section-head"><h2>Sessions</h2><div class="sub">Top {len(sessions)} by token volume</div></div>
    <table>
      <thead>
        <tr>
          <th>Time</th>
          <th>Project</th>
          <th>Prompt</th>
          <th class="r">Total</th>
          <th class="r">Input</th>
          <th class="r">Output</th>
          <th class="r">Cache</th>
          <th class="r"><span class="tip">Cold &#8505;<span class="tip-box">Cold Start：缓存失效后重新构建上下文的次数。<br>次数越多 → 上下文切换越频繁 → token 浪费越大。</span></span></th>
        </tr>
      </thead>
      <tbody>{''.join(session_rows)}</tbody>
    </table>
  </section>

{generate_insights_html(payload.get("insights", []))}

</div>
</body>
</html>"""


def write_report(html_text: str, claude_dir: Path, output: str | None, time_range: dict[str, Any] | None = None) -> Path:
    if output:
        path = Path(output).expanduser().resolve()
    else:
        reports_dir = claude_dir / "usage-data" / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        # Name by date range so same-range re-runs overwrite instead of piling up
        if time_range:
            label = time_range.get("label", "")
            if label in ("today", "yesterday", "this-month", "last-month", "last-7d", "last-30d") or label.startswith("last "):
                slug = label.replace(" ", "-")
            else:
                local_tz = dt.datetime.now().astimezone().tzinfo or UTC
                start_str = time_range["start_utc"].astimezone(local_tz).strftime("%Y-%m-%d")
                end_dt = time_range["end_utc"] - dt.timedelta(seconds=1)
                end_str = end_dt.astimezone(local_tz).strftime("%Y-%m-%d")
                if start_str == end_str:
                    slug = start_str
                else:
                    slug = f"{start_str}_to_{end_str}"
        else:
            slug = dt.datetime.now().strftime("%Y-%m-%d")
        path = reports_dir / f"claude-usage-{slug}.html"
    path.write_text(html_text, encoding="utf-8")
    return path


def emit_json(payload: dict[str, Any]) -> None:
    def default(value: Any) -> Any:
        if isinstance(value, dt.datetime):
            return value.isoformat()
        if isinstance(value, set):
            return sorted(value)
        raise TypeError(f"Unsupported type: {type(value)!r}")

    print(json.dumps(payload, ensure_ascii=False, indent=2, default=default))


def main() -> int:
    args = parse_args()
    try:
        local_tz = get_timezone(args.timezone)
        time_range = resolve_range(args, local_tz)
    except Exception as error:
        print(f"[error] {error}", file=sys.stderr)
        return 2

    claude_dir = Path(args.claude_dir).expanduser().resolve()
    if not claude_dir.exists():
        print(f"[error] Claude directory not found: {claude_dir}", file=sys.stderr)
        return 2

    history = load_history_index(claude_dir)
    session_meta = load_session_meta(claude_dir)
    facets = load_facets(claude_dir)
    requests = scan_requests(claude_dir, time_range, local_tz)
    sessions = build_sessions(requests, session_meta, facets, history, local_tz)

    if args.command == "summary":
        payload = build_summary_payload(requests, sessions, time_range, local_tz, args.limit)
        if args.format == "json":
            emit_json(payload)
        else:
            print(render_summary_markdown(payload, args.limit))
        return 0

    if args.command == "top":
        rows = aggregate_top(requests, sessions, by=args.by, metric=args.metric)
        payload = {
            "range": {
                "label": time_range["label"],
                "start_local": time_range["start_utc"].astimezone(local_tz).isoformat(),
                "end_local": time_range["end_utc"].astimezone(local_tz).isoformat(),
                "timezone": str(local_tz),
            },
            "by": args.by,
            "metric": args.metric,
            "rows": rows[: args.limit],
        }
        if args.format == "json":
            emit_json(payload)
        else:
            print(render_top_markdown(rows, by=args.by, metric=args.metric, limit=args.limit, requests=requests, sessions=sessions))
        return 0

    if args.command == "report":
        payload = build_summary_payload(requests, sessions, time_range, local_tz, limit=10)
        report_path = write_report(render_report_html(payload), claude_dir, args.output, time_range)
        if args.format == "json":
            emit_json(
                {
                    "report_path": str(report_path),
                    "range": payload["range"],
                    "overview": payload["overview"],
                }
            )
        else:
            print(f"Report written to `{report_path}`")
            # Auto-open on macOS
            import platform
            if platform.system() == "Darwin":
                import subprocess
                subprocess.Popen(["open", str(report_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return 0

    print(f"[error] Unknown command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
