"""Column-name mapping: operator/vendor headers -> canonical schema names.

Strategy (in order):
  1. exact match on a normalised alias from the mapping YAML
  2. exact match on the canonical name itself
  3. token-overlap fuzzy match (only when unambiguous and above a score floor)
  4. leave unmapped -> reported so the user can map it by hand in the UI

`normalise_name` is the great equaliser: it lower-cases, strips units in
parentheses/brackets, collapses punctuation and whitespace, and unifies a few
common synonyms ("succ"/"success", "att"/"attempt", ...).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import yaml

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

_UNIT_RE = re.compile(r"[\(\[\{].*?[\)\]\}]")
_NONWORD_RE = re.compile(r"[^a-z0-9]+")

_SYNONYMS = {
    "succ": "success", "success": "success", "successes": "success",
    "att": "attempt", "atts": "attempt", "attempts": "attempt",
    "attempt": "attempt", "req": "request", "requests": "request",
    "sr": "successrate", "ratio": "rate", "rat": "rate",
    "avg": "average", "ave": "average", "mean": "average",
    "thrp": "throughput", "thr": "throughput", "tput": "throughput",
    "util": "utilization", "utilisation": "utilization", "usage": "utilization",
    "dl": "downlink", "ul": "uplink", "conn": "connection",
    "estab": "establishment", "est": "establishment",
    "rel": "release", "abnorm": "abnormal", "abnormal": "abnormal",
    "ho": "handover", "hho": "handover", "vol": "volume",
    "erab": "erab", "e": "e", "rab": "rab", "num": "number", "cnt": "count",
    "prb": "prb", "cce": "cce", "pct": "percent", "perc": "percent",
    "nbr": "neighbor", "neighbour": "neighbor", "tac": "tac",
}


def normalise_name(name: str) -> str:
    """Return a canonicalised, comparison-friendly token string."""
    s = str(name).strip().lower()
    s = _UNIT_RE.sub(" ", s)                 # drop "(%)", "[mbps]", "(kbit/s)"
    s = s.replace("%", " percent ")
    s = _NONWORD_RE.sub(" ", s)
    tokens = [t for t in s.split() if t]
    tokens = [_SYNONYMS.get(t, t) for t in tokens]
    # collapse duplicate adjacent tokens ("rate rate")
    out: list[str] = []
    for t in tokens:
        if not out or out[-1] != t:
            out.append(t)
    return " ".join(out)


def _token_set(name: str) -> set[str]:
    return set(normalise_name(name).split())


@dataclass
class MappingConfig:
    """Resolved mapping for one uploaded file."""
    resolved: dict[str, str] = field(default_factory=dict)      # source -> canon
    unmapped: list[str] = field(default_factory=list)
    ambiguous: dict[str, list[str]] = field(default_factory=dict)
    method: dict[str, str] = field(default_factory=dict)         # source -> how

    def rename_dict(self) -> dict[str, str]:
        return dict(self.resolved)

    def summary(self) -> str:
        return (f"{len(self.resolved)} mapped, {len(self.unmapped)} unmapped, "
                f"{len(self.ambiguous)} ambiguous")


def _load_alias_yaml(tech: str) -> dict[str, list[str]]:
    path = _CONFIG_DIR / f"huawei_kpi_mapping.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    common = data.get("common", {}) or {}
    tech_block = data.get(tech.lower(), {}) or {}
    merged: dict[str, list[str]] = {}
    for block in (common, tech_block):
        for canon, aliases in block.items():
            merged.setdefault(canon, [])
            merged[canon].extend(aliases or [])
    return merged


def build_mapping(
    source_columns: list[str],
    technology: str = "LTE",
    *,
    canonical_names: list[str] | None = None,
    extra_aliases: dict[str, list[str]] | None = None,
    fuzzy_floor: float = 0.72,
) -> MappingConfig:
    """Resolve every source column to a canonical name where possible."""
    from rfopt.ingest.schema import DIM_COLUMNS, numeric_kpi_names

    canon = list(canonical_names or (DIM_COLUMNS + numeric_kpi_names(technology)))
    alias_map = _load_alias_yaml(technology)
    if extra_aliases:
        for c, al in extra_aliases.items():
            alias_map.setdefault(c, []).extend(al)

    # normalised alias -> canonical
    norm_alias: dict[str, str] = {}
    for c in canon:
        norm_alias.setdefault(normalise_name(c), c)
    for c, aliases in alias_map.items():
        if c not in canon:
            continue
        for a in aliases:
            norm_alias.setdefault(normalise_name(a), c)

    cfg = MappingConfig()
    used_targets: set[str] = set()

    # pass 1 + 2: exact normalised match
    pending: list[str] = []
    for col in source_columns:
        n = normalise_name(col)
        if n in norm_alias:
            tgt = norm_alias[n]
            cfg.resolved[col] = tgt
            cfg.method[col] = "exact"
            used_targets.add(tgt)
        else:
            pending.append(col)

    # pass 3: fuzzy token match on whatever is still unmapped
    canon_tokens = {c: _token_set(c) for c in canon}
    canon_tokens.update({c: _token_set(c) for c in alias_map})  # alias keys too
    for col in pending:
        ctoks = _token_set(col)
        if not ctoks:
            cfg.unmapped.append(col)
            continue
        scored: list[tuple[float, str]] = []
        for c in canon:
            if c in used_targets:
                continue
            # best score over the canonical name and all its aliases
            candidates = [c, *alias_map.get(c, [])]
            best = 0.0
            for cand in candidates:
                toks = _token_set(cand)
                if not toks:
                    continue
                jac = len(ctoks & toks) / len(ctoks | toks)
                seq = SequenceMatcher(None, normalise_name(col),
                                      normalise_name(cand)).ratio()
                best = max(best, 0.6 * jac + 0.4 * seq)
            if best > 0:
                scored.append((best, c))
        scored.sort(reverse=True)
        if scored and scored[0][0] >= fuzzy_floor:
            top = scored[0]
            close = [c for s, c in scored[1:3] if top[0] - s < 0.06]
            if close:
                cfg.ambiguous[col] = [top[1], *close]
                cfg.unmapped.append(col)
            else:
                cfg.resolved[col] = top[1]
                cfg.method[col] = f"fuzzy:{top[0]:.2f}"
                used_targets.add(top[1])
        else:
            cfg.unmapped.append(col)

    return cfg
