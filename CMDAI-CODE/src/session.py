import os
import re
from dataclasses import dataclass, field
from typing import List, Dict, Tuple

@dataclass
class SessionState:
    session_id: str = "default"
    goal: str = ""
    decisions: List[str] = field(default_factory=list)
    files_touched: Dict[str, str] = field(default_factory=dict)
    current_plan: List[Tuple[str, bool]] = field(default_factory=list)
    open_issues: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    summary_sections: Dict[str, List[str]] = field(default_factory=dict)

    def to_prompt(self) -> str:
        if self.summary_sections:
            out = "[SESSION STATE]\n# Session State\n"
            for title, lines in self.summary_sections.items():
                out += f"\n## {title}\n"
                out += "\n".join(lines) + "\n"
            return out.strip()

        out = "[KONTEKST SESJI]\n"
        if self.goal:
            out += f"Cel: {self.goal}\n\n"
            
        if self.decisions:
            out += "Decyzje:\n"
            for d in self.decisions:
                out += f"- {d}\n"
            out += "\n"
            
        if self.files_touched:
            out += "Pliki:\n"
            for k, v in self.files_touched.items():
                out += f"- {k}: {v}\n"
            out += "\n"
            
        if self.current_plan:
            out += "Plan:\n"
            for step, done in self.current_plan:
                mark = "x" if done else " "
                out += f"[{mark}] {step}\n"
            out += "\n"
            
        if self.open_issues:
            out += "Problemy:\n"
            for i in self.open_issues:
                out += f"- {i}\n"
            out += "\n"
            
        if self.constraints:
            out += "Ograniczenia:\n"
            for c in self.constraints:
                out += f"- {c}\n"
            out += "\n"
            
        return out.strip()

    def to_json(self) -> str:
        import json
        import dataclasses
        return json.dumps(dataclasses.asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_markdown(cls, text: str, session_id: str = "default"):
        state = cls(session_id=session_id)
        current_section = None
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue

            if line.startswith("## "):
                current_section = line[3:].strip()
                state.summary_sections.setdefault(current_section, [])
                continue
            if line.startswith("# "):
                continue
            if current_section in state.summary_sections:
                state.summary_sections[current_section].append(line)
                continue
            
            if line.startswith("Goal:"):
                state.goal = line[5:].strip()
                current_section = "goal"
            elif line.startswith("Decisions:"):
                current_section = "decisions"
            elif line.startswith("Files:"):
                current_section = "files"
            elif line.startswith("Plan:"):
                current_section = "plan"
            elif line.startswith("Issues:"):
                current_section = "issues"
            elif line.startswith("Constraints:"):
                current_section = "constraints"
            else:
                if current_section == "goal" and not line.startswith("-") and not line.startswith("["):
                    state.goal += " " + line
                elif current_section == "decisions" and line.startswith("-"):
                    state.decisions.append(line[1:].strip())
                elif current_section == "files" and line.startswith("-"):
                    parts = line[1:].strip().split(":", 1)
                    if len(parts) == 2:
                        state.files_touched[parts[0].strip()] = parts[1].strip()
                    else:
                        state.files_touched[parts[0].strip()] = ""
                elif current_section == "plan" and line.startswith("["):
                    is_done = line.startswith("[x]") or line.startswith("[X]")
                    step = line[3:].strip() if len(line) > 3 and line[2] == "]" else line
                    state.current_plan.append((step, is_done))
                elif current_section == "issues" and line.startswith("-"):
                    state.open_issues.append(line[1:].strip())
                elif current_section == "constraints" and line.startswith("-"):
                    state.constraints.append(line[1:].strip())
        if state.summary_sections:
            objective = state.summary_sections.get("Objective", [])
            for item in objective:
                if item.lower().startswith("- user goal:"):
                    state.goal = item.split(":", 1)[1].strip()
                    break
        return state

    @classmethod
    def from_json(cls, text: str, session_id: str = "default"):
        import json
        try:
            data = json.loads(text)
            data["session_id"] = session_id
            if "current_plan" in data:
                data["current_plan"] = [tuple(x) for x in data["current_plan"]]
            return cls(**data)
        except Exception:
            return cls(session_id=session_id)

class SessionManager:
    def __init__(self, cwd: str = "."):
        self.cwd = os.path.abspath(cwd)
        self.cmdai_code_dir = os.path.join(os.path.expanduser("~"), ".cmdai_code")
        self.current_state = SessionState()
        
    def ensure_dir(self):
        if not os.path.exists(self.cmdai_code_dir):
            os.makedirs(self.cmdai_code_dir)
            
    def save_state(self):
        if not self.current_state.goal and not self.current_state.decisions and not self.current_state.files_touched and not self.current_state.summary_sections:
            return        
        self.ensure_dir()
        path = os.path.join(self.cmdai_code_dir, f"session_{self.current_state.session_id}_state.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.current_state.to_json())
            
        # Zapisz też globalny state dla łatwego powrotu
        state_path = os.path.join(self.cmdai_code_dir, "state_session.json")
        with open(state_path, "w", encoding="utf-8") as f:
            f.write(self.current_state.to_json())

    def load_state(self, session_id: str = "default"):
        self.ensure_dir()
        path = os.path.join(self.cmdai_code_dir, f"session_{session_id}_state.json")
        if not os.path.exists(path) and session_id == "default":
            path = os.path.join(self.cmdai_code_dir, "state_session.json")
            
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.current_state = SessionState.from_json(f.read(), session_id)
            except Exception:
                self.current_state = SessionState(session_id=session_id)
        else:
            self.current_state = SessionState(session_id=session_id)
            
    def get_all_sessions(self) -> List[Dict[str, 'Any']]:
        import datetime
        if not os.path.exists(self.cmdai_code_dir):
            return []
        sessions_dict = {}
        for f in os.listdir(self.cmdai_code_dir):
            if f.startswith("session_") and f.endswith("_state.json"):
                sid = f[8:-11]
                path = os.path.join(self.cmdai_code_dir, f)
                mtime = os.path.getmtime(path)
                sessions_dict[sid] = mtime
            elif f.startswith("session_") and f.endswith("_history.json"):
                sid = f[8:-13]
                path = os.path.join(self.cmdai_code_dir, f)
                mtime = os.path.getmtime(path)
                if sid not in sessions_dict or sessions_dict[sid] < mtime:
                    sessions_dict[sid] = mtime
                    
        result = []
        for sid, mtime in sessions_dict.items():
            dt = datetime.datetime.fromtimestamp(mtime)
            msg_count = 0
            hist_path = os.path.join(self.cmdai_code_dir, f"session_{sid}_history.json")
            if os.path.exists(hist_path):
                try:
                    import json
                    with open(hist_path, "r", encoding="utf-8") as f:
                        msgs = json.load(f)
                        msg_count = len(msgs)
                except Exception:
                    pass
            result.append({"id": sid, "mtime": mtime, "date": dt.strftime("%Y-%m-%d %H:%M"), "msg_count": msg_count})
            
        result.sort(key=lambda x: x["mtime"], reverse=True)
        return result

    def delete_session(self, session_id: str):
        if not os.path.exists(self.cmdai_code_dir):
            return
        
        md_path = os.path.join(self.cmdai_code_dir, f"session_{session_id}_state.json")
        json_path = os.path.join(self.cmdai_code_dir, f"session_{session_id}_history.json")
        
        try:
            if os.path.exists(md_path):
                os.remove(md_path)
            if os.path.exists(json_path):
                os.remove(json_path)
        except Exception:
            pass

    def rename_session(self, new_id: str):
        if not self.current_state.session_id or self.current_state.session_id == new_id:
            return
            
        old_md = os.path.join(self.cmdai_code_dir, f"session_{self.current_state.session_id}_state.json")
        old_json = os.path.join(self.cmdai_code_dir, f"session_{self.current_state.session_id}_history.json")
        
        self.current_state.session_id = new_id
        
        new_md = os.path.join(self.cmdai_code_dir, f"session_{new_id}_state.json")
        new_json = os.path.join(self.cmdai_code_dir, f"session_{new_id}_history.json")
        
        try:
            if os.path.exists(old_md):
                os.rename(old_md, new_md)
            if os.path.exists(old_json):
                os.rename(old_json, new_json)
        except Exception:
            pass
            
        self.save_state()
