"""Check a publication file manifest for credential literals and private files.

Usage: python scripts/audit_public.py manifest.json [repository-root]
The JSON manifest is an array of paths obtained from `git ls-files`.
This focused check complements manual review; it is not a full security audit.
"""
import ast
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent
paths = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
findings = []
secret_pattern = re.compile(r"password|passwd|pwd|secret|token|api_key", re.I)
token_pattern = re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
private_prefixes = ("uploads/", "reports/", "mysql/", "endpoint-exe/")


def safe_literal(value):
    return not value or value.startswith(("Demo", "replace-with-", "demo-"))


for name in paths:
    path = root / name
    if name == ".env" or name == "connection.json" or name.startswith(private_prefixes) or path.suffix in (".docx", ".frm", ".MYD", ".MYI", ".exe"):
        findings.append(f"{name}: private file staged")
    if path.suffix not in (".py", ".html", ".js", ".css", ".json", ".md", ".txt", ".yaml") and name != ".env.example":
        continue
    raw = path.read_bytes()
    source = raw.decode("utf-16" if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else "utf-8-sig")
    if token_pattern.search(source):
        findings.append(f"{name}: possible credential token")
    emails = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", source)
    if any(email != "demo@example.com" for email in emails):
        findings.append(f"{name}: non-demo email literal")
    if path.suffix == ".py":
        tree = ast.parse(source, filename=name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                targets = [n.id for target in node.targets for n in ast.walk(target) if isinstance(n, ast.Name)]
                targets += [target.attr for target in node.targets if isinstance(target, ast.Attribute)]
                if any(secret_pattern.search(target) for target in targets) and not safe_literal(node.value.value):
                    findings.append(f"{name}:{node.lineno}: credential literal assignment")
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if (isinstance(key, ast.Constant) and isinstance(key.value, str) and secret_pattern.search(key.value)
                            and isinstance(value, ast.Constant) and isinstance(value.value, str) and not safe_literal(value.value)):
                        findings.append(f"{name}:{value.lineno}: credential literal in dictionary")

if findings:
    for finding in findings:
        print(finding)
    raise SystemExit(1)
print(f"Publication audit passed: {len(paths)} files; no private files or non-demo credential/email literals detected.")
