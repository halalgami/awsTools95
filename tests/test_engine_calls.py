"""Spec §3.2: gate.LABSNAP equals exactly the set of (service, operation) pairs the copied engine code calls."""
import ast
from pathlib import Path

from aws95.aws import gate

ENGINES = Path(__file__).resolve().parents[1] / "aws95" / "aws" / "engines"
NOT_OPERATIONS = {"pages"}


def _is_gate_attr(node, name):
    return (isinstance(node, ast.Attribute) and node.attr == name
            and isinstance(node.value, ast.Name) and node.value.id == "gate")


def _literal(node, what, path):
    assert isinstance(node, ast.Constant) and isinstance(node.value, str), \
        f"{path}:{getattr(node, 'lineno', '?')}: the {what} must be a string literal"
    return node.value


def _bind_service(node, path):
    if isinstance(node, ast.Call) and _is_gate_attr(node.func, "bind"):
        return _literal(node.args[1], "service", path)
    return None


def engine_calls(root: Path = ENGINES) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text())
        names: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                svc = _bind_service(node.value, path)
                if svc is None:
                    continue
                for target in node.targets:
                    assert isinstance(target, ast.Name), f"{path}: assign gate.bind(...) to a plain name"
                    assert names.get(target.id, svc) == svc, f"{path}: {target.id} is bound to two services"
                    names[target.id] = svc

        def service_of(value, _names=names, _path=path):
            if isinstance(value, ast.Name):
                return _names.get(value.id)
            return _bind_service(value, _path)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (_is_gate_attr(node.func, "call") or _is_gate_attr(node.func, "paginate")):
                pairs.add((_literal(node.args[1], "service", path), _literal(node.args[2], "operation", path)))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "pages":
                svc = service_of(node.func.value)
                if svc is not None:
                    pairs.add((svc, _literal(node.args[0], "operation", path)))
            if isinstance(node, ast.Attribute) and node.attr not in NOT_OPERATIONS:
                svc = service_of(node.value)
                if svc is not None:
                    pairs.add((svc, node.attr))
    return pairs


def test_engines_call_exactly_the_reviewed_pairs():
    found = engine_calls(ENGINES / "labsnap")
    assert found == gate.LABSNAP, (f"called but not reviewed: {sorted(found - gate.LABSNAP)}; "
                                   f"reviewed but never called: {sorted(gate.LABSNAP - found)}")


def test_ctwatch_calls_exactly_the_reviewed_pairs():
    found = engine_calls(ENGINES / "ctwatch")
    assert found == gate.CTWATCH, (f"called but not reviewed: {sorted(found - gate.CTWATCH)}; "
                                   f"reviewed but never called: {sorted(gate.CTWATCH - found)}")


def test_scan_sees_every_call_shape(tmp_path):
    (tmp_path / "m.py").write_text(
        "from aws95.aws import gate\n"
        "def f(session, region, cancel=None):\n"
        "    ec2 = gate.bind(session, 'ec2', region, cancel=cancel)\n"
        "    ec2.describe_instances()\n"
        "    list(ec2.pages('describe_volumes'))\n"
        "    gate.bind(session, 'guardduty', region).list_detectors()\n"
        "    gate.call(session, 'sts', 'get_caller_identity')\n"
        "def g(ec2):\n"
        "    return ec2.describe_security_groups\n")
    assert engine_calls(tmp_path) == {("ec2", "describe_instances"), ("ec2", "describe_volumes"),
                                      ("guardduty", "list_detectors"), ("sts", "get_caller_identity"),
                                      ("ec2", "describe_security_groups")}


def test_insights_calls_exactly_the_reviewed_pairs():
    found = engine_calls(ENGINES.parent / "insights")
    assert found == gate.INSIGHTS, (f"called but not reviewed: {sorted(found - gate.INSIGHTS)}; "
                                    f"reviewed but never called: {sorted(gate.INSIGHTS - found)}")
