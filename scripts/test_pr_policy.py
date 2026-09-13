"""Testes da política pr-policy: parsing YAML, regras e fail-closed.

Roda com apenas stdlib + pytest (sem dependências novas do projeto).
PyYAML é opcional: se disponível, cruza parser mínimo com safe_load;
senão, testes de cross-check são pulados.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

POLICY_PATH = Path(__file__).resolve().parent.parent / ".github" / "pr-policy.yml"
WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "pr-policy.yml"
)
VALIDATOR_PATH = Path(__file__).resolve().parent.parent / "scripts" / "validate_pr_policy.py"

# ---------------------------------------------------------------------------
# import do validador (funciona sem alterar sys.path porque pytest já resolve)
# ---------------------------------------------------------------------------

sys_path_bak = os.environ.get("PYTHONPATH", "")
os.environ["PYTHONPATH"] = str(VALIDATOR_PATH.parent) + os.pathsep + os.environ.get(
    "PYTHONPATH", ""
)
import validate_pr_policy as v  # noqa: E402
os.environ["PYTHONPATH"] = sys_path_bak


# ========================================================================
# helpers
# ========================================================================


def _valid_policy_yaml() -> str:
    return textwrap.dedent("""\
        # comentário
        version: 1

        rules:
          - base: develop
            head_pattern: "pre-develop/*"
            reason: "pre-develop para develop"
          - base: main
            head_exact: develop
            reason: "produce só via develop"
    """)


def _eval(head: str, base: str) -> tuple[bool, str]:
    policy = v.parse_policy_minimal(_valid_policy_yaml())
    return v.evaluate(head, base, policy)


# ========================================================================
# Parser mínimo — casos válidos
# ========================================================================


class TestParserValidCases:
    def test_parse_with_comments_and_blanks(self):
        text = _valid_policy_yaml()
        result = v.parse_policy_minimal(text)
        assert result["version"] == 1
        assert len(result["rules"]) == 2

    def test_develop_rule_has_head_pattern(self):
        rules = v.parse_policy_minimal(_valid_policy_yaml())["rules"]
        assert rules[0]["base"] == "develop"
        assert rules[0]["head_pattern"] == "pre-develop/*"

    def test_main_rule_has_head_exact(self):
        rules = v.parse_policy_minimal(_valid_policy_yaml())["rules"]
        assert rules[1]["base"] == "main"
        assert rules[1]["head_exact"] == "develop"

    def test_quoted_values_are_unescaped(self):
        text = textwrap.dedent("""\
            version: 1
            rules:
              - base: develop
                head_pattern: "pre-develop/*"
                reason: "foo \\"bar\\" baz"
        """)
        rules = v.parse_policy_minimal(text)["rules"]
        assert rules[0]["reason"] == 'foo "bar" baz'


# ========================================================================
# Parser mínimo — casos inválidos
# ========================================================================


class TestParserInvalidCases:
    def test_empty_file(self):
        with pytest.raises(ValueError, match="rules"):
            v.parse_policy_minimal("")

    def test_no_rules_block(self):
        with pytest.raises(ValueError, match="rules"):
            v.parse_policy_minimal("version: 1\n")

    def test_rule_without_base(self, tmp_path):
        text = textwrap.dedent("""\
            version: 1
            rules:
              - head_pattern: "pre-develop/*"
                reason: "ok"
        """)
        p = tmp_path / "no_base.yml"
        p.write_text(text)
        with pytest.raises(ValueError):
            v.load_policy(p)

    def test_rule_without_reason(self, tmp_path):
        text = textwrap.dedent("""\
            version: 1
            rules:
              - base: develop
                head_pattern: "pre-develop/*"
        """)
        p = tmp_path / "no_reason.yml"
        p.write_text(text)
        with pytest.raises(ValueError):
            v.load_policy(p)

    def test_rule_without_head_key(self, tmp_path):
        text = textwrap.dedent("""\
            version: 1
            rules:
              - base: develop
                reason: "oops"
        """)
        p = tmp_path / "no_head.yml"
        p.write_text(text)
        with pytest.raises(ValueError):
            v.load_policy(p)

    def test_unexpected_top_level_key(self):
        text = textwrap.dedent("""\
            version: 1
            funky_key: nope
            rules:
              - base: develop
                head_pattern: "pre-develop/*"
                reason: "ok"
        """)
        with pytest.raises(ValueError, match="desconhecida"):
            v.parse_policy_minimal(text)


# ========================================================================
# load_policy — fail-closed com arquivo real
# ========================================================================


class TestLoadPolicy:
    def test_real_policy_loads(self):
        result = v.load_policy(POLICY_PATH)
        assert result["version"] == 1
        assert len(result["rules"]) == 2

    def test_real_policy_covers_develop_and_main(self):
        result = v.load_policy(POLICY_PATH)
        bases = {r["base"] for r in result["rules"]}
        assert bases == {"develop", "main"}

    def test_missing_file_fail_closed(self):
        with pytest.raises(FileNotFoundError):
            v.load_policy(Path("/tmp/__nonexistent_policy__.yml"))

    def test_empty_file_fail_closed(self, tmp_path):
        p = tmp_path / "empty.yml"
        p.write_text("")
        with pytest.raises(ValueError):
            v.load_policy(p)

    def test_malformed_yaml_fail_closed(self, tmp_path):
        p = tmp_path / "bad.yml"
        p.write_text("{{\nnot yaml at all\n")
        with pytest.raises(ValueError):
            v.load_policy(p)


# ========================================================================
# evaluate — regras de validade
# ========================================================================


class TestEvaluate:
    def test_valid_predevelop_to_develop(self):
        passed, msg = _eval("pre-develop/ci-pr-policy", "develop")
        assert passed
        assert "ok" in msg

    def test_valid_develop_to_main(self):
        passed, msg = _eval("develop", "main")
        assert passed
        assert "ok" in msg

    def test_invalid_feature_to_develop(self):
        passed, msg = _eval("feature/x", "develop")
        assert not passed
        assert "inaprovado" in msg

    def test_invalid_main_to_main(self):
        passed, msg = _eval("main", "main")
        assert not passed

    def test_invalid_predevelop_to_main(self):
        passed, msg = _eval("pre-develop/x", "main")
        assert not passed

    def test_invalid_feature_to_main(self):
        passed, msg = _eval("feature/x", "main")
        assert not passed

    def test_empty_head(self):
        passed, msg = _eval("", "develop")
        assert not passed

    def test_empty_base(self):
        passed, msg = _eval("pre-develop/x", "")
        assert not passed


# ========================================================================
# evaluate — edge cases fnmatch
# ========================================================================


class TestFnmatchEdges:
    def test_exact_predevelop_slash(self):
        passed, _ = _eval("pre-develop/x", "develop")
        assert passed

    def test_nested_path(self):
        passed, _ = _eval("pre-develop/foo/bar/baz", "develop")
        assert passed

    def test_missing_slash_no_match(self):
        passed, _ = _eval("pre-develop", "develop")
        assert not passed

    def test_similar_prefix_no_match(self):
        passed, _ = _eval("pre-developer/foo", "develop")
        assert not passed

    def test_trailing_slash_match(self):
        passed, _ = _eval("pre-develop/ci/", "develop")
        assert passed


# ========================================================================
# Cross-check com PyYAML (se disponível)
# ========================================================================


def _has_pyyaml() -> bool:
    try:
        import yaml  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _has_pyyaml(), reason="PyYAML indisponível no ambiente")
class TestPyYAMLCrossCheck:
    def test_minimal_vs_yaml_safe_load(self):
        text = POLICY_PATH.read_text()
        import yaml

        real = yaml.safe_load(text) or {}
        minimal = v.parse_policy_minimal(text)
        assert real.get("version") == minimal.get("version")
        assert real.get("rules") == minimal.get("rules")

    def test_no_yaml_env_uses_minimal(self, monkeypatch):
        text = POLICY_PATH.read_text()
        monkeypatch.setenv("PR_POLICY_NO_YAML", "1")
        # _clean_scalar e parse_policy_minimal devem ser suficientes
        result = v.parse_policy_minimal(text)
        assert result["version"] == 1
        assert len(result["rules"]) == 2


# ========================================================================
# Consistência do workflow: parser do arquivo pr-policy.yml do workflow
# ========================================================================


def _grep_workflow_safety(path: Path) -> dict[str, bool]:
    """Checagens textuais leves do workflow (sem dependência em PyYAML)."""
    text = path.read_text()
    return {
        "no_pull_request_target": "pull_request_target" not in text,
        "has_pr_policy_job": "pr-policy" in text,
        "contents_read": "contents: read" in text,
        "uses_pull_request_trigger": "pull_request:" in text,
    }


def _workflow_on(w: dict) -> dict:
    top = w.get("on", w.get(True, {}))  # PyYAML 1.1 lê a chave `on` como bool
    return top or {}


@pytest.mark.skipif(not _has_pyyaml(), reason="PyYAML indisponível no ambiente")
class TestWorkflowConsistency:
    def _parse_workflow(self):
        import yaml

        return yaml.safe_load(WORKFLOW_PATH.read_text())

    def test_workflow_no_target_event(self):
        w = self._parse_workflow()
        top = _workflow_on(w)
        assert "pull_request" in top
        assert len(top) == 1
        assert "pull_request" in top

    def test_workflow_permissions_contents_read(self):
        w = self._parse_workflow()
        assert w.get("permissions", {}).get("contents") == "read"

    def test_workflow_on_pull_request_branches(self):
        w = self._parse_workflow()
        branches = _workflow_on(w).get("pull_request", {}).get("branches", [])
        assert set(branches) == {"develop", "main"}

    def test_workflow_has_pr_policy_job(self):
        w = self._parse_workflow()
        jobs = w.get("jobs", {})
        assert "pr-policy" in jobs

    def test_workflow_job_has_validate_step(self):
        w = self._parse_workflow()
        steps = w["jobs"]["pr-policy"].get("steps", [])
        labels = [s.get("name", "") for s in steps]
        assert any("pr-policy.yml" in l for l in labels)


class TestWorkflowSafetyWithoutYaml:
    def test_no_pull_request_target(self):
        checks = _grep_workflow_safety(WORKFLOW_PATH)
        assert checks["no_pull_request_target"]

    def test_contents_read_present(self):
        checks = _grep_workflow_safety(WORKFLOW_PATH)
        assert checks["contents_read"]

    def test_job_name_present(self):
        checks = _grep_workflow_safety(WORKFLOW_PATH)
        assert checks["has_pr_policy_job"]

    def test_on_pull_request(self):
        checks = _grep_workflow_safety(WORKFLOW_PATH)
        assert checks["uses_pull_request_trigger"]


# ========================================================================
# CLI do validador
# ========================================================================


class TestCLI:
    def test_pass_valid_predevelop(self):
        rc = v.main(["--head", "pre-develop/x", "--base", "develop"])
        assert rc == 0

    def test_pass_valid_develop_to_main(self):
        rc = v.main(["--head", "develop", "--base", "main"])
        assert rc == 0

    def test_fail_invalid_feature_to_develop(self):
        rc = v.main(["--head", "feature/x", "--base", "develop"])
        assert rc == 1

    def test_fail_missing_policy(self):
        rc = v.main(["--head", "x", "--base", "develop", "--policy", "/tmp/nonexistent.yml"])
        assert rc == 2

    def test_cli_with_env_vars(self, monkeypatch):
        monkeypatch.setenv("PR_HEAD", "pre-develop/z")
        monkeypatch.setenv("PR_BASE", "develop")
        monkeypatch.delenv("PR_POLICY_FILE", raising=False)
        rc = v.main(["--policy", str(POLICY_PATH)])
        assert rc == 0