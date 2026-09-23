"""计划任务相位定义与状态读取（标记文件优先，日志仅作回退）。"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from quantpy.paths import LOG_DIR, SCHEDULER_PHASES_FILE, SCHEDULER_STATUS_FILE


def load_phases_config() -> dict:
    """读取 scripts/phases.json；缺失时回退内置默认。"""
    default = {
        "task_prefix": "QuantPyStock",
        "phases": [
            {"id": "morning", "task_name": "Morning", "label": "早盘 9:35", "time": "09:35", "timeout_hours": 2},
            {"id": "ma20-am", "task_name": "Ma20Am", "label": "MA20回踩 11:00", "time": "11:00", "timeout_hours": 1},
            {"id": "ma20-pm", "task_name": "Ma20Pm", "label": "MA20回踩 13:30", "time": "13:30", "timeout_hours": 1},
            {"id": "triple-volume", "task_name": "TripleVolume", "label": "三倍量 14:45", "time": "14:45", "timeout_hours": 2},
            {"id": "close", "task_name": "Close", "label": "收盘 15:10", "time": "15:10", "timeout_hours": 2},
            {"id": "report", "task_name": "Report", "label": "日报 15:25", "time": "15:25", "timeout_hours": 2},
        ],
    }
    path = SCHEDULER_PHASES_FILE
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    if not isinstance(data, dict) or not isinstance(data.get("phases"), list):
        return default
    return data


def list_phases() -> List[dict]:
    cfg = load_phases_config()
    return [p for p in cfg.get("phases", []) if isinstance(p, dict) and p.get("id")]


def phase_labels() -> Dict[str, str]:
    return {str(p["id"]): str(p.get("label") or p["id"]) for p in list_phases()}


def _task_full_name(task_name: str, prefix: Optional[str] = None) -> str:
    cfg = load_phases_config()
    pref = prefix or str(cfg.get("task_prefix") or "QuantPyStock")
    return f"{pref}-{task_name}"


def is_scheduled_task_registered(task_name: str) -> Optional[bool]:
    """查询 Windows 计划任务是否已注册。非 Windows 或查询失败返回 None。"""
    full = _task_full_name(task_name)
    try:
        proc = subprocess.run(
            ["schtasks", "/Query", "/TN", full],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.returncode == 0


def read_status_markers() -> dict:
    path = SCHEDULER_STATUS_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_status_marker(
    phase: str,
    state: str,
    *,
    message: str = "",
    log: str = "",
) -> None:
    """供 Python 侧写入标记（PS1 runner 仍直接写 JSON）。"""
    all_markers = read_status_markers()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prev = all_markers.get(phase) if isinstance(all_markers.get(phase), dict) else {}
    started = now
    if state != "running" and prev.get("started_at"):
        started = str(prev["started_at"])
    all_markers[phase] = {
        "phase": phase,
        "state": state,
        "started_at": started,
        "finished_at": None if state == "running" else now,
        "ok": state == "ok",
        "message": message,
        "log": log,
        "updated_at": now,
    }
    SCHEDULER_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULER_STATUS_FILE.write_text(
        json.dumps(all_markers, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _from_marker(phase: str, label: str, marker: dict) -> dict:
    state = str(marker.get("state") or "")
    started = str(marker.get("started_at") or "")
    finished = str(marker.get("finished_at") or "")
    last_run = finished or started
    base = {
        "label": label,
        "last_run": last_run,
        "log": str(marker.get("log") or ""),
        "message": str(marker.get("message") or ""),
        "state": state or "idle",
    }
    if state == "running":
        return {
            **base,
            "last_run": started or last_run,
            "ok": False,
            "running": True,
            "message": base["message"] or "运行中",
        }
    if state == "ok":
        return {**base, "ok": True, "running": False}
    if state == "fail":
        return {
            **base,
            "ok": False,
            "running": False,
            "message": base["message"] or "失败",
        }
    return {
        **base,
        "ok": False,
        "running": False,
        "state": "idle",
        "message": base["message"] or "尚未执行",
    }


def _from_log_fallback(phase: str, label: str) -> dict:
    logs = sorted(LOG_DIR.glob(f"daily_{phase}_*.log"), reverse=True)
    if not logs:
        return {
            "label": label,
            "last_run": "",
            "ok": False,
            "running": False,
            "state": "idle",
            "log": "",
            "message": "尚未执行",
        }
    path = logs[0]
    text = ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    started = "Start " in text
    done = f"Done {phase}" in text or "Done " in text
    has_body = len(text.strip()) > 120
    success_hint = (
        "报告已保存" in text
        or "扫描完成" in text
        or "完成，共" in text
        or "观察池评估" in text
        or "三倍量选股完成" in text
    )
    running = started and not done and has_body
    ok = bool(done and (has_body or success_hint)) or (success_hint and done)
    if started and not done and not has_body:
        ok = False
        running = False
    return {
        "label": label,
        "last_run": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "ok": ok,
        "running": running,
        "state": "running" if running else ("ok" if ok else "fail"),
        "log": path.name,
        "message": "运行中" if running else ("" if ok else "未完成"),
    }


def load_scheduler_status() -> dict:
    """仪表盘用：优先 data/scheduler_status.json，回退日志启发式；并标注任务是否已注册。"""
    markers = read_status_markers()
    status: Dict[str, Any] = {}
    for phase_def in list_phases():
        phase = str(phase_def["id"])
        label = str(phase_def.get("label") or phase)
        task_name = str(phase_def.get("task_name") or phase)
        registered = is_scheduled_task_registered(task_name)

        marker = markers.get(phase) if isinstance(markers, dict) else None
        if isinstance(marker, dict) and marker.get("state"):
            entry = _from_marker(phase, label, marker)
        else:
            entry = _from_log_fallback(phase, label)

        entry["registered"] = registered
        entry["task_name"] = _task_full_name(task_name)
        if registered is False and entry.get("state") in ("idle", "fail") and not entry.get("running"):
            # 避免「未运行」掩盖「从未注册」
            if not entry.get("last_run") or entry.get("message") in ("", "尚未执行", "未完成"):
                entry["message"] = "计划任务未注册"
                entry["state"] = "unregistered"
        status[phase] = entry
    return status
