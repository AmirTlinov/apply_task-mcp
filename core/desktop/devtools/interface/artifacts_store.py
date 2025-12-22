"""Local artifact store for evidence_capture (black box evidence).

Stores redacted blobs under `<tasks_dir>/.artifacts/` and returns stable, content-addressed URIs.
This module is adapter-level I/O and must remain safe-by-default.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Tuple


ARTIFACTS_DIRNAME = ".artifacts"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def artifacts_dir(tasks_dir: Path) -> Path:
    return (Path(tasks_dir) / ARTIFACTS_DIRNAME).resolve()


def write_artifact(tasks_dir: Path, *, content: bytes, ext: str) -> Tuple[str, int, str]:
    """Write content-addressed artifact into tasks_dir/.artifacts.

    Returns: (uri, size_bytes, sha256_hex).
    """
    data = bytes(content or b"")
    digest = sha256_hex(data)

    extension = str(ext or "").strip().lower().lstrip(".")
    if not extension:
        extension = "bin"

    root = artifacts_dir(tasks_dir)
    root.mkdir(parents=True, exist_ok=True)

    filename = f"{digest}.{extension}"
    target = (root / filename).resolve()
    if root not in target.parents:
        raise ValueError("artifact path escape")

    if not target.exists():
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                delete=False,
                dir=str(root),
                prefix=f".{digest}.",
                suffix=".tmp",
            ) as tmp:
                tmp.write(data)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_path = Path(tmp.name).resolve()
            os.replace(str(tmp_path), str(target))
        finally:
            if tmp_path and tmp_path.exists() and tmp_path != target:
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    uri = f"{ARTIFACTS_DIRNAME}/{filename}"
    return uri, len(data), digest

