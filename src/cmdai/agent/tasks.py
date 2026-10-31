import os
import time
import subprocess
import tempfile
from typing import Dict, Any, Optional, List


class BackgroundTask:
    def __init__(self, task_id: str, cmd: str, proc: subprocess.Popen, log_path: str):
        self.task_id = task_id
        self.cmd = cmd
        self.proc = proc
        self.log_path = log_path
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
        self._notified_tasks: set = set()
        self._counter: int = 0

    @classmethod
    def get_instance(cls) -> "BackgroundTaskManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_finished_notifications(self) -> List[str]:
        notifications = []
        for tid, t in list(self.tasks.items()):
            if not t.is_running() and tid not in self._notified_tasks:
                self._notified_tasks.add(tid)
                code = t.poll()
                logs = t.read_logs(max_lines=15)
                msg = f"[Background command '{t.cmd}' has finished with exit code {code}]"
                if logs.strip():
                    msg += f"\nLast output:\n{logs.strip()}"
                notifications.append(msg)
        return notifications

    def spawn(self, cmd: str, cwd: str) -> Dict[str, Any]:
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
        bg_task = BackgroundTask(task_id, cmd, proc, log_path)
        self.tasks[task_id] = bg_task
        return {
            "success": True,
            "background": True,
            "task_id": task_id,
            "pid": proc.pid,
            "cmd": cmd,
            "log_path": log_path,
        }

    def adopt(self, cmd: str, proc: subprocess.Popen, log_path: str) -> Dict[str, Any]:
        self._counter += 1
        task_id = f"task_{self._counter}"
        bg_task = BackgroundTask(task_id, cmd, proc, log_path)
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
