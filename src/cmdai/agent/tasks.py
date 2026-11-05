import os
import time
import subprocess
import tempfile
from typing import Dict, Any, Optional, List


class BackgroundTask:
    def __init__(self, task_id: str, cmd: str, proc: subprocess.Popen, log_path: str, session_id: str = ""):
        self.task_id = task_id
        self.cmd = cmd
        self.proc = proc
        self.log_path = log_path
        self.session_id = session_id
        self.start_time = time.time()

    def is_running(self) -> bool:
        return self.proc.poll() is None

    def poll(self) -> Optional[int]:
        return self.proc.poll()

    def kill(self) -> bool:
        try:
            if self.is_running():
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                return True
        except Exception:
            pass
        return False

    def read_logs(self, max_lines: int = 50) -> str:
        try:
            if os.path.exists(self.log_path):
                with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                    return "".join(lines[-max_lines:])
        except Exception:
            pass
        return ""


class BackgroundTaskManager:
    _instance: Optional["BackgroundTaskManager"] = None

    def __init__(self):
        self.tasks: Dict[str, BackgroundTask] = {}
        self.subagents: Dict[str, Dict[str, Any]] = {}
        self._notified_tasks: set = set()
        self._counter: int = 0

    @classmethod
    def get_instance(cls) -> "BackgroundTaskManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def has_finished_notifications(self, session_id: str = "") -> bool:
        for tid, t in list(self.tasks.items()):
            if session_id and str(getattr(t, "session_id", "") or "") != session_id:
                continue
            if not t.is_running() and tid not in self._notified_tasks:
                return True
        for sid, s in list(self.subagents.items()):
            if session_id and str(s.get("session_id") or "") != session_id:
                continue
            if s.get("background") and s.get("status") != "running" and sid not in self._notified_tasks:
                return True
        return False

    def get_finished_notifications(self, session_id: str = "") -> List[str]:
        notifications = []
        for tid, t in list(self.tasks.items()):
            if session_id and str(getattr(t, "session_id", "") or "") != session_id:
                continue
            if not t.is_running() and tid not in self._notified_tasks:
                self._notified_tasks.add(tid)
                code = t.poll()
                logs = t.read_logs(max_lines=40)
                msg = (
                    f'<tool_response name="background_task" id="{tid}">\n'
                    f"[Background command '{t.cmd}' ({tid}) has finished with exit code {code}]\n"
                )
                if logs.strip():
                    msg += f"Output:\n{logs.strip()}\n"
                msg += "Please report this background command result to the user.\n</tool_response>"
                notifications.append(msg)
        for sid, s in list(self.subagents.items()):
            if session_id and str(s.get("session_id") or "") != session_id:
                continue
            if s.get("background") and s.get("status") != "running" and sid not in self._notified_tasks:
                self._notified_tasks.add(sid)
                role = s.get("role", "Subagent")
                status = s.get("status", "finished")
                res = s.get("result") or ""
                msg = (
                    f'<tool_response name="background_subagent" id="{sid}">\n'
                    f"[Background subagent '{role}' ({sid}) has {status}]\n"
                )
                if str(res).strip():
                    msg += f"Report:\n{str(res).strip()}\n"
                msg += "Please summarize and report this background subagent result to the user.\n</tool_response>"
                notifications.append(msg)
        return notifications

    def spawn(self, cmd: str, cwd: str, session_id: str = "") -> Dict[str, Any]:
        self._counter += 1
        task_id = f"task_{self._counter}"
        temp_dir = tempfile.gettempdir()
        log_path = os.path.join(temp_dir, f"cmdai_bg_{task_id}.log")
        log_file = open(log_path, "w", encoding="utf-8", errors="replace")

        creation_flags = 0
        if os.name == "nt":
            creation_flags = 0x08000000  # CREATE_NO_WINDOW

        proc = subprocess.Popen(
            cmd,
            shell=True,
            cwd=cwd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )
        bg_task = BackgroundTask(task_id, cmd, proc, log_path, session_id=session_id)
        self.tasks[task_id] = bg_task
        return {
            "success": True,
            "background": True,
            "task_id": task_id,
            "pid": proc.pid,
            "cmd": cmd,
            "log_path": log_path,
        }

    def adopt(self, cmd: str, proc: subprocess.Popen, log_path: str, session_id: str = "") -> Dict[str, Any]:
        self._counter += 1
        task_id = f"task_{self._counter}"
        bg_task = BackgroundTask(task_id, cmd, proc, log_path, session_id=session_id)
        self.tasks[task_id] = bg_task
        return {
            "success": True,
            "background": True,
            "task_id": task_id,
            "pid": proc.pid,
            "cmd": cmd,
            "log_path": log_path,
        }

    def list_tasks(self) -> List[Dict[str, Any]]:
        res = []
        for tid, t in self.tasks.items():
            running = t.is_running()
            res.append({
                "task_id": tid,
                "session_id": str(getattr(t, "session_id", "") or ""),
                "cmd": t.cmd,
                "status": "running" if running else f"finished (code {t.poll()})",
                "running": running,
                "elapsed": round(time.time() - t.start_time, 1),
            })
        return res

    def kill_task(self, task_id: str) -> bool:
        t = self.tasks.get(task_id)
        if t:
            return t.kill()
        return False

    def get_task(self, task_id: str) -> Optional[BackgroundTask]:
        return self.tasks.get(task_id)

    def has_active_background(self, session_id: Optional[str] = None) -> bool:
        sid_str = str(session_id or "").strip()
        for t in self.tasks.values():
            if t.is_running():
                if not sid_str or str(getattr(t, "session_id", "") or "") == sid_str:
                    return True
        for s in self.subagents.values():
            if s.get("background") and s.get("status") == "running":
                if not sid_str or str(s.get("session_id") or "") == sid_str:
                    return True
        return False

    def register_subagent(self, sid: str, role: str, task: str, background: bool = False, session_id: str = "") -> None:
        self.subagents[sid] = {
            "subagent_id": sid,
            "session_id": session_id,
            "role": role,
            "task": task,
            "start_time": time.time(),
            "status": "running",
            "result": None,
            "background": background,
        }

    def mark_subagent_background(self, sid: str) -> None:
        if sid in self.subagents:
            self.subagents[sid]["background"] = True

    def update_subagent_tool(self, sid: str, tool: str, target: str = "") -> None:
        if sid in self.subagents:
            self.subagents[sid]["current_tool"] = tool
            self.subagents[sid]["tool_target"] = target

    def finish_subagent(self, sid: str, status: str = "finished", result: Any = None) -> None:
        if sid in self.subagents:
            self.subagents[sid]["status"] = status
            self.subagents[sid]["result"] = result

    def list_subagents(self) -> List[Dict[str, Any]]:
        res = []
        for sid, s in list(self.subagents.items()):
            res.append({
                "subagent_id": sid,
                "session_id": str(s.get("session_id") or ""),
                "role": s["role"],
                "task": s["task"],
                "status": s["status"],
                "elapsed": round(time.time() - s["start_time"], 1),
                "background": bool(s.get("background", False)),
            })
        return res

    def get_subagent(self, sid: str) -> Optional[Dict[str, Any]]:
        return self.subagents.get(sid)
