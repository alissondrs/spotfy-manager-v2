"""Testes da política pr-policy: parsing YAML, regras e fail-closed.

Roda com apenas stdlib + pytest (sem dependências novas do projeto).
PyYAML é opcional: se disponível, cruza parser mínimo com safe_load;
senão, testes de cross-check são pulados.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

POLICY_PATH = Path(__file__).resolve().parent.parent / ".github" / "pr-policy.yml"
WORKFLOW_PATH = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "pr-policy.yml"
)
VALIDATOR_PATH = Path(__file__).resolve().parent.parent / "scripts" / "validate_pr_policy.py"
CI_CD_PATH = Path(__file__).resolve().parent.parent / "docs" / "ci-cd.md"
AGENTS_PATH = Path(__file__).resolve().parent.parent / "AGENTS.md"

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
# Consistência do workflow: extração da lógica inline vs .github/pr-policy.yml
# ========================================================================


def _inline_python_source() -> str:
    """Extrai o código Python embutido no passo de validação do workflow.

    O heredoc `<<'PY' ... PY` é a única lógica executada pelo check; nada de
    armazenado no repo é rodado. Essa extração textual (sem YAML) espelha o
    que o passo injeta via stdin no python do runner.
    """
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    marker = "<<'PY'"
    start = text.index(marker) + len(marker)
    if start < len(text) and text[start] == "\n":
        start += 1
    body: list[str] = []
    for line in text[start:].splitlines():
        if line.strip() == "PY":
            return textwrap.dedent("\n".join(body))
        body.append(line)
    raise AssertionError("fim do heredoc PY não encontrado no workflow")


def _inline_rules() -> list[dict]:
    """Executa somente o bloco inline (stdlib) e extrai as regras RULES."""
    ns: dict = {}
    exec(compile(_inline_python_source(), "<pr-policy.yml inline>", "exec"), ns)
    return [dict(r) for r in ns["RULES"]]


def _normalize_rules(rules: list[dict]) -> list[dict]:
    norm = []
    for r in rules:
        norm.append(
            {
                "base": r["base"],
                "head_exact": r.get("head_exact"),
                "head_pattern": r.get("head_pattern"),
            }
        )
    return sorted(norm, key=lambda r: (r["base"], r["head_exact"] or "", r["head_pattern"] or ""))


def _workflow_on(w: dict) -> dict:
    top = w.get("on", w.get(True, {}))  # PyYAML 1.1 lê a chave `on` como bool
    return top or {}


class TestInlineRulesConsistency:
    def test_inline_rules_match_policy_file(self):
        policy = [dict(r) for r in v.load_policy(POLICY_PATH)["rules"]]
        assert _normalize_rules(_inline_rules()) == _normalize_rules(policy)

    def test_inline_rules_cover_develop_and_main(self):
        bases = {r["base"] for r in _inline_rules()}
        assert bases == {"develop", "main"}


class TestInlineBehaviorParity:
    """Os mesmos pares (head, base) devem dar o mesmo veredito na lógica inline
    (rodada como subprocesso, só com o bloco extraído) e no validador local."""

    HEADS = [
        "pre-develop/x",
        "pre-develop/a/b/c",
        "pre-develop",
        "pre-developer/x",
        "develop",
        "main",
        "feature/x",
        "release/1.0",
        "chore/x",
        "",
    ]
    BASES = ["develop", "main", "feature", "develop-prod", ""]

    def test_parity_matrix(self):
        src = _inline_python_source()
        for head in self.HEADS:
            for base in self.BASES:
                expected_rc = v.main(["--head", head, "--base", base])
                proc = subprocess.run(
                    [sys.executable, "-", head, base],
                    input=src,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                assert proc.returncode in (0, 1), (head, base, proc.returncode, proc.stderr)
                assert (proc.returncode == 0) == (expected_rc == 0), (
                    head,
                    base,
                    expected_rc,
                    proc.returncode,
                    proc.stdout,
                    proc.stderr,
                )


# ========================================================================
# Segurança do workflow: ausência de checkout/pip/execução de código do PR
# ========================================================================


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


class TestWorkflowNoUntrustedExecution:
    """Checagens textuais (sem PyYAML): o check usa pull_request_target com
    lógica inline e NÃO clona o head, não instala deps e não roda scripts."""

    def test_pull_request_target_instead_of_pull_request(self):
        text = _workflow_text()
        assert "pull_request_target" in text
        assert "pull_request:" not in text

    def test_no_checkout_of_pr_code(self):
        assert "actions/checkout" not in _workflow_text()

    def test_no_pip_install(self):
        assert "pip" not in _workflow_text()

    def test_no_repo_scripts_execution(self):
        text = _workflow_text()
        assert "scripts/" not in text
        assert "validate_pr_policy" not in text
        assert "pytest" not in text

    def test_no_secrets_or_token(self):
        text = _workflow_text()
        assert "secrets." not in text
        assert "GITHUB_TOKEN" not in text

    def test_no_uses_steps(self):
        assert "uses:" not in _workflow_text()

    def test_permissions_contents_read(self):
        assert "contents: read" in _workflow_text()

    def test_job_pr_policy_exists(self):
        assert "pr-policy" in _workflow_text()


@pytest.mark.skipif(not _has_pyyaml(), reason="PyYAML indisponível no ambiente")
class TestWorkflowStructure:
    def _parse_workflow(self):
        import yaml

        return yaml.safe_load(WORKFLOW_PATH.read_text())

    def test_workflow_uses_pull_request_target_only(self):
        w = self._parse_workflow()
        top = _workflow_on(w)
        assert "pull_request_target" in top
        assert "pull_request" not in top
        assert len(top) == 1

    def test_pull_request_target_branches(self):
        w = self._parse_workflow()
        branches = _workflow_on(w)["pull_request_target"].get("branches", [])
        assert set(branches) == {"develop", "main"}

    def test_workflow_permissions_contents_read(self):
        w = self._parse_workflow()
        assert w.get("permissions", {}).get("contents") == "read"

    def test_workflow_has_pr_policy_job(self):
        w = self._parse_workflow()
        assert "pr-policy" in w.get("jobs", {})

    def test_steps_have_no_uses(self):
        w = self._parse_workflow()
        steps = w["jobs"]["pr-policy"].get("steps", [])
        assert steps
        for step in steps:
            assert "uses" not in step

    def test_validate_step_is_inline_heredoc(self):
        w = self._parse_workflow()
        run = w["jobs"]["pr-policy"]["steps"][0]["run"]
        assert "<<'PY'" in run
        assert "$PR_HEAD" in run and "$PR_BASE" in run

    def test_validate_step_binds_event_head_base(self):
        w = self._parse_workflow()
        env = w["jobs"]["pr-policy"]["steps"][0]["env"]
        assert env["PR_HEAD"] == "${{ github.event.pull_request.head.ref }}"
        assert env["PR_BASE"] == "${{ github.event.pull_request.base.ref }}"


# ========================================================================
# Bootstrap: a documentação deve exigir a branch padrão (main) antes da ativação
# ========================================================================
#
# `pull_request_target` roda no contexto da branch padrão do repo (aqui `main`)
# e usa o workflow da branch padrão. O rollout correto é: merge em develop →
# promoção develop→main → PR piloto verde → só então required checks. Estes
# testes garantem que `docs/ci-cd.md`, `AGENTS.md` e o comentário do workflow
# deixam isso explícito antes de qualquer ativação.


def _doc_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestBootstrapDocsRequireDefaultBranch:
    def test_ci_cd_mentions_default_branch_and_main(self):
        text = _doc_text(CI_CD_PATH)
        assert "branch padrão" in text
        assert "`main`" in text

    def test_ci_cd_ties_pull_request_target_to_default_branch(self):
        text = _doc_text(CI_CD_PATH)
        assert "no contexto da branch padrão" in text
        assert "o workflow usado é o arquivo da **branch padrão**" in text

    def test_ci_cd_says_check_only_exists_when_workflow_is_on_main(self):
        text = _doc_text(CI_CD_PATH)
        assert "só passa a existir quando o workflow estiver na branch padrão" in text

    def test_ci_cd_rollout_goes_through_main_before_pilot_and_required(self):
        text = _doc_text(CI_CD_PATH)
        step_develop = text.index("Merge deste workflow em `develop`")
        step_main = text.index("Promova `develop` → `main`")
        step_pilot = text.index("PR piloto")
        step_required = text.index("adicione `pr-policy` aos required checks")
        assert step_develop < step_main < step_pilot < step_required

    def test_ci_cd_required_notes_reference_default_branch(self):
        text = _doc_text(CI_CD_PATH)
        assert "branch padrão `main`" in text

    def test_agents_mentions_default_branch_bootstrap(self):
        text = _doc_text(AGENTS_PATH)
        assert "branch padrão (`main`)" in text
        assert "docs/ci-cd.md" in text
        assert "PR piloto" in text
        assert "required checks" in text

    def test_workflow_comment_mentions_default_branch_main(self):
        text = _workflow_text()
        assert "BRANCH PADRÃO" in text
        assert "main" in text
        assert "PR piloto" in text
        assert "required checks" in text


# ========================================================================
# Autorização (4º commit): push + PR draft automático após PASS local
# ========================================================================
#
# O usuário autorizou: ao concluir o pipeline multiagente/local com revisão
# PASS, testes obrigatórios verdes, branch `pre-develop/*` válida e worktree
# limpa, o orquestrador faz push e abre/atualiza a PR **draft** para develop
# ANTES do GitHub CI (o ci.yml dispara em `pull_request`), passando a
# acompanhar o CI. Nada de ready/auto-merge/merge automático; qualquer check
# falho/skipped/cancelled/ausente/inconclusivo mantém a PR draft e bloqueada.
# Estes testes documentais garantem que AGENTS.md e docs/ci-cd.md registram o
# fluxo e as restrições.


class TestAutoDraftPRDocs:
    def test_agents_mentions_automatic_draft_pr(self):
        text = _doc_text(AGENTS_PATH)
        assert "**Abertura automática de PR draft (autorização)**" in text
        assert "draft" in text

    def test_agents_gates_before_push(self):
        text = _doc_text(AGENTS_PATH)
        for gate in (
            "**revisão PASS**",
            "**testes obrigatórios verdes**",
            "`pre-develop/*` válida",
            "**worktree limpa**",
        ):
            assert gate in text

    def test_agents_pr_opened_before_github_ci(self):
        text = _doc_text(AGENTS_PATH)
        assert "**antes**" in text
        assert "acionado por `pull_request`" in text
        assert "acompanha o CI" in text

    def test_agents_no_ready_auto_merge_merge(self):
        text = _doc_text(AGENTS_PATH)
        assert "marcar `ready`" in text
        assert "auto-merge" in text
        assert "mergear automaticamente" in text

    def test_agents_failure_states_keep_draft_blocked(self):
        text = _doc_text(AGENTS_PATH)
        assert "falha, skipped, cancelled, ausentes ou" in text
        assert "inconclusivos" in text
        assert "draft e bloqueada" in text

    def test_ci_cd_authorized_section_exists(self):
        text = _doc_text(CI_CD_PATH)
        assert "### Abertura automática de PR draft (autorização)" in text

    def test_ci_cd_pr_before_ci_reason_and_monitoring(self):
        text = _doc_text(CI_CD_PATH)
        assert "antes" in text
        assert "acionado por" in text
        assert "`pull_request`" in text
        assert "acompanha o CI" in text

    def test_ci_cd_no_ready_auto_merge_merge(self):
        text = _doc_text(CI_CD_PATH)
        assert "`ready`" in text
        assert "auto-merge" in text
        assert "mergear automaticamente" in text

    def test_ci_cd_failure_states_keep_draft_blocked(self):
        text = _doc_text(CI_CD_PATH)
        assert "falha, skipped, cancelled, ausente ou inconclusivo" in text
        assert "draft e bloqueada" in text


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