from __future__ import annotations

import copy
import re
import uuid
from datetime import datetime, timezone
from typing import Any


class WorkflowGuardError(ValueError):
    """Raised when resume or versioned-redo state is unsafe."""


ALLOWED_TRANSITIONS = {
    "draft": {"confirmed", "discarded"},
    "confirmed": {"generating", "discarded"},
    "generating": {"completed", "redo"},
    "redo": {"confirmed", "discarded"},
    "completed": set(),
    "discarded": set(),
}

_JOB_STATUSES = {"pending", "redo", "generating", "completed", "discarded"}
_RESUMABLE_JOB_STATUSES = {"pending", "redo"}
_FILE_VERSION = re.compile(r"_V(\d{3})(?=\.[^./\\]+$)")
_TOKEN_VERSION = re.compile(r"_V(\d{3})$")
_ANY_VERSION = re.compile(r"_V\d{3}")
_GENERATED_OUTPUT_FIELDS = {
    "relative_path",
    "absolute_path",
    "file_path",
    "output_path",
    "output_url",
    "checksum",
    "completed_at",
    "generated_at",
    "output",
    "provider_output",
    "provider_response",
    "generation_output",
    "media_url",
    "size_bytes",
}


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def transition_asset(asset: dict[str, Any], target: str) -> dict[str, Any]:
    """Return a copied asset after a legal state transition."""
    if not isinstance(asset, dict):
        raise WorkflowGuardError("asset must be an object")
    current = asset.get("status")
    if not isinstance(current, str):
        raise WorkflowGuardError("asset.status must be a known status")
    if current not in ALLOWED_TRANSITIONS:
        raise WorkflowGuardError(f"unknown current asset status: {current}")
    if current == "completed":
        raise WorkflowGuardError(
            "completed assets are immutable; create a new version"
        )
    if current == "discarded":
        raise WorkflowGuardError("discarded assets are terminal")
    if not isinstance(target, str) or target not in ALLOWED_TRANSITIONS:
        raise WorkflowGuardError(f"unknown target asset status: {target}")
    if target not in ALLOWED_TRANSITIONS[current]:
        raise WorkflowGuardError(f"illegal asset transition: {current} -> {target}")
    updated = copy.deepcopy(asset)
    updated["status"] = target
    return updated


def resumable_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return copied pending/redo jobs in their original deterministic order."""
    if not isinstance(jobs, list):
        raise WorkflowGuardError("jobs must be a list")
    selected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, job in enumerate(jobs):
        path = f"jobs[{index}]"
        if not isinstance(job, dict):
            raise WorkflowGuardError(f"{path} must be an object")
        job_id = job.get("job_id")
        if not _nonempty(job_id):
            raise WorkflowGuardError(f"{path}.job_id must be a nonempty string")
        assert isinstance(job_id, str)
        if job_id in seen_ids:
            raise WorkflowGuardError(f"duplicate media job id: {job_id}")
        seen_ids.add(job_id)
        status = job.get("status")
        if not isinstance(status, str):
            raise WorkflowGuardError(f"{path}.status must be a known status")
        if status not in _JOB_STATUSES:
            raise WorkflowGuardError(f"{path} has unknown status: {status}")
        if status in _RESUMABLE_JOB_STATUSES:
            selected.append(copy.deepcopy(job))
    return selected


def _validated_history(
    assets: object, asset_id: str
) -> list[dict[str, Any]]:
    if not isinstance(assets, list):
        raise WorkflowGuardError("assets must be a list")
    matching: list[dict[str, Any]] = []
    for index, asset in enumerate(assets):
        path = f"assets[{index}]"
        if not isinstance(asset, dict):
            raise WorkflowGuardError(f"{path} must be an object")
        current_id = asset.get("asset_id")
        if not _nonempty(current_id):
            raise WorkflowGuardError(f"{path}.asset_id must be a nonempty string")
        if current_id == asset_id:
            matching.append(asset)
    if not matching:
        raise WorkflowGuardError(f"asset_id not found: {asset_id}")

    versions: set[int] = set()
    identity_fields = ("asset_type", "owner_type", "owner_id")
    expected_identity = {field: matching[0].get(field) for field in identity_fields}
    for asset in matching:
        version = asset.get("version")
        if not _positive_integer(version):
            raise WorkflowGuardError(
                f"asset version history for {asset_id} requires positive integers"
            )
        assert isinstance(version, int)
        if version in versions:
            raise WorkflowGuardError(
                f"asset version history for {asset_id} has duplicate version {version}"
            )
        versions.add(version)
        for field in identity_fields:
            value = asset.get(field)
            if not _nonempty(value):
                raise WorkflowGuardError(
                    f"asset version history for {asset_id} has malformed {field}"
                )
            if value != expected_identity[field]:
                raise WorkflowGuardError(
                    f"asset version history for {asset_id} has inconsistent {field}"
                )
        status = asset.get("status")
        if not isinstance(status, str) or status not in ALLOWED_TRANSITIONS:
            raise WorkflowGuardError(
                f"asset version history for {asset_id} has unknown status: {status}"
            )

    maximum = max(versions)
    if versions != set(range(1, maximum + 1)):
        raise WorkflowGuardError(
            f"asset version history for {asset_id} must start at 1 and be contiguous"
        )
    return sorted(matching, key=lambda item: int(item["version"]))


def _versioned_value(
    value: object,
    *,
    field: str,
    current_version: int,
    next_version: int,
) -> str:
    if not isinstance(value, str):
        raise WorkflowGuardError(f"cannot safely update {field}")
    pattern = _FILE_VERSION if field == "file_name" else _TOKEN_VERSION
    matches = list(pattern.finditer(value))
    if (
        len(matches) != 1
        or len(_ANY_VERSION.findall(value)) != 1
        or int(matches[0].group(1)) != current_version
    ):
        raise WorkflowGuardError(f"cannot safely update {field}")
    return (
        value[: matches[0].start(1)]
        + f"{next_version:03d}"
        + value[matches[0].end(1) :]
    )


def _new_run_id(existing_runs: list[dict[str, Any]]) -> str:
    known = {
        run.get("run_id")
        for run in existing_runs
        if isinstance(run, dict) and isinstance(run.get("run_id"), str)
    }
    while True:
        candidate = f"RUN_{uuid.uuid4().hex}"
        if candidate not in known:
            return candidate


def create_redo_asset(
    data: dict[str, Any], asset_id: str, reason: str
) -> dict[str, Any]:
    """Append a versioned redo asset and audit run without mutating project data."""
    if not isinstance(data, dict):
        raise WorkflowGuardError("project root must be an object")
    if not _nonempty(asset_id):
        raise WorkflowGuardError("asset_id must be a nonempty string")
    if not _nonempty(reason):
        raise WorkflowGuardError("reason must be a nonempty string")
    runs = data.get("generation_runs")
    if not isinstance(runs, list):
        raise WorkflowGuardError("generation_runs must be a list")
    for index, run in enumerate(runs):
        if not isinstance(run, dict):
            raise WorkflowGuardError(f"generation_runs[{index}] must be an object")

    history = _validated_history(data.get("assets"), asset_id)
    latest = history[-1]
    if latest.get("status") == "discarded":
        raise WorkflowGuardError("latest asset version is discarded")
    if not _nonempty(latest.get("prompt")):
        raise WorkflowGuardError("latest asset prompt must be a nonempty string")
    current_version = int(latest["version"])
    next_version = current_version + 1
    if next_version > 999:
        raise WorkflowGuardError("asset version exceeds V999 naming contract")

    new_asset = copy.deepcopy(latest)
    new_asset["version"] = next_version
    new_asset["file_name"] = _versioned_value(
        latest.get("file_name"),
        field="file_name",
        current_version=current_version,
        next_version=next_version,
    )
    new_asset["reference_token"] = _versioned_value(
        latest.get("reference_token"),
        field="reference_token",
        current_version=current_version,
        next_version=next_version,
    )
    for field in _GENERATED_OUTPUT_FIELDS:
        new_asset.pop(field, None)
    new_asset["parent_asset_ids"] = [asset_id]
    new_asset["status"] = "redo"

    updated = copy.deepcopy(data)
    updated["assets"].append(new_asset)
    old_prompt = copy.deepcopy(latest.get("prompt"))
    updated["generation_runs"].append(
        {
            "run_id": _new_run_id(runs),
            "operation": "redo_asset",
            "asset_id": asset_id,
            "asset_type": latest["asset_type"],
            "owner_type": latest["owner_type"],
            "owner_id": latest["owner_id"],
            "from_version": current_version,
            "to_version": next_version,
            "reason": reason,
            "old_prompt": old_prompt,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ),
        }
    )
    return updated
