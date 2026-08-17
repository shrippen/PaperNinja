from __future__ import annotations

import json
import re
from pathlib import Path

_SPLIT = re.compile(r"[^\wÄÖÜäöüß]+", re.UNICODE)


def normalize_name(value: str) -> str:
    text = (value or "").strip().lower()
    text = _SPLIT.sub(" ", text)
    return " ".join(text.split())


class VendorAliasStore:
    """Clusters of equivalent vendor/correspondent names, learned from confirmed links."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clusters, self._backfilled = self._load()

    def _load(self) -> tuple[list[set[str]], bool]:
        if not self.path.exists():
            return [], False
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return [], False
        clusters = raw.get("clusters") if isinstance(raw, dict) else raw
        backfilled = bool(raw.get("backfilled")) if isinstance(raw, dict) else False
        out: list[set[str]] = []
        if isinstance(clusters, list):
            for item in clusters:
                if isinstance(item, list):
                    names = {normalize_name(str(n)) for n in item if str(n).strip()}
                    names.discard("")
                    if len(names) >= 2:
                        out.append(names)
        return out, backfilled

    def _save(self) -> None:
        payload = {
            "clusters": [sorted(c) for c in self._clusters],
            "backfilled": self._backfilled,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    @property
    def backfilled(self) -> bool:
        return self._backfilled

    def mark_backfilled(self) -> None:
        self._backfilled = True
        self._save()

    def equivalents(self, name: str) -> set[str]:
        key = normalize_name(name)
        if not key:
            return set()
        for cluster in self._clusters:
            if key in cluster:
                return set(cluster)
        return {key}

    def learn(self, left: str, right: str, *, persist: bool = True) -> bool:
        a, b = normalize_name(left), normalize_name(right)
        if not a or not b or a == b:
            return False
        found: list[int] = []
        for idx, cluster in enumerate(self._clusters):
            if a in cluster or b in cluster:
                found.append(idx)
        if not found:
            self._clusters.append({a, b})
        else:
            merged = {a, b}
            for idx in found:
                merged |= self._clusters[idx]
            for idx in reversed(found):
                del self._clusters[idx]
            self._clusters.append(merged)
        if persist:
            self._save()
        return True
