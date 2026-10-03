import sys
import re
import json
import uuid
from pathlib import Path
import redis
from openai import OpenAI

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import settings
from orchestrator.guardrails import guardrail_interceptor, GuardrailViolation

settings.WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
PROFILES_DIR = ROOT_DIR / "profiles"

# Connect to Redis
r = redis.Redis(
    host=settings.redis.host,
    port=settings.redis.port,
    db=settings.redis.db,
    decode_responses=True
)

# ----------------- REDIS MEMORY & STATE LAYER -----------------

class AgentMemory:
    """Manages agent conversation context and shared state in Redis."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.context_key = f"session:{session_id}:context"
        self.blackboard_key = f"session:{session_id}:blackboard"

    def append_message(self, role: str, content: str):
        """Persists a conversation message to Redis."""
        entry = json.dumps({"role": role, "content": content})
        r.rpush(self.context_key, entry)

    def get_context(self) -> list:
        """Retrieves full conversation context from Redis."""
        raw_items = r.lrange(self.context_key, 0, -1)
        return [json.loads(item) for item in raw_items]

    def set_blackboard(self, field: str, value: str):
        """Writes shared agent data (reports, status, summaries) to a Redis hash."""
        r.hset(self.blackboard_key, field, value)

    def get_blackboard(self, field: str) -> str:
        """Reads shared agent data from the Redis hash."""
        return r.hget(self.blackboard_key, field) or ""


def publish_event(stage: str, status: str, details: str):
    """Broadcasts agent lifecycle transitions to Redis Pub/Sub."""
    payload = json.dumps({"stage": stage, "status": status, "details": details})
    try:
        channel_name = getattr(settings.redis, "channel", "agent_pipeline_events")
        r.publish(channel_name, payload)
    except Exception as e:
        print(f"Redis warning: {e}")
    print(f"\033[94m[{stage}]\033[0m \033[92m{status}\033[0m: {details}")

# ----------------- INFERENCE & TOOLS -----------------

client = OpenAI(
    base_url=settings.hermes.base_url,
    api_key=settings.hermes.api_key
)

HERMES_TOOLS_SPEC = """
<tools>
{
  "name": "run_impact_analysis",
  "description": "Scans workspace directory to determine affected files and potential blast radius.",
  "parameters": {
    "type": "object",
    "properties": {
      "module_name": {"type": "string", "description": "Module or feature to analyze"}
    },
    "required": ["module_name"]
  }
}
{
  "name": "write_file",
  "description": "Writes or updates source code inside the local workspace sandbox.",
  "parameters": {
    "type": "object",
    "properties": {
      "path": {"type": "string", "description": "Relative file path within workspace"},
      "content": {"type": "string", "description": "Source code text to write"}
    },
    "required": ["path", "content"]
  }
}
{
  "name": "read_file",
  "description": "Reads source code from the local workspace sandbox.",
  "parameters": {
    "type": "object",
    "properties": {
      "path": {"type": "string", "description": "Relative file path within workspace"}
    },
    "required": ["path"]
  }
}
</tools>
"""

HERMES_SYSTEM_INSTRUCTION = """
You have access to the functions defined inside <tools>.
To invoke a tool, you MUST wrap your call in <tool_call> tags with JSON content:
<tool_call>
{"name": "function_name", "arguments": {"arg_name": "value"}}
</tool_call>
"""

def load_profile(profile_name: str) -> str:
    profile_path = PROFILES_DIR / f"{profile_name}.md"
    base_prompt = profile_path.read_text(encoding="utf-8").strip()
    return f"{base_prompt}\n\n{HERMES_SYSTEM_INSTRUCTION}\n{HERMES_TOOLS_SPEC}"

def execute_tool(tool_name: str, args: dict) -> str:
    try:
        guardrail_interceptor(tool_name, args)
    except GuardrailViolation as gv:
        publish_event("Guardrail", "BLOCKED", str(gv))
        return json.dumps({"error": f"Guardrail Violation: {str(gv)}"})

    if tool_name == "run_impact_analysis":
        module = args.get("module_name")
        return json.dumps({
            "status": "success",
            "analyzed_target": module,
            "blast_radius": "LOW",
            "impacted_files": [f"{module}.py", f"test_{module}.py"]
        })

    elif tool_name == "write_file":
        rel_path = args.get("path")
        content = args.get("content")
        target_file = settings.WORKSPACE_DIR / rel_path
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(content, encoding="utf-8")
        return json.dumps({"status": "success", "file_created": str(rel_path), "bytes": len(content)})

    elif tool_name == "read_file":
        rel_path = args.get("path")
        target_file = settings.WORKSPACE_DIR / rel_path
        if not target_file.exists():
            return json.dumps({"error": f"File '{rel_path}' does not exist."})
        return json.dumps({"status": "success", "content": target_file.read_text(encoding="utf-8")})

    return json.dumps({"error": f"Tool '{tool_name}' not implemented"})

def parse_hermes_tool_calls(text: str):
    pattern = r"<tool_call>\s*({.*?})\s*</tool_call>"
    matches = re.findall(pattern, text, re.DOTALL)
    calls = []
    for m in matches:
        try:
            calls.append(json.loads(m.strip()))
        except json.JSONDecodeError:
            continue
    return calls

def run_agent_turn(memory: AgentMemory) -> str:
    """Executes a model turn by pulling context from and syncing responses back to Redis memory."""
    messages = memory.get_context()
    
    response = client.chat.completions.create(
        model=settings.hermes.model_name,
        messages=messages,
        temperature=settings.hermes.temperature
    )
    content = response.choices[0].message.content or ""
    
    # Save the assistant response to Redis
    memory.append_message("assistant", content)

    # Check for tool invocations
    tool_calls = parse_hermes_tool_calls(content)
    if tool_calls:
        for call in tool_calls:
            fn_name = call.get("name")
            fn_args = call.get("arguments", {})

            publish_event("Tool Execution", "INVOKING", f"{fn_name}({fn_args})")
            tool_output = execute_tool(fn_name, fn_args)
            publish_event("Tool Response", "RETURNED", tool_output)

            # Persist tool response to Redis context
            tool_response_msg = f"<tool_response>\n{{\"name\": \"{fn_name}\", \"content\": {tool_output}}}\n</tool_response>"
            memory.append_message("user", tool_response_msg)

        # Recurse using updated context stored in Redis
        return run_agent_turn(memory)

    return content

# ----------------- ORCHESTRATION PIPELINE -----------------

def run_orchestrator(task_prompt: str, session_id: str = None):
    session_id = session_id or str(uuid.uuid4())[:8]
    memory = AgentMemory(session_id)
    publish_event("Project Ingestion", "STARTED", f"Session: {session_id} | Task: {task_prompt}")

    # Stage 1: Impact Analyser
    publish_event("Impact Analyser", "STARTED", "Assessing repository dependencies and blast radius...")
    memory.append_message("system", load_profile("impact_analyser"))
    memory.append_message("user", f"Perform an impact analysis for this requirement: {task_prompt}")
    impact_report = run_agent_turn(memory)
    
    # Store report in Redis Blackboard
    memory.set_blackboard("impact_report", impact_report)
    publish_event("Impact Analyser", "COMPLETED", impact_report)

    # Stage 2: SWE Coder
    publish_event("SWE Coder", "STARTED", "Writing implementation and unit tests into workspace...")
    coder_memory = AgentMemory(f"{session_id}:coder")
    coder_memory.append_message("system", load_profile("coder"))
    coder_memory.append_message(
        "user",
        f"Task: {task_prompt}\n\nImpact Analysis Report:\n{memory.get_blackboard('impact_report')}\n\nPlease generate the required source code and save it using write_file."
    )
    coding_summary = run_agent_turn(coder_memory)
    
    # Store summary in Redis Blackboard
    memory.set_blackboard("coding_summary", coding_summary)
    publish_event("SWE Coder", "COMPLETED", coding_summary)

    # Stage 3: Reviewer
    publish_event("Reviewer Agent", "STARTED", "Auditing workspace implementation against guardrails...")
    reviewer_memory = AgentMemory(f"{session_id}:reviewer")
    reviewer_memory.append_message("system", load_profile("reviewer"))
    reviewer_memory.append_message(
        "user",
        f"Original Task: {task_prompt}\n\nCoder Summary:\n{memory.get_blackboard('coding_summary')}\n\nRead the generated files in workspace and audit them for correctness, security, and quality."
    )
    review_verdict = run_agent_turn(reviewer_memory)
    
    # Store final verdict in Redis Blackboard
    memory.set_blackboard("review_verdict", review_verdict)
    publish_event("Reviewer Agent", "COMPLETED", review_verdict)

    publish_event("Orchestration Pipeline", "FINISHED", f"Session {session_id} state persisted in Redis.")
    return {
        "session_id": session_id,
        "impact_report": impact_report,
        "coding_summary": coding_summary,
        "review_verdict": review_verdict
    }

if __name__ == "__main__":
    task = "Implement a user authentication module in auth_service.py with JWT verification and unit tests in test_auth_service.py."
    result = run_orchestrator(task)
    print("\n================ FINAL REVIEW VERDICT ================\n")
    print(result["review_verdict"])