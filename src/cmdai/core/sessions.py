import json
import os
import time
from typing import Any, Dict, List, Optional


class SessionManager:
    """Saves and restores conversation sessions in app/sessions."""

    def __init__(self, conversations_dir: Optional[str] = None, project_dir: Optional[str] = None):
        here = os.path.dirname(os.path.abspath(__file__))
        self.repo_root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
        self.repo_sessions_dir = os.path.join(self.repo_root, "app", "sessions")

        if project_dir:
            self.project_dir = os.path.abspath(project_dir)
        else:
            self.project_dir = os.path.abspath(os.getcwd())

        if conversations_dir:
            self.conversations_dir = os.path.abspath(conversations_dir)
        else:
            self.conversations_dir = os.path.join(self.project_dir, "app", "sessions")

        os.makedirs(self.conversations_dir, exist_ok=True)
        os.makedirs(self.repo_sessions_dir, exist_ok=True)

    def _get_candidate_dirs(self) -> List[str]:
        dirs = [
            self.conversations_dir,
            self.repo_sessions_dir,
            os.path.join(self.project_dir, ".cmdai", "sessions"),
            os.path.join(self.project_dir, "conversations"),
            os.path.join(self.repo_root, ".cmdai", "sessions"),
            os.path.join(self.repo_root, "conversations"),
            os.path.join(os.path.expanduser("~"), ".cmdai", "sessions"),
            os.path.join(os.path.expanduser("~"), ".cmdai_code", "sessions"),
        ]
        unique_dirs = []
        for d in dirs:
            if d and d not in unique_dirs and os.path.exists(d):
                unique_dirs.append(d)
        return unique_dirs

    def save_session(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        model_id: str,
        title: str = "",
        stats: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not session_id:
            session_id = f"session_{int(time.time())}"

        data = {
            "id": session_id,
            "title": title or f"Session {session_id}",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "model_id": model_id,
            "messages": messages,
            "stats": stats or {},
        }

        targets = {self.conversations_dir, self.repo_sessions_dir}
        saved = False
        for target_dir in targets:
            try:
                os.makedirs(target_dir, exist_ok=True)
                path = os.path.join(target_dir, f"{session_id}.json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                saved = True
            except Exception:
                pass
        return saved

    def list_sessions(self) -> List[Dict[str, Any]]:
        sessions = []
        seen_ids = set()

        for s_dir in self._get_candidate_dirs():
            try:
                entries = sorted(os.listdir(s_dir), reverse=True)
            except Exception:
                continue

            for fname in entries:
                if fname.endswith(".json"):
                    sid = fname[:-5]
                    if sid in seen_ids:
                        continue
                    seen_ids.add(sid)
                    path = os.path.join(s_dir, fname)
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            sessions.append({
                                "id": data.get("id", sid),
                                "title": data.get("title", fname),
                                "created_at": data.get("created_at", ""),
                                "model_id": data.get("model_id", ""),
                                "message_count": len(data.get("messages", [])),
                            })
                        if s_dir != self.conversations_dir:
                            dest = os.path.join(self.conversations_dir, fname)
                            if not os.path.exists(dest):
                                with open(dest, "w", encoding="utf-8") as f_out:
                                    json.dump(data, f_out, indent=2, ensure_ascii=False)
                        if s_dir != self.repo_sessions_dir:
                            dest_repo = os.path.join(self.repo_sessions_dir, fname)
                            if not os.path.exists(dest_repo):
                                with open(dest_repo, "w", encoding="utf-8") as f_out2:
                                    json.dump(data, f_out2, indent=2, ensure_ascii=False)
                    except Exception:
                        pass
        return sessions

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        for s_dir in self._get_candidate_dirs():
            path = os.path.join(s_dir, f"{session_id}.json")
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
        return None

    def delete_session(self, session_id: str) -> bool:
        deleted = False
        for s_dir in self._get_candidate_dirs():
            path = os.path.join(s_dir, f"{session_id}.json")
            if os.path.exists(path):
                try:
                    os.remove(path)
                    deleted = True
                except Exception:
                    pass
        return deleted
