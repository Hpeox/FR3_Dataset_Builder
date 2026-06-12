"""Build warning and sidecar report helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BuildReport:
    demo_id: str
    manifest_path: Path
    output_path: Path
    report_path: Path
    warnings: list[dict[str, Any]] = field(default_factory=list)
    dropped_leading_rows: list[int] = field(default_factory=list)
    source_paths: dict[str, str] = field(default_factory=dict)
    total_aligned_rows: int = 0
    exported_rows: int = 0

    def warn(self, **record: Any) -> None:
        payload = {"level": "warning", "demo_id": self.demo_id, **record}
        self.warnings.append(payload)
        print(json.dumps(payload, ensure_ascii=True), flush=True)

    def payload(self) -> dict[str, Any]:
        return {
            "demo_id": self.demo_id,
            "manifest_path": self.manifest_path.as_posix(),
            "output_path": self.output_path.as_posix(),
            "total_aligned_rows": self.total_aligned_rows,
            "exported_rows": self.exported_rows,
            "dropped_leading_rows": self.dropped_leading_rows,
            "source_paths": self.source_paths,
            "warning_count": len(self.warnings),
            "warnings": self.warnings,
        }

    def write(self) -> None:
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(
            json.dumps(self.payload(), indent=2, ensure_ascii=True),
            encoding="utf-8",
        )

