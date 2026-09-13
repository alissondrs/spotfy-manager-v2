#!/usr/bin/env python3
"""Valida a política de branches de PRs (fluxo pré-develop).

Usa apenas a biblioteca padrão (Python >= 3.11). O arquivo de política
`.github/pr-policy.yml` é um subconjunto YAML mínimo, parseado por
`parse_policy_minimal` quando PyYAML não estiver disponível; se PyYAML
existir no ambiente, ele é usado como parser preferido e os resultados
são cruzados (a menos de PR_POLICY_NO_YAML=1, usado nos testes).

Fail-closed:
  - política ausente, vazia ou malformada       -> falha
  - base do PR sem regra correspondente no YAML -> falha
  - head do PR fora das regras da base          -> falha

Modos de uso (CI usa as env PR_HEAD/PR_BASE):
  python3 scripts/validate_pr_policy.py
  python3 scripts/validate_pr_policy.py --head pre-develop/x --base develop
  PR_HEAD=x PR_BASE=develop python3 scripts/validate_pr_policy.py
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY = REPO_ROOT / ".github" / "pr-policy.yml"

_ALLOWED_ITEM_KEYS = {"base", "head_exact", "head_pattern", "reason"}
_REQUIRED_ITEM_KEYS = {"base", "reason"}
_HEAD_KEYS = {"head_exact", "head_pattern"}


# ---------------------------------------------------------------------------
# Parsing da política
# ---------------------------------------------------------------------------


def _clean_scalar(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        quote = value[0]
        return value[1:-1].replace("\\" + quote, quote).replace("\\\\", "\\")
    return value


def parse_policy_minimal(text: str) -> dict:
    """Parser estrito do subconjunto YAML usado em pr-policy.yml."""
    result: dict = {}
    rules: list[dict] = []
    current: dict | None = None
    in_rules = False

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())

        if indent == 0:
            key, _, rest = stripped.partition(":")
            if key == "rules":
                in_rules = True
                current = None
                continue
            if key == "version":
                in_rules = False
                current = None
                try:
                    result["version"] = int(_clean_scalar(rest))
                except ValueError as exc:  # pragma: no cover
                    raise ValueError(
                        f"linha {lineno}: version deve ser inteiro"
                    ) from exc
                continue
            raise ValueError(f"linha {lineno}: chave de topo desconhecida: {key!r}")

        if not in_rules:
            raise ValueError(f"linha {lineno}: estrutura fora de rules (indent={indent})")

        if indent == 2 and stripped.startswith("-"):
            if current is not None:
                rules.append(current)
            current = {}
            payload = stripped[1:].strip()
            if payload:
                k, _, v = payload.partition(":")
                if not k or not v:
                    raise ValueError(f"linha {lineno}: item de regra inválida")
                current[k.strip()] = _clean_scalar(v)
            continue

        if indent == 4:
            if current is None:
                raise ValueError(f"linha {lineno}: chave sem item de regra")
            k, _, v = stripped.partition(":")
            if not k or not v:
                raise ValueError(f"linha {lineno}: chave de regra inválida")
            current[k.strip()] = _clean_scalar(v)
            continue

        raise ValueError(f"linha {lineno}: estrutura inesperada (indent={indent})")

    if current is not None:
        rules.append(current)
    if not in_rules:
        raise ValueError("bloco rules: ausente")
    result["rules"] = rules
    return result


def load_policy(path: Path) -> dict:
    """Carrega e valida a política a partir do arquivo YAML."""
    if not path.exists():
        raise FileNotFoundError(f"política não encontrada: {path}")
    text = path.read_text(encoding="utf-8")

    minimal = parse_policy_minimal(text)
    yaml_mod = None
    try:
        if os.environ.get("PR_POLICY_NO_YAML"):
            raise ImportError("PR_POLICY_NO_YAML forçado")
        import yaml  # type: ignore

        yaml_mod = yaml
    except ImportError:
        yaml_mod = None

    if yaml_mod is not None:
        real = yaml_mod.safe_load(text) or {}
        real_rules = real.get("rules") or []
        if real_rules != minimal["rules"]:
            raise ValueError("discrepância: parser mínimo difere do PyYAML")

    rules = minimal["rules"]
    if not rules:
        raise ValueError("política sem regras (protocolo fail-closed)")
    versions = {r.get("base") for r in rules}
    missing = {"develop", "main"} - versions
    if missing:
        raise ValueError(f"política sem regra para as bases: {sorted(missing)}")

    for rule in rules:
        extra = set(rule) - _ALLOWED_ITEM_KEYS
        if extra:
            raise ValueError(f"chave(s) desconhecida(s) na regra: {sorted(extra)}")
        miss = _REQUIRED_ITEM_KEYS - set(rule)
        if miss:
            raise ValueError(f"regra sem campo(s) obrigatório(s): {sorted(miss)}")
        if not rule["base"].strip():
            raise ValueError("regra com base vazia")
        heads = _HEAD_KEYS & set(rule)
        if len(heads) != 1:
            raise ValueError(
                "regra deve ter exatamente um de head_exact/head_pattern"
            )
        if "reason" in rule and not str(rule["reason"]).strip():
            raise ValueError("regra com reason vazio")
    return minimal


def evaluate(head: str, base: str, policy: dict) -> tuple[bool, str]:
    """Aplica head/base contra as regras. Retorna (passou, diagnóstico)."""
    if not head or not base:
        return False, f"head/base ausentes (head={head!r}, base={base!r})"
    relevant = [r for r in policy["rules"] if r["base"] == base]
    if not relevant:
        return False, f"nenhuma regra para base={base!r}"
    failures: list[str] = []
    for rule in relevant:
        if "head_exact" in rule:
            ok = head == rule["head_exact"]
        else:
            ok = fnmatch.fnmatch(head, rule["head_pattern"])
        if ok:
            reason = rule.get("reason", "")
            return True, f"ok: {head} -> {base} ({reason})".strip()
        failures.append(
            rule.get("head_exact") or rule.get("head_pattern") or "<?>"
        )
    return (
        False,
        f"inaprovado: head={head!r} para base={base!r}; esperado: {', '.join(failures)}",
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Valida a topologia de branches de PRs (fluxo pré-develop)."
    )
    parser.add_argument("--head", default=os.environ.get("PR_HEAD", ""))
    parser.add_argument("--base", default=os.environ.get("PR_BASE", ""))
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path(os.environ.get("PR_POLICY_FILE", DEFAULT_POLICY)),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _resolve_args(argv)
    try:
        policy = load_policy(args.policy)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[pr-policy] erro ao carregar política: {exc}", file=sys.stderr)
        return 2
    passed, msg = evaluate(args.head, args.base, policy)
    print(f"[pr-policy] {msg}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())