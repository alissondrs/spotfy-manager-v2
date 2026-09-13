"""Testes do job `pip-audit` do CI (`.github/workflows/ci.yml`).

Regressão: `cat services/*/requirements.txt | sort -u` concatenava o fim de um
requirements.txt que não termina em newline com o início do seguinte, gerando
linhas inválidas como `httpx>=0.27` + `fastapi>=0.115` ->
`httpx>=0.27fastapi>=0.115`, que quebravam o `pip-audit`. O job agora normaliza
a separação por newline entre arquivos ANTES do `sort -u` (`awk 1`).

Estes testes garantem que:
- o comando no job usa a separação por newline (falha se o comportamento antigo
  for reintroduzido no `ci.yml`);
- a saída sobre os arquivos reais do repo só contém requirements bem formados.

Roda com apenas stdlib + pytest (sem dependências novas); PyYAML é opcional e,
quando disponível, cruza a extração do comando via parse estrutural.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CI_PATH = REPO / ".github" / "workflows" / "ci.yml"
REQ_PATTERN = "services/*/requirements.txt"
PIP_AUDIT_STEP = "Auditoria de dependências (pip-audit)"
REQS_TMP = "/tmp/reqs.txt"

# pip-ignore linhas em branco e comentários.
_IGNORED_RE = re.compile(r"^\s*(#.*)?$")
_REQUIREMENT_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9_.\-\[\]]*)(?:\s*(?:===|==|~=|!=|<=|>=|>|<)\s*"
    r"(?P<version>[0-9A-Za-z.*+!_\-]+))?\s*$"
)


# ========================================================================
# helpers
# ========================================================================


def _req_files() -> list[Path]:
    return sorted(REPO.glob(REQ_PATTERN))


def _raw_cat() -> str:
    """Replica `cat services/*/requirements.txt` (concatenação exata de bytes/linhas)."""
    return "".join(p.read_text(encoding="utf-8") for p in _req_files())


def _newline_safe_concat() -> str:
    """Replica `awk 1 services/*/requirements.txt` (cada arquivo termina em newline)."""
    parts = []
    for p in _req_files():
        text = p.read_text(encoding="utf-8")
        parts.append(text if text.endswith("\n") else text + "\n")
    return "".join(parts)


def _sorted_unique(text: str) -> list[str]:
    lines = [l for l in text.splitlines() if not _IGNORED_RE.match(l)]
    return sorted(set(lines))


def _is_well_formed(line: str) -> bool:
    return _REQUIREMENT_RE.fullmatch(line) is not None


def _ci_text() -> str:
    return CI_PATH.read_text(encoding="utf-8")


def _has_pyyaml() -> bool:
    try:
        import yaml  # noqa: F401

        return True
    except ImportError:
        return False


def _reqs_assembly_line() -> str:
    """Extrai do job pip-audit a linha que monta `$REQS_TMP`."""
    text = _ci_text()
    start = text.index(PIP_AUDIT_STEP)
    tail = text[start:]
    end = tail.index(f" > {REQS_TMP}")
    block = tail[: end + len(REQS_TMP) + 3]
    assembly = f" > {REQS_TMP}"
    hits = [l for l in block.splitlines() if assembly in l]
    assert len(hits) == 1, f"esperada uma linha montando {REQS_TMP}"
    return hits[0].strip()


# ========================================================================
# Regressão: o comportamento antigo (cat simples) gera linha inválida
# ========================================================================


class TestOldBehaviorIsBroken:
    def test_raw_cat_merges_files_without_newline(self):
        lines = _sorted_unique(_raw_cat())
        assert "httpx>=0.27fastapi>=0.115" in lines

    def test_raw_cat_output_has_malformed_requirement(self):
        lines = _sorted_unique(_raw_cat())
        assert any(not _is_well_formed(l) for l in lines)


# ========================================================================
# O novo comportamento valida: separação por newline gera output limpo
# ========================================================================


class TestNewBehaviorIsWellFormed:
    def test_newline_safe_concat_unique_sorted(self):
        lines = _sorted_unique(_newline_safe_concat())
        assert lines
        assert all(_is_well_formed(l) for l in lines)

    def test_newline_safe_concat_parses_every_requirement(self):
        lines = _sorted_unique(_newline_safe_concat())
        for l in lines:
            ver = _REQUIREMENT_RE.fullmatch(l)
            assert ver is not None, f"{l!r} malformado"
            assert ver.group("version") is not None, f"{l!r} sem versão"

    def test_every_service_present_in_merged_set(self):
        merged = set(_sorted_unique(_newline_safe_concat()))
        for f in _req_files():
            for l in f.read_text(encoding="utf-8").splitlines():
                if _IGNORED_RE.match(l):
                    continue
                assert l.strip() in merged, f"{f.name}: {l!r} ausente"


# ========================================================================
# Guarante de que o ci.yml mantém a separação por newline no job pip-audit
# ========================================================================


class TestCiPipAuditUsesSafeAssembly:
    def test_pip_audit_step_exists(self):
        assert PIP_AUDIT_STEP in _ci_text()

    def test_assembly_uses_newline_separation_before_sort(self):
        line = _reqs_assembly_line()
        assert "awk 1 " in line
        assert line.endswith(f" > {REQS_TMP}")
        assert "| sort -u" in line

    def test_assembly_is_not_the_vulnerable_cat_pipeline(self):
        line = _reqs_assembly_line()
        assert "cat services/*/requirements.txt" not in line
        assert re.search(r"\bcat\b.*\|\s*sort", line) is None

    def test_assembly_orders_files_concat_then_sort(self):
        line = _reqs_assembly_line()
        first, _, rest = line.partition("|")
        assert REQ_PATTERN in first
        assert "sort -u" in rest


@pytest.mark.skipif(
    not _has_pyyaml(), reason="PyYAML indisponível no ambiente"
)
class TestCiPipAuditStructureYaml:
    def _pip_audit_run(self):
        import yaml

        w = yaml.safe_load(_ci_text())
        steps = w["jobs"]["pip-audit"]["steps"]
        run = next(s["run"] for s in steps if s.get("name") == PIP_AUDIT_STEP)
        return run

    def test_run_script_has_safe_assembly_line(self):
        run = self._pip_audit_run()
        assembly = f" > {REQS_TMP}"
        hits = [l for l in run.splitlines() if assembly in l]
        assert len(hits) == 1
        line = hits[0].strip()
        assert "awk 1 " in line
        assert "| sort -u" in line

    def test_run_script_has_no_raw_cat_pipeline(self):
        run = self._pip_audit_run()
        assert "cat services/*/requirements.txt" not in run