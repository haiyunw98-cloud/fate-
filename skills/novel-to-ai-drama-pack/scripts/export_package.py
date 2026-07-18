#!/usr/bin/env python3
"""Validate and atomically export an AI short-drama production package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Sequence

from build_media_jobs import build_media_jobs
from project_io import _fsync_directory, load_json
from validate_project import validate_project
from validate_references import validate_references
from xlsx_writer import write_xlsx


class ExportError(RuntimeError):
    """Raised when validation, staging, or publication cannot complete safely."""


_MARKER_NAME = ".novel-to-ai-drama-pack-export.json"
_MARKER = {
    "export_format": "novel-to-ai-drama-pack",
    "format_version": 1,
}
_CHECKSUM = re.compile(r"^[0-9a-f]{64}$")
_OUTPUT_NAMES = {
    "project_md": "project.md",
    "shots_xlsx": "shots.xlsx",
    "prompts": "video-prompts.txt",
    "media_manifest": "media-manifest.json",
    "validation_report": "validation-report.txt",
}
def _single_line(value: object) -> str:
    return str(value).replace("\r", "\\r").replace("\n", "\\n")


def _write_text(path: Path, text: str) -> None:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    with path.open("w", encoding="utf-8", newline="\n") as target:
        target.write(normalized)


def _write_json(path: Path, value: object) -> None:
    _write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _display(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _formal_contract_errors(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    settings = data.get("generation_settings")
    script_count = settings.get("script_episode_count") if isinstance(settings, dict) else None
    if (
        not isinstance(script_count, int)
        or isinstance(script_count, bool)
        or script_count != 3
    ):
        errors.append(
            "generation_settings.script_episode_count must be exactly 3 for formal export"
        )

    episodes = data.get("episodes")
    expected_ids = ["E001", "E002", "E003"]
    if not isinstance(episodes, list) or len(episodes) < 3:
        errors.append("first three episodes must be E001, E002, E003 in canonical order")
        return errors
    actual_ids = [
        episode.get("episode_id") if isinstance(episode, dict) else None
        for episode in episodes[:3]
    ]
    if actual_ids != expected_ids:
        errors.append("first three episodes must be E001, E002, E003 in canonical order")

    for index, episode in enumerate(episodes[:3]):
        episode_id = expected_ids[index]
        if not isinstance(episode, dict):
            errors.append(f"{episode_id} must be an object for formal export")
            continue
        script = episode.get("script")
        if not isinstance(script, str) or not script.strip():
            errors.append(f"{episode_id}.script must be nonempty for formal export")
        shots = episode.get("shots")
        if not isinstance(shots, list) or not shots:
            errors.append(f"{episode_id}.shots must be nonempty for formal export")

    first_episode = episodes[0]
    if not isinstance(first_episode, dict):
        return errors
    shots = first_episode.get("shots")
    if not isinstance(shots, list):
        return errors
    character_ids = {
        character.get("character_id")
        for character in data.get("characters", [])
        if isinstance(character, dict)
        and isinstance(character.get("character_id"), str)
    } if isinstance(data.get("characters"), list) else set()
    for shot_index, shot in enumerate(shots):
        fallback_id = f"E001_SH{shot_index + 1:03d}"
        shot_id = shot.get("shot_id", fallback_id) if isinstance(shot, dict) else fallback_id
        if not isinstance(shot, dict):
            continue
        if "dialogue_lines" not in shot:
            errors.append(
                f"{shot_id}.dialogue_lines must be explicitly present; [] means no dialogue"
            )
            continue
        lines = shot.get("dialogue_lines")
        if not isinstance(lines, list):
            errors.append(f"{shot_id}.dialogue_lines must be a list")
            continue
        for line_index, line in enumerate(lines):
            path = f"{shot_id}.dialogue_lines[{line_index}]"
            if not isinstance(line, dict):
                errors.append(f"{path} must be an object")
                continue
            speaker_id = line.get("speaker_id")
            text = line.get("text")
            if not isinstance(speaker_id, str) or not speaker_id.strip():
                errors.append(f"{path}.speaker_id must be a nonempty string")
            elif speaker_id not in character_ids:
                errors.append(f"{path}.speaker_id is unknown: {_single_line(speaker_id)}")
            if not isinstance(text, str) or not text.strip():
                errors.append(f"{path}.text must be a nonempty string")
    return errors


def _confined_media_parts(relative_path: object) -> tuple[str, ...] | None:
    if not isinstance(relative_path, str) or not relative_path.strip() or "\x00" in relative_path:
        return None
    posix = PurePosixPath(relative_path.replace("\\", "/"))
    windows = PureWindowsPath(relative_path)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or ".." in posix.parts
        or ".." in windows.parts
    ):
        return None
    parts = tuple(part for part in posix.parts if part not in {"", "."})
    return parts or None


def _hash_media_without_following_symlinks(
    project_root: Path,
    parts: tuple[str, ...],
) -> str:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(project_root, directory_flags)
    opened_directories: list[int] = [directory_fd]
    file_fd: int | None = None
    try:
        for component in parts[:-1]:
            directory_fd = os.open(
                component,
                directory_flags | no_follow,
                dir_fd=directory_fd,
            )
            opened_directories.append(directory_fd)
        file_fd = os.open(
            parts[-1],
            os.O_RDONLY | no_follow,
            dir_fd=directory_fd,
        )
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise OSError("media path is not a regular file")
        digest = hashlib.sha256()
        while chunk := os.read(file_fd, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(file_fd)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise OSError("media file changed while hashing")
        return digest.hexdigest()
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for opened_fd in reversed(opened_directories):
            os.close(opened_fd)


def _physical_media_errors(data: dict[str, Any], project_path: Path) -> list[str]:
    errors: list[str] = []
    try:
        project_root = project_path.expanduser().resolve(strict=True).parent
    except OSError as error:
        return [f"cannot resolve project root for media verification: {_single_line(error)}"]
    assets = data.get("assets")
    if not isinstance(assets, list):
        return ["assets must be a list for physical media verification"]

    for index, asset in enumerate(assets):
        if not isinstance(asset, dict) or asset.get("status") != "completed":
            continue
        label = str(asset.get("asset_id") or f"assets[{index}]")
        parts = _confined_media_parts(asset.get("relative_path"))
        if parts is None:
            errors.append(f"{label} media relative_path is not confined")
            continue
        media_path = project_root.joinpath(*parts)
        component = project_root
        component_error = False
        for part in parts[:-1]:
            component = component / part
            try:
                component_status = component.lstat()
            except FileNotFoundError:
                errors.append(f"{label} media file is missing: {media_path}")
                component_error = True
                break
            except OSError as error:
                errors.append(f"{label} media path cannot be inspected: {_single_line(error)}")
                component_error = True
                break
            if stat.S_ISLNK(component_status.st_mode):
                errors.append(f"{label} media path has a symbolic link component: {component}")
                component_error = True
                break
            if not stat.S_ISDIR(component_status.st_mode):
                errors.append(f"{label} media path component is not a directory: {component}")
                component_error = True
                break
        if component_error:
            continue
        try:
            media_status = media_path.lstat()
        except FileNotFoundError:
            errors.append(f"{label} media file is missing: {media_path}")
            continue
        except OSError as error:
            errors.append(f"{label} media file cannot be inspected: {_single_line(error)}")
            continue
        if stat.S_ISLNK(media_status.st_mode):
            errors.append(f"{label} media file is a symbolic link: {media_path}")
            continue
        if not stat.S_ISREG(media_status.st_mode):
            errors.append(f"{label} media path must be a regular file: {media_path}")
            continue
        try:
            resolved_media = media_path.resolve(strict=True)
            resolved_media.relative_to(project_root)
        except (OSError, ValueError):
            errors.append(f"{label} resolved media path escapes project root: {media_path}")
            continue
        expected_checksum = asset.get("checksum")
        if not isinstance(expected_checksum, str) or not _CHECKSUM.fullmatch(expected_checksum):
            errors.append(f"{label} checksum must be 64 lowercase hexadecimal characters")
            continue
        try:
            actual_checksum = _hash_media_without_following_symlinks(project_root, parts)
        except OSError as error:
            errors.append(f"{label} media file cannot be safely read: {_single_line(error)}")
            continue
        if actual_checksum != expected_checksum:
            errors.append(
                f"{label} checksum mismatch: expected {expected_checksum}, got {actual_checksum}"
            )
    return errors


def _dialogue_text(shot: dict[str, Any]) -> str:
    lines = shot.get("dialogue_lines")
    if not isinstance(lines, list):
        return ""
    return "\n".join(
        f"{line['speaker_id']}：{line['text']}"
        for line in lines
        if isinstance(line, dict)
        and isinstance(line.get("speaker_id"), str)
        and isinstance(line.get("text"), str)
    )


def _warnings(data: dict[str, Any]) -> list[str]:
    result: list[str] = []
    raw_warnings = data.get("warnings", [])
    if isinstance(raw_warnings, list):
        result.extend(_single_line(item) for item in raw_warnings if str(item).strip())
    elif raw_warnings is not None and str(raw_warnings).strip():
        result.append(_single_line(raw_warnings))

    for asset in data.get("assets", []):
        if not isinstance(asset, dict) or asset.get("status") in {"completed", "discarded"}:
            continue
        result.append(
            f"asset {asset.get('asset_id', '<unknown>')} V"
            f"{asset.get('version', '?')} is {asset.get('status', '<unknown>')}"
        )
    return result


def _registered_tokens(data: dict[str, Any]) -> list[str]:
    tokens = {
        asset["reference_token"]
        for asset in data.get("assets", [])
        if isinstance(asset, dict)
        and isinstance(asset.get("reference_token"), str)
        and asset["reference_token"]
    }
    return sorted(tokens, key=lambda token: (-len(token), token))


def _shot_tokens(shot: dict[str, Any], registered: Sequence[str]) -> list[str]:
    prompts = [
        shot.get("prompt_zh", ""),
        shot.get("prompt_en", ""),
    ]
    first_positions: dict[str, tuple[int, int]] = {}
    for prompt_index, prompt in enumerate(prompts):
        if not isinstance(prompt, str):
            continue
        for token in registered:
            position = prompt.find(token)
            if position >= 0:
                first_positions.setdefault(token, (prompt_index, position))
    return sorted(
        first_positions,
        key=lambda token: (*first_positions[token], token),
    )


def _camera_text(shot: dict[str, Any]) -> str:
    fields = (
        "shot_size",
        "camera",
        "camera_height",
        "camera_angle",
        "camera_movement",
        "lens",
        "composition",
    )
    values = [f"{field}: {_display(shot[field])}" for field in fields if shot.get(field) not in (None, "", [])]
    return " | ".join(values)


def _project_markdown(data: dict[str, Any], warnings: Sequence[str]) -> str:
    project = data["project"]
    analysis = data["analysis"]
    lines = [
        f"# {project['title']}",
        "",
        "## 项目状态",
        "",
        f"- 项目ID：{project['project_id']}",
        f"- 完成状态：{project['status']}",
        f"- 目标集数：{project['target_episode_count']}",
        "",
        "## 故事圣经",
        "",
        _display(analysis.get("world_bible")),
        "",
        "### 时间线",
        "",
        _display(analysis.get("timeline")) or "无",
        "",
        "### 故事结构",
        "",
        _display(analysis.get("story_arc")) or "无",
        "",
        "### 改编决策",
        "",
        _display(analysis.get("adaptation_decisions")) or "无",
        "",
        "## 资产表",
        "",
        "| 资产ID | 版本 | 类型 | 所属 | 状态 | 引用名 | 相对路径 |",
        "|---|---:|---|---|---|---|---|",
    ]
    for asset in sorted(
        data["assets"],
        key=lambda item: (str(item.get("asset_id", "")), int(item.get("version", 0))),
    ):
        values = [
            asset.get("asset_id", ""),
            asset.get("version", ""),
            asset.get("asset_type", ""),
            asset.get("owner_id", ""),
            asset.get("status", ""),
            asset.get("reference_token", ""),
            asset.get("relative_path", ""),
        ]
        escaped = [str(value).replace("|", "\\|").replace("\n", "<br>") for value in values]
        lines.append("| " + " | ".join(escaped) + " |")

    lines.extend(["", "## 角色声音档案", ""])
    for character in data["characters"]:
        profile = character.get("voice_profile", {})
        lines.extend(
            [
                f"### {character['character_id']} {character['name']}",
                "",
                f"- 声线：{_display(profile.get('voice'))}",
                f"- 语气：{_display(profile.get('tone'))}",
                f"- 语速：{_display(profile.get('pace'))}",
                f"- 样本文本：{_display(profile.get('sample_text'))}",
                "",
            ]
        )

    lines.extend(["## 前三集剧本", ""])
    for episode in data["episodes"][:3]:
        lines.extend(
            [
                f"### {episode['episode_id']} 《{episode['title']}》",
                "",
                _display(episode.get("script")),
                "",
            ]
        )
        for shot in episode.get("shots", []):
            if not isinstance(shot, dict):
                continue
            dialogue = _dialogue_text(shot)
            if dialogue:
                lines.extend(
                    [
                        f"- {shot.get('shot_id', '未知镜头')}对白：",
                        "",
                        dialogue,
                        "",
                    ]
                )

    lines.extend(["## 非阻断警告", ""])
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- 无")
    lines.append("")
    return "\n".join(lines)


def _shot_rows(data: dict[str, Any]) -> list[list[object]]:
    registered = _registered_tokens(data)
    rows: list[list[object]] = []
    for episode in data["episodes"][:3]:
        for shot in episode["shots"]:
            rows.append(
                [
                    episode["episode_id"],
                    shot["shot_id"],
                    ", ".join(shot.get("character_ids", [])),
                    shot.get("scene_id", ""),
                    _dialogue_text(shot),
                    _camera_text(shot),
                    shot.get("prompt_zh", ""),
                    shot.get("prompt_en", ""),
                    shot.get("negative_prompt", ""),
                    "\n".join(_shot_tokens(shot, registered)),
                ]
            )
    return rows


def _prompts_text(data: dict[str, Any]) -> str:
    registered = _registered_tokens(data)
    lines = [
        "# AI 视频提示词",
        "",
        "说明：本文件只导出视频提示词，不生成视频。",
        "",
    ]
    for episode in data["episodes"][:3]:
        lines.extend([f"## {episode['episode_id']} 《{episode['title']}》", ""])
        for shot in episode["shots"]:
            lines.extend(
                [
                    f"### {shot['shot_id']}",
                    f"中文：{shot.get('prompt_zh', '')}",
                    f"English: {shot.get('prompt_en', '')}",
                    f"对白：{_dialogue_text(shot) or '无'}",
                    f"负面：{shot.get('negative_prompt', '')}",
                    "精确引用：" + " ".join(_shot_tokens(shot, registered)),
                    "",
                ]
            )
    return "\n".join(lines)


def _manifest(data: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "asset_id",
        "version",
        "asset_type",
        "owner_type",
        "owner_id",
        "file_name",
        "relative_path",
        "checksum",
        "status",
        "reference_token",
        "parent_asset_ids",
    )
    assets = [
        {field: asset.get(field) for field in fields}
        for asset in sorted(
            data["assets"],
            key=lambda item: (str(item.get("asset_id", "")), int(item.get("version", 0))),
        )
    ]
    return {
        "project_id": data["project"]["project_id"],
        "assets": assets,
    }


def _validation_report(warnings: Sequence[str]) -> str:
    lines = [
        "Novel to AI Drama Pack validation report",
        "Errors: 0",
        f"Warnings: {len(warnings)}",
    ]
    lines.extend(f"WARNING: {warning}" for warning in warnings)
    lines.append("")
    return "\n".join(lines)


def _write_all(data: dict[str, Any], stage: Path) -> dict[str, Path]:
    warnings = _warnings(data)
    outputs = {key: stage / name for key, name in _OUTPUT_NAMES.items()}
    _write_text(outputs["project_md"], _project_markdown(data, warnings))
    write_xlsx(
        outputs["shots_xlsx"],
        (
            "剧集ID",
            "镜头ID",
            "角色ID",
            "场景ID",
            "对白",
            "摄影机",
            "中文视频提示词",
            "英文视频提示词",
            "负面提示词",
            "精确素材引用",
        ),
        _shot_rows(data),
        sheet_name="AI视频分镜",
        widths=(12, 18, 18, 14, 28, 28, 60, 60, 36, 60),
    )
    _write_text(outputs["prompts"], _prompts_text(data))
    _write_json(outputs["media_manifest"], _manifest(data))
    _write_text(outputs["validation_report"], _validation_report(warnings))
    _write_json(stage / _MARKER_NAME, _MARKER)
    return outputs


def _path_contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _prepare_paths(project_path: Path, output_dir: Path) -> tuple[Path, Path]:
    lexical_source = Path(os.path.abspath(str(project_path.expanduser())))
    lexical_requested = Path(os.path.abspath(str(output_dir.expanduser())))
    if _path_contains(lexical_requested, lexical_source):
        raise ExportError(
            "output destination contains the source project path and cannot be replaced: "
            f"{lexical_requested}"
        )
    source = project_path.expanduser().resolve(strict=True)
    requested = output_dir.expanduser()
    if not requested.is_absolute():
        requested = Path.cwd() / requested
    if requested.is_symlink():
        raise ExportError(f"output destination is a symbolic link: {requested}")
    parent = requested.parent
    if parent.is_symlink():
        raise ExportError(f"output parent is a symbolic link: {parent}")
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ExportError(f"cannot prepare output parent: {_single_line(error)}") from error
    parent = parent.resolve(strict=True)
    requested = parent / requested.name
    if _path_contains(requested, source):
        raise ExportError(
            f"output destination contains the source project and cannot be replaced: {requested}"
        )
    if requested.exists() and not requested.is_dir():
        try:
            if os.path.samefile(requested, source):
                raise ExportError("output destination aliases the source project")
        except OSError:
            pass
    return source, requested


def _is_managed_export(path: Path) -> bool:
    if not path.is_dir() or path.is_symlink():
        return False
    marker = path / _MARKER_NAME
    if not marker.is_file() or marker.is_symlink():
        return False
    try:
        return json.loads(marker.read_text(encoding="utf-8")) == _MARKER
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False


def _versioned_sibling(requested: Path) -> Path:
    for version in range(1, 10000):
        candidate = requested.with_name(f"{requested.name}-v{version:03d}")
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
    raise ExportError(f"cannot allocate versioned export sibling for {requested}")


def _safe_remove_created_directory(path: Path, parent: Path, prefix: str) -> None:
    if path.parent != parent or not path.name.startswith(prefix):
        raise ExportError(f"refusing unsafe cleanup outside export parent: {path}")
    if path.is_symlink():
        raise ExportError(f"refusing to clean symbolic-link workspace: {path}")
    if path.exists():
        shutil.rmtree(path)


def _publish_new(stage: Path, target: Path) -> Path:
    if target.exists() or target.is_symlink():
        target = _versioned_sibling(target)
    os.replace(stage, target)
    try:
        _fsync_directory(target.parent)
    except BaseException:
        _safe_remove_created_directory(target, target.parent, target.name)
        raise
    return target


def _publish_managed(stage: Path, destination: Path) -> Path:
    parent = destination.parent
    backup = parent / f".{destination.name}.backup-{uuid.uuid4().hex}"
    if backup.exists() or backup.is_symlink():
        raise ExportError(f"cannot allocate managed export backup: {backup}")
    os.replace(destination, backup)
    stage_published = False
    try:
        os.replace(stage, destination)
        stage_published = True
        _fsync_directory(parent)
    except BaseException:
        try:
            if stage_published:
                _safe_remove_created_directory(destination, parent, destination.name)
            os.replace(backup, destination)
            _fsync_directory(parent)
        except BaseException as restore_error:
            raise ExportError(
                "managed export publication failed and backup restoration also failed: "
                + _single_line(restore_error)
            )
        raise
    _safe_remove_created_directory(backup, parent, f".{destination.name}.backup-")
    _fsync_directory(parent)
    return destination


def _publish_atomically(stage: Path, requested: Path) -> Path:
    if requested.is_symlink():
        raise ExportError(f"output destination is a symbolic link: {requested}")
    if requested.exists():
        if _is_managed_export(requested):
            return _publish_managed(stage, requested)
        return _publish_new(stage, _versioned_sibling(requested))
    return _publish_new(stage, requested)


def export_package(project_path: Path, output_dir: Path) -> dict[str, Path]:
    """Validate, stage, and atomically publish the five formal export files."""
    try:
        data = load_json(Path(project_path))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ExportError(
            f"cannot load project {project_path}: {_single_line(error)}"
        ) from error

    validation_errors: list[str] = []
    try:
        validation_errors.extend(validate_project(data))
    except Exception as error:
        validation_errors.append(
            f"project validation failed internally: {_single_line(error)}"
        )
    try:
        validation_errors.extend(validate_references(data))
    except Exception as error:
        validation_errors.append(f"reference validation failed: {_single_line(error)}")
    project = data.get("project")
    if not isinstance(project, dict) or project.get("status") != "completed":
        validation_errors.append(
            "project.status must be completed for formal export"
        )
    validation_errors.extend(_formal_contract_errors(data))
    try:
        pending_jobs = build_media_jobs(data)
        if pending_jobs:
            validation_errors.append(
                "formal export has pending required media jobs: "
                + ", ".join(str(job.get("job_id", "<unknown>")) for job in pending_jobs)
            )
    except Exception as error:
        validation_errors.append(f"media readiness validation failed: {_single_line(error)}")
    validation_errors.extend(_physical_media_errors(data, Path(project_path)))
    if validation_errors:
        raise ExportError(
            "project validation failed:\n" + "\n".join(validation_errors)
        )

    try:
        _source, requested = _prepare_paths(Path(project_path), Path(output_dir))
    except (OSError, RuntimeError) as error:
        if isinstance(error, ExportError):
            raise
        raise ExportError(f"unsafe export path: {_single_line(error)}") from error

    stage_path: Path | None = None
    published: Path | None = None
    prefix = f".{requested.name}.staging-"
    try:
        stage_path = Path(tempfile.mkdtemp(prefix=prefix, dir=requested.parent))
        _write_all(data, stage_path)
        published = _publish_atomically(stage_path, requested)
        stage_path = None
    except ExportError:
        raise
    except Exception as error:
        raise ExportError(f"export failed: {_single_line(error)}") from error
    finally:
        if stage_path is not None and stage_path.exists():
            try:
                _safe_remove_created_directory(stage_path, requested.parent, prefix)
            except Exception as cleanup_error:
                if sys.exc_info()[0] is None:
                    raise ExportError(
                        f"export staging cleanup failed: {_single_line(cleanup_error)}"
                    ) from cleanup_error

    assert published is not None
    return {
        key: (published / name).resolve(strict=True)
        for key, name in _OUTPUT_NAMES.items()
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export a validated novel-to-AI-drama production package."
    )
    parser.add_argument("project", type=Path, metavar="PROJECT_JSON")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        outputs = export_package(args.project, args.output_dir)
    except ExportError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    for key in _OUTPUT_NAMES:
        print(outputs[key])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
