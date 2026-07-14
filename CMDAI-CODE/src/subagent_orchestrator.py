import os
import json
import re
import time
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

from .api_model import OpenAIAPIModel
from .llama import LlamaModel
from .context import ContextManager


@dataclass
class SubAgent:
    """Represents a single sub-agent with its configuration and results."""
    name: str
    system_prompt: str
    task: str
    status: str = "pending"
    result: str = ""
    elapsed: float = 0.0


class SubAgentOrchestrator:
    """Orchestrates multiple sub-agents to collaboratively analyze and respond to complex tasks."""

    def __init__(self, model, context, cwd):
        """Initialize the orchestrator with the main model, context, and working directory."""
        self.model = model
        self.context = context
        self.cwd = cwd
        self.subagent_model = None
        state_path = os.path.expanduser("~/.cmdai_code/state.json")
        if os.path.exists(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
                self.subagent_model = state.get("subagent_model", None)
            except (json.JSONDecodeError, IOError):
                self.subagent_model = None

    def scan_project(self) -> dict:
        """Walk the project directory tree and collect file information."""
        skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv"}
        files = []
        dirs_count = 0
        file_types = {}

        for root, dirnames, filenames in os.walk(self.cwd):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            rel_root = os.path.relpath(root, self.cwd)
            if rel_root != ".":
                dirs_count += 1
            for fname in filenames:
                rel_path = os.path.join(rel_root, fname) if rel_root != "." else fname
                rel_path = rel_path.replace("\\", "/")
                files.append(rel_path)
                ext = os.path.splitext(fname)[1].lower()
                if ext:
                    file_types[ext] = file_types.get(ext, 0) + 1
                else:
                    file_types["(no ext)"] = file_types.get("(no ext)", 0) + 1

        tree_str = self._build_tree_str(self.cwd, skip_dirs, max_depth=3)

        summary_parts = []
        sorted_types = sorted(file_types.items(), key=lambda x: x[1], reverse=True)
        primary_lang = sorted_types[0][0] if sorted_types else "Unknown"
        lang_map = {
            ".py": "Python",
            ".js": "JavaScript",
            ".ts": "TypeScript",
            ".java": "Java",
            ".go": "Go",
            ".rs": "Rust",
            ".rb": "Ruby",
            ".c": "C",
            ".cpp": "C++",
            ".cs": "C#",
            ".html": "HTML",
            ".css": "CSS",
        }
        project_type = lang_map.get(primary_lang, primary_lang.lstrip(".").capitalize() if primary_lang != "(no ext)" else "Mixed")
        for ext, count in sorted_types:
            summary_parts.append(f"{count} {ext} file{'s' if count != 1 else ''}")
        summary = f"{project_type} project with " + ", ".join(summary_parts)

        return {
            "total_files": len(files),
            "total_dirs": dirs_count,
            "file_types": file_types,
            "tree_str": tree_str,
            "files": files,
            "summary": summary,
        }

    def _build_tree_str(self, root_dir, skip_dirs, max_depth=3) -> str:
        """Build an indented tree display string limited to a given depth."""
        lines = [os.path.basename(root_dir) + "/"]
        self._walk_tree(root_dir, skip_dirs, lines, prefix="", depth=0, max_depth=max_depth)
        return "\n".join(lines)

    def _walk_tree(self, current_dir, skip_dirs, lines, prefix, depth, max_depth):
        """Recursively build tree lines."""
        if depth >= max_depth:
            return
        try:
            entries = sorted(os.listdir(current_dir))
        except PermissionError:
            return
        dirs = [e for e in entries if os.path.isdir(os.path.join(current_dir, e)) and e not in skip_dirs]
        files = [e for e in entries if os.path.isfile(os.path.join(current_dir, e))]
        all_entries = [(d, True) for d in dirs] + [(f, False) for f in files]
        for i, (name, is_dir) in enumerate(all_entries):
            is_last = i == len(all_entries) - 1
            connector = "└── " if is_last else "├── "
            display = name + "/" if is_dir else name
            lines.append(prefix + connector + display)
            if is_dir:
                extension = "    " if is_last else "│   "
                self._walk_tree(
                    os.path.join(current_dir, name),
                    skip_dirs,
                    lines,
                    prefix + extension,
                    depth + 1,
                    max_depth,
                )

    def plan_subagents(self, project_info: dict, user_instruction: str = None, on_chunk=None, retry=False) -> list:
        """Use the main model to analyze the project and plan sub-agents."""
        tree = project_info.get("tree_str", "")
        summary = project_info.get("summary", "")
        total_files = project_info.get("total_files", 0)
        files_list = project_info.get("files", [])

        files_preview = "\n".join(files_list[:50])
        if len(files_list) > 50:
            files_preview += f"\n... and {len(files_list) - 50} more files"

        if retry:
            prompt = f"""Project: {summary} ({total_files} files).
Files: {files_preview}

{"User request: " + user_instruction if user_instruction else "Analyze the project."}

You MUST respond with ONLY this JSON (no other text):
```json
[
  {{"name": "Agent1", "system_prompt": "You analyze code quality and bugs.", "task": "Find bugs and code quality issues."}},
  {{"name": "Agent2", "system_prompt": "You analyze architecture and design.", "task": "Review project architecture."}}
]
```
Replace the example content with real agents for this project. Use 2-4 agents. ONLY output the JSON block, nothing else."""
        else:
            prompt = f"""You are a project planner. Analyze this project and create specialized sub-agents.

Project: {summary}
Total files: {total_files}

Files:
{files_preview}

{"User request: " + user_instruction if user_instruction else ""}

Create sub-agents to analyze this project. Respond with a JSON array inside ```json``` fences.
Each object needs exactly these 3 fields:
- "name": short agent name (2-3 words)
- "system_prompt": what this agent specializes in
- "task": what specifically to investigate

Example format:
```json
[
  {{"name": "Code Reviewer", "system_prompt": "You are an expert code reviewer. Analyze code for bugs, anti-patterns, and improvements.", "task": "Review all source files for bugs and code quality issues."}},
  {{"name": "Architecture Analyst", "system_prompt": "You are a software architect. Analyze project structure and design patterns.", "task": "Evaluate the project architecture and suggest improvements."}}
]
```

Create 2-4 agents. Respond ONLY with the ```json``` block."""

        messages = [{"role": "user", "content": prompt}]
        response_text = ""
        thinking_text = ""
        try:
            for content, thinking, tc in self.model.stream_chat(messages, reasoning_budget=512):
                if content or thinking:
                    if content:
                        response_text += content
                    if thinking:
                        thinking_text += thinking
                    if on_chunk:
                        # Try to pass two args if on_chunk supports it
                        try:
                            on_chunk(response_text, thinking_text)
                        except TypeError:
                            on_chunk(response_text)
        except Exception:
            pass

        result = self._parse_subagents_json(response_text)

        if not result and response_text.strip():
            result = self._fallback_parse(response_text)

        if not result:
            result = self._generate_default_agents(project_info, user_instruction)

        return result

    def _parse_subagents_json(self, text: str) -> list:
        """Extract and parse JSON array of sub-agent definitions from model response. Multi-strategy."""
        strategies = [
            lambda t: re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", t, re.DOTALL),
            lambda t: re.search(r"(\[\s*\{[\s\S]*?\}\s*\])", t, re.DOTALL),
            lambda t: re.search(r"(\[[\s\S]*\])\s*$", t, re.DOTALL),
        ]

        for strategy in strategies:
            match = strategy(text)
            if match:
                json_str = match.group(1)
                json_str = json_str.replace("'", '"')
                json_str = re.sub(r',\s*\]', ']', json_str)
                json_str = re.sub(r',\s*\}', '}', json_str)
                try:
                    parsed = json.loads(json_str)
                    if isinstance(parsed, list):
                        agents = self._extract_agents_from_list(parsed)
                        if agents:
                            return agents
                except json.JSONDecodeError:
                    cleaned = self._repair_json(json_str)
                    try:
                        parsed = json.loads(cleaned)
                        if isinstance(parsed, list):
                            agents = self._extract_agents_from_list(parsed)
                            if agents:
                                return agents
                    except json.JSONDecodeError:
                        pass

        return []

    def _fallback_parse(self, text: str) -> list:
        """Last-resort: find individual JSON objects in the text."""
        agents = []
        for m in re.finditer(r'\{[^{}]*"name"[^{}]*\}', text, re.DOTALL):
            try:
                obj = json.loads(m.group(0))
                agent = self._normalize_agent_dict(obj)
                if agent:
                    agents.append(agent)
            except json.JSONDecodeError:
                cleaned = m.group(0).replace("'", '"')
                cleaned = re.sub(r',\s*\}', '}', cleaned)
                try:
                    obj = json.loads(cleaned)
                    agent = self._normalize_agent_dict(obj)
                    if agent:
                        agents.append(agent)
                except json.JSONDecodeError:
                    pass
        return agents

    def _repair_json(self, text: str) -> str:
        """Attempt to repair broken JSON."""
        text = text.strip()
        open_braces = text.count('{')
        close_braces = text.count('}')
        if open_braces > close_braces:
            text += '}' * (open_braces - close_braces)
        open_brackets = text.count('[')
        close_brackets = text.count(']')
        if open_brackets > close_brackets:
            text += ']' * (open_brackets - close_brackets)
        text = re.sub(r',\s*([}\]])', r'\1', text)
        return text

    def _extract_agents_from_list(self, parsed: list) -> list:
        """Extract SubAgent objects from a parsed JSON list with lenient field matching."""
        agents = []
        for item in parsed:
            if isinstance(item, dict):
                agent = self._normalize_agent_dict(item)
                if agent:
                    agents.append(agent)
        return agents

    def _normalize_agent_dict(self, item: dict):
        """Normalize a dict into a SubAgent, accepting various field name aliases."""
        name = item.get("name") or item.get("agent_name") or item.get("title") or ""
        system_prompt = (
            item.get("system_prompt")
            or item.get("prompt")
            or item.get("role")
            or item.get("description")
            or item.get("system")
            or item.get("specialization")
            or ""
        )
        task = (
            item.get("task")
            or item.get("goal")
            or item.get("instruction")
            or item.get("objective")
            or item.get("question")
            or item.get("focus")
            or ""
        )

        if not name:
            return None
        if not system_prompt:
            system_prompt = f"You are a specialized agent named {name}."
        if not task:
            task = system_prompt

        return SubAgent(
            name=str(name),
            system_prompt=str(system_prompt),
            task=str(task),
        )

    def _generate_default_agents(self, project_info: dict, user_instruction: str = None) -> list:
        """Generate default sub-agents from project info when model fails completely."""
        summary = project_info.get("summary", "Unknown project")
        agents = [
            SubAgent(
                name="Code Reviewer",
                system_prompt="You are an expert code reviewer. Analyze all source files for bugs, anti-patterns, code smells, and potential improvements. Be thorough and specific.",
                task=f"Review this {summary} for code quality issues, bugs, and improvements." + (f" Focus on: {user_instruction}" if user_instruction else ""),
            ),
            SubAgent(
                name="Architecture Analyst",
                system_prompt="You are a software architect. Analyze the project structure, module dependencies, design patterns, and overall architecture. Suggest improvements.",
                task=f"Analyze the architecture of this {summary}. Evaluate file organization, module coupling, and design patterns." + (f" Focus on: {user_instruction}" if user_instruction else ""),
            ),
        ]
        return agents

    def _create_subagent_model(self):
        """Create a new model instance for sub-agents based on configuration."""
        if self.subagent_model is None:
            return self.model

        if isinstance(self.subagent_model, dict):
            return OpenAIAPIModel(
                model_name=self.subagent_model.get("name", ""),
                api_key=self.subagent_model.get("api_key", ""),
                base_url=self.subagent_model.get("base_url", "https://api.openai.com/v1"),
                provider_id=self.subagent_model.get("provider"),
            )

        if isinstance(self.subagent_model, str):
            if os.path.exists(self.subagent_model):
                return LlamaModel(self.subagent_model)

        return self.model

    def run_sequential(self, subagents: list, ui_display) -> list:
        """Run sub-agents sequentially, suitable for local models."""
        for agent in subagents:
            agent.status = "running"
            if ui_display:
                ui_display.update_agent_status(agent.name, "running")
            self._run_single_agent(agent, ui_display)
            if ui_display:
                ui_display.update_agent_status(agent.name, "done", agent.elapsed)
        return subagents

    def run_parallel(self, subagents: list, ui_display) -> list:
        """Run sub-agents in parallel using threads, suitable for API models."""
        with ThreadPoolExecutor(max_workers=len(subagents)) as executor:
            futures = {}
            for agent in subagents:
                agent.status = "running"
                if ui_display:
                    ui_display.update_agent_status(agent.name, "running")
                future = executor.submit(self._run_single_agent, agent, ui_display)
                futures[future] = agent

            for future in as_completed(futures):
                agent = futures[future]
                try:
                    future.result()
                except Exception:
                    agent.status = "error"
                    agent.result = "Sub-agent execution failed."
                if ui_display:
                    ui_display.update_agent_status(agent.name, agent.status, agent.elapsed)

        return subagents

    def _run_single_agent(self, agent: SubAgent, ui_display):
        """Run a single sub-agent: create model, build messages, stream response, collect result."""
        from .tools import TOOLS_DEFINITIONS, execute_tool

        start_time = time.time()

        model = self._create_subagent_model()

        sys_prompt_ext = agent.system_prompt + "\n\nCRITICAL: You are an autonomous sub-agent equipped with tools. DO NOT JUST LIST FILES AND STOP. You MUST actively use tools (like read_file, run_grep, ls, etc.) in a loop to READ the actual code/contents of the project. Keep calling tools to read file after file until you fully understand the architecture and can provide a deep technical analysis or complete the task. Never stop after just one tool call if there is more code to explore!"

        messages = [
            {"role": "system", "content": sys_prompt_ext},
            {"role": "user", "content": agent.task},
        ]

        response_text = ""
        while True:
            current_chunk_text = ""
            current_tc = None
            try:
                for content, thinking, tc in model.stream_chat(messages, tools=TOOLS_DEFINITIONS, reasoning_budget=1024):
                    if content:
                        current_chunk_text += content
                        response_text += content
                        if ui_display:
                            ui_display.update_agent_content(agent.name, response_text)
                    if tc:
                        current_tc = tc
            except Exception as e:
                response_text += f"\nError during sub-agent execution: {str(e)}"
                agent.status = "error"
                agent.elapsed = time.time() - start_time
                agent.result = response_text
                if ui_display:
                    ui_display.set_agent_result(agent.name, response_text)
                return

            if current_chunk_text.strip():
                messages.append({"role": "assistant", "content": current_chunk_text})
            
            if current_tc:
                if ui_display and len(current_tc) > 0:
                    func_name = current_tc[0].get("function", {}).get("name", "tool")
                    ui_display.set_agent_tool(agent.name, func_name)
                    
                if not current_chunk_text.strip():
                    messages.append({"role": "assistant", "content": ""})
                messages[-1]["tool_calls"] = current_tc
                for tool_call in current_tc:
                    func = tool_call.get("function", {})
                    name = func.get("name", "")
                    args = func.get("arguments", "{}")
                    if isinstance(args, str):
                        import json
                        try:
                            args = json.loads(args)
                        except:
                            args = {}
                    
                    res = execute_tool(name, args, restricted_dir=self.cwd)
                    messages.append({
                        "role": "tool",
                        "content": str(res),
                        "tool_call_id": tool_call.get("id", "")
                    })
                
                if ui_display:
                    ui_display.set_agent_tool(agent.name, "")
            else:
                agent.result = response_text
                agent.status = "done"
                agent.elapsed = time.time() - start_time
                if ui_display:
                    ui_display.set_agent_result(agent.name, response_text)
                break

    def synthesize_results(self, subagents: list, user_instruction: str = None, on_chunk=None) -> str:
        """Synthesize all sub-agent results into a comprehensive final response."""
        results_block = ""
        for agent in subagents:
            results_block += f"=== Sub-Agent: {agent.name} (status: {agent.status}, {agent.elapsed:.1f}s) ===\n{agent.result}\n\n"

        sys_prompt = "You are the Lead Engineer. You have just delegated research tasks to several sub-agents. They have provided their findings. Your job is to EXECUTE the user's original instruction based on these findings. DO NOT just write a 'status report' or 'synthesis report' of what the agents did. Write the actual code, architecture plan, or direct answer the user asked for."
        
        user_msg = f"User Instruction: {user_instruction}\n\nSub-Agent Findings:\n{results_block}\n\nBased on these findings, fulfill my instruction directly."

        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg}
        ]

        response_text = ""
        try:
            for content, thinking, tc in self.model.stream_chat(messages, reasoning_budget=2048):
                if content:
                    response_text += content
                    if on_chunk:
                        on_chunk(content)
        except Exception as e:
            response_text = f"Error during synthesis: {str(e)}"
            if on_chunk:
                on_chunk(response_text)

        return response_text
