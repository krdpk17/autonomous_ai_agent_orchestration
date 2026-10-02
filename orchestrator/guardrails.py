import re
import ast

# 1. Shell Command Blocklist (prevents destructive commands & unauthorized system modifications)
DANGEROUS_COMMAND_PATTERNS = [
    r"\brm\s+-[rf]{1,2}\b",        # rm -rf, rm -r, rm -f
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",  # Fork bombs
    r"\bmkfs\b",                   # Filesystem format
    r"\bdd\s+if=",                 # Raw disk overwrite
    r"\bchmod\s+-R\s+777\b",       # Insecure open permissions
    r"\bgit\s+push\s+--force\b",   # Overwriting remote branches
]

# 2. Blocked Python Imports for AST Inspection
BLOCKED_AST_MODULES = {"os", "subprocess", "shutil", "socket", "pty"}

class GuardrailViolation(Exception):
    """Raised when an agent action triggers a safety policy violation."""
    pass

def validate_shell_command(command: str) -> bool:
    """Inspects shell commands before execution."""
    for pattern in DANGEROUS_COMMAND_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            raise GuardrailViolation(f"BLOCKED: Command matches dangerous pattern '{pattern}': {command}")
    return True

def validate_python_code_ast(code: str) -> bool:
    """Parses Python source code to block unpermitted system/network imports."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise GuardrailViolation(f"SYNTAX ERROR: Code could not be parsed: {e}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split('.')[0] in BLOCKED_AST_MODULES:
                    raise GuardrailViolation(f"BLOCKED: Unauthorized module import '{alias.name}' detected.")
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split('.')[0] in BLOCKED_AST_MODULES:
                raise GuardrailViolation(f"BLOCKED: Unauthorized module import from '{node.module}' detected.")
    return True

def guardrail_interceptor(tool_name: str, arguments: dict) -> None:
    """Central interceptor hook for all agent tool calls."""
    if tool_name in ["run_bash_command", "execute_terminal"]:
        cmd = arguments.get("command", "")
        validate_shell_command(cmd)

    elif tool_name in ["write_file", "patch_file"]:
        filepath = arguments.get("path", "")
        content = arguments.get("content", "")
        # Prevent writing outside the workspace directory
        if ".." in filepath or filepath.startswith("/"):
            raise GuardrailViolation(f"PATH TRAVERSAL: Blocked file operation outside workspace: {filepath}")
        # Run AST check on Python files
        if filepath.endswith(".py"):
            validate_python_code_ast(content)