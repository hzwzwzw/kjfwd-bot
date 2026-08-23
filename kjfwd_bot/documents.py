from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


@dataclass(frozen=True, slots=True)
class MarkdownDocument:
    path: str
    title: str
    size: int
    updated_at: str
    content: str | None = None

    def as_dict(self, *, include_content: bool = False) -> dict[str, Any]:
        value: dict[str, Any] = {
            "path": self.path,
            "title": self.title,
            "size": self.size,
            "updated_at": self.updated_at,
        }
        if include_content:
            value["content"] = self.content or ""
        return value


class MarkdownDocumentLibrary:
    """KJFWD-owned, local and read-only Markdown document tree."""

    def __init__(self, root: str | Path, *, max_document_bytes: int = 262_144):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_document_bytes = int(max_document_bytes)
        if self.max_document_bytes <= 0:
            raise ValueError("max_document_bytes must be positive")

    def list_documents(self) -> list[MarkdownDocument]:
        documents: list[MarkdownDocument] = []
        for path in sorted(self.root.rglob("*.md"), key=lambda item: item.as_posix()):
            if not path.is_file() or path.is_symlink() or self._has_hidden_part(path):
                continue
            try:
                documents.append(self._summary(path))
            except (OSError, UnicodeError, ValueError):
                continue
        return documents

    def tree(self) -> list[dict[str, Any]]:
        root: dict[str, Any] = {}
        for document in self.list_documents():
            cursor = root
            parts = PurePosixPath(document.path).parts
            for part in parts[:-1]:
                cursor = cursor.setdefault(part, {})
            cursor[parts[-1]] = document
        return self._tree_nodes(root)

    def read(self, document_path: str) -> MarkdownDocument:
        relative = self._validate_relative_path(document_path)
        candidate = self.root.joinpath(*relative.parts)
        self._reject_symlink_components(candidate)
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise KeyError(f"unknown document {relative.as_posix()}") from exc
        if self.root not in resolved.parents or not resolved.is_file():
            raise KeyError(f"unknown document {relative.as_posix()}")
        content = self._read_text(resolved)
        return self._summary(resolved, content=content)

    def _summary(self, path: Path, *, content: str | None = None) -> MarkdownDocument:
        size = path.stat().st_size
        if size > self.max_document_bytes:
            raise ValueError(f"document exceeds {self.max_document_bytes} bytes")
        if content is None:
            content = self._read_text(path)
        title = next(
            (
                line.strip()[2:].strip()[:200]
                for line in content.splitlines()
                if line.strip().startswith("# ") and line.strip()[2:].strip()
            ),
            path.stem[:200],
        )
        updated_at = datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc
        ).isoformat()
        return MarkdownDocument(
            path.relative_to(self.root).as_posix(), title, size, updated_at, content
        )

    def _read_text(self, path: Path) -> str:
        if path.stat().st_size > self.max_document_bytes:
            raise ValueError(f"document exceeds {self.max_document_bytes} bytes")
        return path.read_text(encoding="utf-8-sig")

    def _tree_nodes(self, value: dict[str, Any]) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        for name in sorted(value):
            child = value[name]
            if isinstance(child, MarkdownDocument):
                nodes.append({"type": "document", **child.as_dict()})
            else:
                nodes.append(
                    {
                        "type": "directory",
                        "name": name,
                        "children": self._tree_nodes(child),
                    }
                )
        return nodes

    @staticmethod
    def _validate_relative_path(value: str) -> PurePosixPath:
        clean = str(value or "").strip()
        if not clean or "\x00" in clean or "\\" in clean or len(clean) > 500:
            raise ValueError("invalid document path")
        path = PurePosixPath(clean)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("invalid document path")
        if path.suffix.lower() != ".md" or any(part.startswith(".") for part in path.parts):
            raise ValueError("document path must identify a visible .md file")
        return path

    def _reject_symlink_components(self, candidate: Path) -> None:
        current = self.root
        for part in candidate.relative_to(self.root).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("document path must not contain symbolic links")

    def _has_hidden_part(self, path: Path) -> bool:
        return any(part.startswith(".") for part in path.relative_to(self.root).parts)
