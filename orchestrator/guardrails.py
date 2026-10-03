import ast
from pathlib import Path
from config import settings

class GuardrailViolation(Exception):
    """Raised when an agent action violates safety boundaries."""
    pass

# Modules that are completely forbidden under any circumstance
STRICTLY_BLOCKED_MODULES = {
    "subprocess",
    "shutil",
    "pty",
    "socket",
    "http",
    "urllib",
    "requests",
    "ftplib",
}

# Dangerous call targets on otherwise permitted modules
DISALLOWED_OS_CALLS = {
    "system",
    "popen",
    "spawn",
    "exec",
    "execl",
    "execle",
    "execlp",
    "execv",
    "execve",
    "execvp",
    "kill",
    "remove",
    "unlink",
    "rmdir",
}

class ASTSecurityChecker(ast.NodeVisitor):
    def __init__(self):
        self.violations = []

    def visit_Import(self, node):
        for alias in node.names:
            base_module = alias.name.split(".")[0]
            if base_module in STRICTLY_BLOCKED_MODULES:
                self.violations.append(f"BLOCKED: Module '{base_module}' is forbidden.")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module:
            base_module = node.module.split(".")[0]
            if base_module in STRICTLY_BLOCKED_MODULES:
                self.violations.append(f"BLOCKED: Module '{base_module}' is forbidden.")
            
            # Catch direct dangerous imports: e.g. from os import system, popen
            if base_module == "os":
                for alias in node.names:
                    if alias.name in DISALLOWED_OS_CALLS:
                        self.violations.append(f"BLOCKED: 'os.{alias.name}' is dangerous and forbidden.")
        self.generic_visit(node)

    def visit_Call(self, node):
        # Catch method calls like os.system(...) or os.popen(...)
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                module_name = node.func.value.id
                method_name = node.func.attr
                if module_name == "os" and method_name in DISALLOWED_OS_CALLS:
                    self.violations.append(f"BLOCKED: Invocations of 'os.{method_name}()' are not permitted.")
        self.generic_visit(node)


def audit_code_safety(code_content: str):
    """Parses Python code into an AST and inspects imports and function calls."""
    try:
        tree = ast.parse(code_content)
    except SyntaxError:
        # If code has invalid syntax, let the compiler/test runner catch it
        return

    checker = ASTSecurityChecker()
    checker.visit(tree)

    if checker.violations:
        raise GuardrailViolation(" | ".join(checker.violations))


def guardrail_interceptor(tool_name: str, args: dict):
    """Enforces sandboxing and AST-level safety rules on tool calls."""
    if tool_name in ("write_file", "read_file"):
        path_str = args.get("path", "")
        target_path = (settings.WORKSPACE_DIR / path_str).resolve()
        
        # Guardrail: Prevent directory traversal outside workspace
        try:
            target_path.relative_to(settings.WORKSPACE_DIR.resolve())
        except ValueError:
            raise GuardrailViolation(f"Access Denied: Path '{path_str}' escapes sandboxed workspace.")

    if tool_name == "write_file":
        content = args.get("content", "")
        path_str = args.get("path", "")
        if path_str.endswith(".py"):
            audit_code_safety(content)