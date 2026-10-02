import sys
import re
import json
from pathlib import Path
import redis
from openai import OpenAI

# Resolve root path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import settings
from orchestrator.guardrails import guardrail_interceptor, GuardrailViolation

# Ensure workspace and profiles directories exist
settings.WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
PROFILES_DIR = ROOT_DIR / "profiles"

# --- Redis State & Pub/Sub Initialization ---
r = redis.Redis(
    host=settings.redis.host,
    port=settings.redis.port,
    db=settings.redis.db,
    decode_responses=True
)

def publish_event(stage: str, status: str, details: str):
    """Broadcasts agent lifecycle transitions to Redis Pub/Sub and console."""
    payload = json.dumps({"stage": stage, "status": status, "details": details})
    try:
        channel_name = getattr(settings.redis, "channel", "agent_pipeline_events")
        r.publish(channel_name, payload)
    except Exception as e:
        print(f"Redis warning: {e}")
    print(f"\033[94m[{stage}]\033[0m \033[92m{status}\033[0m: {details}")

# --- Remote Hermes Client ---
client = OpenAI(
    base_url=settings.hermes.base_url,
    api_key=settings.hermes.api_key
)

# Hermes Native Tool Definitions
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
    """Reads system instructions and attaches Hermes native tool instructions."""
    profile_path = PROFILES_DIR / f"{profile_name}.md"
    if not profile_path.exists():
        raise FileNotFoundError(f"Agent profile not found: {profile_path}")
    base_prompt = profile_path.read_text(encoding="utf-8").strip()
    return f"{base_prompt}\n\n{HERMES_SYSTEM_INSTRUCTION}\n{HERMES_TOOLS_SPEC}"

# --- Tool Execution Engine ---
def execute_tool(tool_name: str, args: dict) -> str:
    """Enforces guardrails before executing local workspace operations."""
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
    """Extracts Hermes native <tool_call> tags from generated text."""
    pattern = r"<tool_call>\s*({.*?})\s*</tool_call>"
    matches = re.findall(pattern, text, re.DOTALL)
    calls = []
    for m in matches:
        try:
            calls.append(json.loads(m.strip()))
        except json.JSONDecodeError:
            continue
    return calls

def run_agent_turn(messages: list) -> str:
    """Executes a turn using plain text completions without provider-side tool filters."""
    response = client.chat.completions.create(
        model=settings.hermes.model_name,
        messages=messages,
        temperature=settings.hermes.temperature
    )
    content = response.choices[0].message.content or ""
    messages.append({"role": "assistant", "content": content})

    # Check for native Hermes <tool_call> tags
    tool_calls = parse_hermes_tool_calls(content)
    if tool_calls:
        for call in tool_calls:
            fn_name = call.get("name")
            fn_args = call.get("arguments", {})

            publish_event("Tool Execution", "INVOKING", f"{fn_name}({fn_args})")
            tool_output = execute_tool(fn_name, fn_args)
            publish_event("Tool Response", "RETURNED", tool_output)

            # Hermes expects results formatted inside <tool_response> tags
            tool_response_msg = f"<tool_response>\n{{\"name\": \"{fn_name}\", \"content\": {tool_output}}}\n</tool_response>"
            messages.append({"role": "user", "content": tool_response_msg})

        # Continue the loop so Hermes can finish after seeing tool results
        return run_agent_turn(messages)

    return content

# --- Multi-Agent Orchestration Pipeline ---
def run_orchestrator(task_prompt: str):
    publish_event("Project Ingestion", "STARTED", f"Task: {task_prompt}")

    # Stage 1: Impact Analysis
    publish_event("Impact Analyser", "STARTED", "Assessing repository dependencies and blast radius...")
    analyser_prompt = load_profile("impact_analyser")
    analysis_messages = [
        {"role": "system", "content": analyser_prompt},
        {"role": "user", "content": f"Perform an impact analysis for this requirement: {task_prompt}"}
    ]
    impact_report = run_agent_turn(analysis_messages)
    publish_event("Impact Analyser", "COMPLETED", impact_report)

    # Stage 2: Code Implementation
    publish_event("SWE Coder", "STARTED", "Writing implementation and unit tests into workspace...")
    coder_prompt = load_profile("coder")
    coder_messages = [
        {"role": "system", "content": coder_prompt},
        {
            "role": "user",
            "content": f"Task: {task_prompt}\n\nImpact Analysis Report:\n{impact_report}\n\nPlease generate the required source code and save it using write_file."
        }
    ]
    coding_summary = run_agent_turn(coder_messages)
    publish_event("SWE Coder", "COMPLETED", coding_summary)

    # Stage 3: Verification & Review
    publish_event("Reviewer Agent", "STARTED", "Auditing workspace implementation against guardrails...")
    reviewer_prompt = load_profile("reviewer")
    reviewer_messages = [
        {"role": "system", "content": reviewer_prompt},
        {
            "role": "user",
            "content": f"Original Task: {task_prompt}\n\nCoder Summary:\n{coding_summary}\n\nRead the generated files in workspace and audit them for correctness, security, and quality."
        }
    ]
    review_verdict = run_agent_turn(reviewer_messages)
    publish_event("Reviewer Agent", "COMPLETED", review_verdict)

    publish_event("Orchestration Pipeline", "FINISHED", "All stages completed successfully.")
    return {
        "impact_report": impact_report,
        "coding_summary": coding_summary,
        "review_verdict": review_verdict
    }

if __name__ == "__main__":
    task = "Implement a user authentication module in auth_service.py with JWT verification and unit tests in test_auth_service.py."
    result = run_orchestrator(task)
    print("\n================ FINAL REVIEW VERDICT ================\n")
    print(result["review_verdict"])