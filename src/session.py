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

    def to_prompt(self) -> str:
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

    def to_markdown(self) -> str:
                                                            
        return self.to_prompt()

    @classmethod
    def from_markdown(cls, text: str, session_id: str = "default"):
        state = cls(session_id=session_id)
        current_section = None
        
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
                
            if line.startswith("Cel:"):
                state.goal = line[4:].strip()
                current_section = "goal"
            elif line.startswith("Decyzje:"):
                current_section = "decisions"
            elif line.startswith("Pliki:"):
                current_section = "files"
            elif line.startswith("Plan:"):
                current_section = "plan"
            elif line.startswith("Problemy:"):
                current_section = "issues"
            elif line.startswith("Ograniczenia:"):
                current_section = "constraints"
            elif line.startswith("[KONTEKST SESJI]"):
                continue
            else:
                if current_section == "goal" and not line.startswith("-"):
                    state.goal += " " + line
                elif current_section == "decisions" and line.startswith("-"):
                    state.decisions.append(line[1:].strip())
                elif current_section == "files" and line.startswith("-"):
                    parts = line[1:].strip().split(":", 1)
                    if len(parts) == 2:
                        state.files_touched[parts[0].strip()] = parts[1].strip()
                elif current_section == "plan" and line.startswith("["):
                    match = re.match(r'\[([xX ]+)\]\s*(.*)', line)
                    if match:
                        done = match.group(1).strip().lower() == "x"
                        state.current_plan.append((match.group(2).strip(), done))
                elif current_section == "issues" and line.startswith("-"):
                    state.open_issues.append(line[1:].strip())
                elif current_section == "constraints" and line.startswith("-"):
                    state.constraints.append(line[1:].strip())
                    
        return state

class SessionManager:
    def __init__(self, cwd: str = "."):
        self.cwd = os.path.abspath(cwd)
        self.cmdai_code_dir = os.path.join(os.path.expanduser("~"), ".cmdai_code")
        self.current_state = SessionState()
        
    def ensure_dir(self):
        if not os.path.exists(self.cmdai_code_dir):
            os.makedirs(self.cmdai_code_dir)
            
    def save_state(self):
        if not self.current_state.goal and not self.current_state.decisions and not self.current_state.files_touched:
            return        
        self.ensure_dir()
        path = os.path.join(self.cmdai_code_dir, f"session_{self.current_state.session_id}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.current_state.to_markdown())
            
                                                             
        state_path = os.path.join(self.cmdai_code_dir, "state.md")
        with open(state_path, "w", encoding="utf-8") as f:
            f.write(self.current_state.to_markdown())

    def load_state(self, session_id: str = "default"):
        self.ensure_dir()
        path = os.path.join(self.cmdai_code_dir, f"session_{session_id}.md")
        if not os.path.exists(path) and session_id == "default":
            path = os.path.join(self.cmdai_code_dir, "state.md")
            
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.current_state = SessionState.from_markdown(f.read(), session_id)
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
            if f.startswith("session_") and f.endswith(".md"):
                sid = f[8:-3]
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
            result.append({"id": sid, "mtime": mtime, "date": dt.strftime("%Y-%m-%d %H:%M")})
            
        result.sort(key=lambda x: x["mtime"], reverse=True)
        return result

    def delete_session(self, session_id: str):
        if not os.path.exists(self.cmdai_code_dir):
            return
        
        md_path = os.path.join(self.cmdai_code_dir, f"session_{session_id}.md")
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
            
        old_md = os.path.join(self.cmdai_code_dir, f"session_{self.current_state.session_id}.md")
        old_json = os.path.join(self.cmdai_code_dir, f"session_{self.current_state.session_id}_history.json")
        
        self.current_state.session_id = new_id
        
        new_md = os.path.join(self.cmdai_code_dir, f"session_{new_id}.md")
        new_json = os.path.join(self.cmdai_code_dir, f"session_{new_id}_history.json")
        
        try:
            if os.path.exists(old_md):
                os.rename(old_md, new_md)
            if os.path.exists(old_json):
                os.rename(old_json, new_json)
        except Exception:
            pass
            
        self.save_state()