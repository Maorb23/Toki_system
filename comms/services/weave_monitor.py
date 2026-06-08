from __future__ import annotations

import hashlib
import logging
import os
import threading
from datetime import datetime, timezone as datetime_timezone
from typing import Any, Callable, TypeVar

from django.utils import timezone

logger = logging.getLogger(__name__)

T = TypeVar("T")

_UNSET = object()
_WEAVE_CLIENT: Any = _UNSET
_INIT_LOCK = threading.Lock()


def weave_enabled() -> bool:
    return os.getenv("WEAVE_TRACING", "").strip().lower() in {"1", "true", "yes", "y", "on"}


def weave_project() -> str:
    return (
        os.getenv("WEAVE_PROJECT")
        or os.getenv("WANDB_PROJECT")
        or "communication-agent"
    )


def weave_log_content() -> bool:
    return os.getenv("WEAVE_LOG_CONTENT", "").strip().lower() in {"1", "true", "yes", "y", "on"}


def content_trace_fields(**fields: Any) -> dict[str, Any]:
    if not weave_log_content():
        return {}
    return {"content": _json_safe(_truncate_content(fields))}


def _weave_module():
    global _WEAVE_CLIENT
    if _WEAVE_CLIENT is not _UNSET:
        return _WEAVE_CLIENT

    with _INIT_LOCK:
        if _WEAVE_CLIENT is not _UNSET:
            return _WEAVE_CLIENT
        _WEAVE_CLIENT = _initialize_weave()
        return _WEAVE_CLIENT


def clear_weave_cache() -> None:
    global _WEAVE_CLIENT
    with _INIT_LOCK:
        _WEAVE_CLIENT = _UNSET


def _initialize_weave():
    if not weave_enabled():
        return None

    try:
        import weave
    except ImportError:
        logger.warning("WEAVE_TRACING is enabled, but the weave package is not installed.")
        return None

    try:
        weave.init(weave_project())
    except Exception as exc:
        if _is_duplicate_project_init_error(exc):
            logger.warning(
                "W&B Weave reported a duplicate project upsert during init; continuing with tracing: %s",
                exc,
            )
            return weave
        logger.warning("Could not initialize W&B Weave tracing: %s", exc)
        return None

    return weave


def _is_duplicate_project_init_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "duplicate entry" in message
        and "projects" in message
        and ("upsertmodel" in message or "ix_projects_name_entity_id" in message)
    )


def trace_operation(
    name: str,
    inputs: dict[str, Any],
    operation: Callable[[], T],
    *,
    output: Callable[[T], dict[str, Any]] | None = None,
) -> T:
    started_at = _trace_time_fields()
    safe_inputs = _safe_inputs({**started_at, **inputs})
    weave = _weave_module()
    if weave is None:
        return operation()

    result_box: dict[str, T] = {}

    @weave.op(name=name)
    def _run(payload: dict[str, Any]) -> dict[str, Any]:
        result = operation()
        result_box["result"] = result
        payload_output = output(result) if output else _safe_output(result)
        return {"result": payload_output}

    _run(safe_inputs)
    return result_box["result"]


def _trace_time_fields() -> dict[str, Any]:
    utc_now = datetime.now(datetime_timezone.utc)
    try:
        local_now = timezone.localtime(utc_now)
        local_timezone = str(timezone.get_current_timezone())
    except Exception:
        local_now = utc_now
        local_timezone = "UTC"
    return {
        "trace_started_at": local_now.isoformat(),
        "trace_started_at_utc": utc_now.isoformat(),
        "trace_timezone": local_timezone,
    }


def graph_trace_inputs(*, state: dict[str, Any], sender: str, receiver: str, company_id: int | str | None) -> dict[str, Any]:
    message = state.get("original_message") or ""
    return {
        "sender": sender,
        "receiver": receiver,
        "company_id": company_id,
        "message_length": len(message),
        **content_trace_fields(original_message=message),
    }


def graph_trace_output(state: dict[str, Any]) -> dict[str, Any]:
    metadata = state.get("metadata") or {}
    return {
        "route": metadata.get("route"),
        "nodes_executed": metadata.get("nodes_executed", []),
        "tools_called": metadata.get("tools_called", []),
        "used_llm": metadata.get("used_llm", False),
        "used_tools": metadata.get("used_tools", False),
        "validator_passed": metadata.get("validator_passed"),
        "total_latency_ms": metadata.get("total_latency_ms"),
        "error_count": len(metadata.get("errors") or []),
        "message_id": state.get("message_id"),
    }


def execution_summary_trace_inputs(state: dict[str, Any]) -> dict[str, Any]:
    metadata = state.get("metadata") or {}
    return {
        "message_id": state.get("message_id"),
        "route": state.get("route") or metadata.get("route"),
        "node_count": len(metadata.get("nodes_executed") or []),
        "tool_count": len(metadata.get("tools_called") or []),
        "used_llm": metadata.get("used_llm", False),
        "used_tools": metadata.get("used_tools", False),
    }


def execution_summary_trace_output(state: dict[str, Any]) -> dict[str, Any]:
    metadata = state.get("metadata") or {}
    return {
        "message_id": state.get("message_id"),
        "route": state.get("route") or metadata.get("route"),
        "nodes_executed": metadata.get("nodes_executed", []),
        "tools_called": metadata.get("tools_called", []),
        "latency_ms_by_node": metadata.get("latency_ms_by_node", {}),
        "total_latency_ms": metadata.get("total_latency_ms"),
        "used_llm": metadata.get("used_llm", False),
        "used_tools": metadata.get("used_tools", False),
        "validator_passed": metadata.get("validator_passed"),
        "validator_issues": metadata.get("validator_issues", []),
        "error_count": len(metadata.get("errors") or []),
        "tool_result_summaries": {
            str(tool_name): _summarize_tool_result(result)
            for tool_name, result in (state.get("tool_results") or {}).items()
        },
    }


def node_trace_inputs(node_name: str, state: dict[str, Any]) -> dict[str, Any]:
    metadata = state.get("metadata") or {}
    message = state.get("normalized_message") or state.get("original_message") or ""
    return {
        "node": node_name,
        "route": state.get("route") or metadata.get("route"),
        "message_length": len(message),
        "company_id": state.get("company_id"),
        "selected_tools": state.get("selected_tools") or [],
    }


def node_trace_output(state: dict[str, Any]) -> dict[str, Any]:
    metadata = state.get("metadata") or {}
    return {
        "route": state.get("route") or metadata.get("route"),
        "used_llm": metadata.get("used_llm", False),
        "used_tools": metadata.get("used_tools", False),
        "tools_called": metadata.get("tools_called", []),
        "errors": metadata.get("errors", []),
        "message_id": state.get("message_id"),
    }


def tool_trace_inputs(
    tool_name: str,
    *,
    company_id: int | str | None,
    receiver: int | str | None,
    message: str,
    has_preference: bool = False,
) -> dict[str, Any]:
    return {
        "tool": tool_name,
        "company_id": company_id,
        "receiver": receiver,
        "message_length": len(message or ""),
        "message_hash": _short_hash(message or ""),
        "has_preference": has_preference,
        **content_trace_fields(message=message or ""),
    }


def tool_trace_output(result: Any) -> dict[str, Any]:
    return _summarize_tool_result(result)


def _safe_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    return _json_safe(inputs)


def _safe_output(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return _json_safe(value)
    return {"value": repr(value)}


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _content_limit() -> int:
    try:
        return max(100, int(os.getenv("WEAVE_LOG_CONTENT_MAX_CHARS", "1000")))
    except ValueError:
        return 1000


def _truncate_content(value: Any) -> Any:
    limit = _content_limit()
    if isinstance(value, dict):
        return {str(key): _truncate_content(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_truncate_content(item) for item in value]
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return f"{value[:limit]}...[truncated]"
    return value


def _summarize_tool_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        summary: dict[str, Any] = {
            "type": "dict",
            "keys": sorted(str(key) for key in result.keys())[:25],
        }
        for key in ("status", "reason", "receiver", "type"):
            if key in result and (isinstance(result[key], (str, int, float, bool)) or result[key] is None):
                summary[key] = result.get(key)
        for key, value in result.items():
            if isinstance(value, (list, tuple, set)):
                summary[f"{key}_count"] = len(value)
            elif isinstance(value, dict):
                summary[f"{key}_keys"] = sorted(str(item) for item in value.keys())[:25]
        return summary

    if isinstance(result, (list, tuple, set)):
        items = list(result)
        summary = {
            "type": "list",
            "count": len(items),
        }
        if items and isinstance(items[0], dict):
            summary["first_item_keys"] = sorted(str(key) for key in items[0].keys())[:25]
        return summary

    return {
        "type": type(result).__name__,
        "value_preview": repr(result)[:200],
    }
