import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "aws95"
LEGACY = {"core", "ui", "widgets", "database", "credentials_handler", "config", "aws_collector",
          "win95_gui", "qt_compat", "data", "utils", "create_icon", "main"}


def _package(path: Path) -> str:
    """The dotted package a file under aws95/ belongs to (an __init__.py is its own package)."""
    parts = path.resolve().relative_to(ROOT.parent).with_suffix("").parts
    return ".".join(parts[:-1])


def _imports(path: Path, package: str | None = None) -> list[str]:
    """Absolute names of everything a file imports: relative imports resolve against its package, and
    `from aws95 import X` (or `from . import X`) counts as importing the submodule X."""
    package = package if package is not None else _package(path)
    names = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[:len(base) - (node.level - 1)]
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            names.append(module)
            if module in ("aws95", "aws95.contracts") or (node.level and not node.module):
                names += [f"{module}.{a.name}" for a in node.names]
    return names


def _provider_violations(path: Path, package: str | None = None) -> list[str]:
    return [n for n in _imports(path, package) if n.startswith("aws95.providers")]


def test_kit_does_not_import_upper_layers_or_other_contracts():
    for path in (ROOT / "kit").rglob("*.py"):
        for name in _imports(path):
            assert not name.startswith(("aws95.providers", "aws95.programs", "aws95.shell")), f"{path} imports {name}"
            if name.startswith("aws95.contracts"):
                assert name == "aws95.contracts.common", f"{path} imports {name}"


def test_no_legacy_top_level_imports():
    for path in ROOT.rglob("*.py"):
        for name in _imports(path):
            assert name.split(".")[0] not in LEGACY, f"{path} imports legacy {name}"


def test_contracts_import_no_qt_or_boto3_layer():
    for path in (ROOT / "contracts").rglob("*.py"):
        for name in _imports(path):
            assert not name.startswith(("PySide6", "boto3", "botocore")), f"{path} imports {name}"


def test_programs_do_not_import_providers():
    for path in (ROOT / "programs").rglob("*.py"):
        assert not _provider_violations(path), f"{path} imports {_provider_violations(path)}"


def test_relative_import_resolved_against_the_files_package(tmp_path):
    src = tmp_path / "x.py"
    src.write_text("from ...providers import demo\nfrom . import common\nfrom .common import paging\n")
    names = _imports(src, package="aws95.programs.billing")
    assert "aws95.providers" in names and "aws95.programs.billing.common" in names
    assert _provider_violations(src, package="aws95.programs.billing")


def test_from_aws95_import_is_a_submodule_import(tmp_path):
    src = tmp_path / "y.py"
    src.write_text("from aws95 import providers\n")
    assert "aws95.providers" in _imports(src, package="aws95.programs")
    assert _provider_violations(src, package="aws95.programs")


def test_package_of_real_files():
    assert _package(ROOT / "programs" / "billing" / "__init__.py") == "aws95.programs.billing"
    assert _package(ROOT / "programs" / "common" / "base.py") == "aws95.programs.common"


def test_only_aws_package_imports_boto():
    for path in ROOT.rglob("*.py"):
        if (ROOT / "aws") in path.parents:
            continue
        for name in _imports(path):
            assert not name.startswith(("boto3", "botocore")), f"{path} imports {name}"


def test_boto3_only_in_session_and_no_botocore_session():
    for path in (ROOT / "aws").rglob("*.py"):
        for name in _imports(path):
            if name.startswith("boto3"):
                assert path.name == "session.py", f"{path} imports {name}"
            assert not name.startswith("botocore.session"), f"{path} imports {name}"


def test_only_live_providers_import_aws_package():
    for path in ROOT.rglob("*.py"):
        if (ROOT / "aws") in path.parents or (ROOT / "providers" / "live") in path.parents:
            continue
        for name in _imports(path):
            assert not name.startswith("aws95.aws"), f"{path} imports {name}"


def test_store_imports_no_qt_boto_or_aws_layer():
    for path in (ROOT / "store").rglob("*.py"):
        for name in _imports(path):
            assert not name.startswith(("PySide6", "boto3", "botocore", "aws95.aws", "aws95.providers",
                                        "aws95.shell", "aws95.programs", "aws95.kit")), f"{path} imports {name}"


REPO = ROOT.parent
PHASE1_LEFTOVERS = ("core/", "database.py", "credentials_handler.py", "config.py", "lambda_report.py", "s3_report.py")


def test_phase1_legacy_files_are_gone():
    """Tracked files only (review Minor 9): an untracked core/__pycache__ left on a developer's disk is not a leftover."""
    import subprocess
    tracked = subprocess.run(["git", "-C", str(REPO), "ls-files"], capture_output=True, text=True,
                             check=True).stdout.splitlines()
    assert [p for p in tracked if any(p == n or (n.endswith("/") and p.startswith(n)) for n in PHASE1_LEFTOVERS)] == []
