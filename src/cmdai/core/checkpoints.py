import os
import json
import time
import shutil
import hashlib
import subprocess
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional

@dataclass
class Checkpoint:
    id: str
    prompt: str
    timestamp: float
    files_changed: List[str]
    stats: Dict[str, int]
    diff: str
    is_active: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'Checkpoint':
        return cls(**data)


class CheckpointManager:

    def __init__(self, workdir: str = "."):
        self.workdir = os.path.abspath(workdir)
        self.checkpoints_dir = os.path.join(self.workdir, ".cmdai_code_project", "checkpoints")
        os.makedirs(self.checkpoints_dir, exist_ok=True)
        self.active_checkpoint_id: Optional[str] = None
        self._load_active()

    def _meta_file(self) -> str:
        return os.path.join(self.checkpoints_dir, "checkpoints.json")

    def _load_active(self) -> None:
        mf = self._meta_file()
        if os.path.exists(mf):
            try:
                with open(mf, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.active_checkpoint_id = data.get("active_id")
            except Exception:
                pass

    def _save_checkpoints(self, checkpoints: List[Checkpoint]) -> None:
        mf = self._meta_file()
        try:
            data = {
                "active_id": self.active_checkpoint_id,
                "checkpoints": [c.to_dict() for c in checkpoints]
            }
            with open(mf, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def list_checkpoints(self) -> List[Checkpoint]:
        mf = self._meta_file()
        if not os.path.exists(mf):
            return []
        try:
            with open(mf, "r", encoding="utf-8") as f:
                data = json.load(f)
                cps = [Checkpoint.from_dict(d) for d in data.get("checkpoints", [])]
                for c in cps:
                    c.is_active = (c.id == self.active_checkpoint_id)
                return cps
        except Exception:
            return []

    def create_checkpoint(self, prompt: str) -> Checkpoint:
        ts = time.time()
        p_hash = hashlib.sha256(f"{prompt}_{ts}".encode()).hexdigest()[:8]
        time_str = time.strftime("%Y%m%d_%H%M%S", time.localtime(ts))
        cid = f"cp_{time_str}_{p_hash}"

        files_changed = []
        diff_text = ""
        added = 0
        removed = 0

        try:
            res = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=self.workdir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5
            )
            if res.returncode == 0 and res.stdout:
                diff_text = res.stdout
            else:
                res2 = subprocess.run(
                    ["git", "diff"],
                    cwd=self.workdir,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=5
                )
                diff_text = res2.stdout or ""

            st_res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.workdir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5
            )
            if st_res.returncode == 0:
                for line in st_res.stdout.splitlines():
                    if line.strip():
                        files_changed.append(line[2:].strip().strip('"'))
        except Exception:
            pass

        for line in diff_text.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1

        cp = Checkpoint(
            id=cid,
            prompt=prompt[:200],
            timestamp=ts,
            files_changed=files_changed,
            stats={"added": added, "removed": removed},
            diff=diff_text,
            is_active=True
        )

        self.active_checkpoint_id = cid
        cps = self.list_checkpoints()
        for old_c in cps:
            old_c.is_active = False
        cps.insert(0, cp)
        cps = cps[:50]
        self._save_checkpoints(cps)
        return cp

    def revert_checkpoint(self, checkpoint_id: str) -> bool:
        cps = self.list_checkpoints()
        target = next((c for c in cps if c.id == checkpoint_id), None)
        if not target:
            return False
        try:
            subprocess.run(["git", "checkout", "."], cwd=self.workdir, check=True)
            self.active_checkpoint_id = checkpoint_id
            self._save_checkpoints(cps)
            return True
        except Exception:
            return False
