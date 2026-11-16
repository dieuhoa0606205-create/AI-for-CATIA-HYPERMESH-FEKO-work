from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ID_RE = re.compile(r"^project_[0-9]{8}_[0-9]{6}_[0-9a-f]{6}$")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    workspace: Path
    installation_root: Path | None
    default_ncpu: int
    max_ncpu: int
    import_roots: tuple[Path, ...] = ()

    @classmethod
    def from_env(cls) -> "Settings":
        raw_home = os.environ.get("HYPERWORKS_HOME", "").strip()
        workspace = Path(
            os.environ.get(
                "HYPERWORKS_MCP_WORKSPACE", str(PACKAGE_ROOT / "workspace")
            )
        ).expanduser().resolve()
        raw_roots = os.environ.get("HYPERWORKS_MCP_IMPORT_ROOTS", "").strip()
        if raw_roots:
            import_roots = tuple(
                Path(part).expanduser().resolve()
                for part in raw_roots.split(os.pathsep)
                if part.strip()
            )
        else:
            import_roots = (workspace, workspace / "inbox")
        return cls(
            workspace=workspace,
            installation_root=(
                Path(raw_home).expanduser().resolve() if raw_home else None
            ),
            default_ncpu=_env_int("HYPERWORKS_DEFAULT_NCPU", 4),
            max_ncpu=_env_int("HYPERWORKS_MAX_NCPU", 32),
            import_roots=import_roots,
        )

    def ensure(self) -> None:
        for name in ("projects", "jobs", "inbox", "export"):
            (self.workspace / name).mkdir(parents=True, exist_ok=True)


def within(path: Path, root: Path) -> Path:
    resolved = path.expanduser().resolve()
    allowed = root.expanduser().resolve()
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise ValueError(f"Path is outside the allowed workspace: {resolved}") from exc
    return resolved


def safe_filename(name: str, suffix: str | None = None) -> str:
    candidate = Path(name).name
    if candidate != name or candidate in {"", ".", ".."}:
        raise ValueError("name must be a plain file name without directories")
    if any(char in candidate for char in '<>:"/\\|?*'):
        raise ValueError("name contains characters that are invalid on Windows")
    if suffix and Path(candidate).suffix.lower() != suffix.lower():
        candidate += suffix
    return candidate
