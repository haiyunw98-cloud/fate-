from __future__ import annotations

import errno
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


_UNSUPPORTED_DIRECTORY_SYNC_ERRNOS = {errno.EACCES, errno.EINVAL, errno.ENOTSUP, errno.EPERM}
if hasattr(errno, "EOPNOTSUPP"):
    _UNSUPPORTED_DIRECTORY_SYNC_ERRNOS.add(errno.EOPNOTSUPP)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return

    directory_fd: int | None = None
    try:
        directory_fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        os.fsync(directory_fd)
    except OSError as error:
        if error.errno not in _UNSUPPORTED_DIRECTORY_SYNC_ERRNOS:
            raise
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as target:
            json.dump(data, target, ensure_ascii=False, indent=2)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("project root must be an object")
    return data


def next_version(assets: list[dict[str, Any]], asset_id: str) -> int:
    return max(
        (asset["version"] for asset in assets if asset.get("asset_id") == asset_id),
        default=0,
    ) + 1
