from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from app.schemas.models import RepoContext, RepoEvidence

IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".next",
    ".turbo",
    "target",
}

# Dependency manifests intentionally remain explicit to satisfy stack detection.
KNOWN_MANIFESTS = [
    "package.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "requirements.txt",
    "pyproject.toml",
    "Pipfile",
    "go.mod",
    "Cargo.toml",
    "Gemfile",
    "mix.exs",
    "pom.xml",
    "build.gradle",
]

CODE_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rb",
    ".java",
    ".cs",
    ".rs",
    ".php",
    ".ex",
    ".exs",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".swift",
    ".kt",
    ".scala",
}

DOC_EXTENSIONS = {".md", ".mdx", ".rst", ".txt", ".adoc"}
CONFIG_EXTENSIONS = {
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".ini",
    ".cfg",
    ".conf",
    ".xml",
}
KNOWN_TEXT_EXTENSIONS = CODE_EXTENSIONS | DOC_EXTENSIONS | CONFIG_EXTENSIONS | {".env"}


@dataclass
class _FileEntry:
    path: Path
    rel_path: str
    ext: str
    size: int
    depth: int
    kind: str


class RepositoryAnalyzer:
    def __init__(
        self,
        max_manifest_chars: int = 4000,
        max_evidence_files: int = 60,
        max_excerpt_chars: int = 1200,
        max_index_file_bytes: int = 300_000,
    ) -> None:
        self.max_manifest_chars = max_manifest_chars
        self.max_evidence_files = max_evidence_files
        self.max_excerpt_chars = max_excerpt_chars
        self.max_index_file_bytes = max_index_file_bytes

    def analyze(self, repo_path: str) -> RepoContext:
        root = Path(repo_path).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"Repository path not found or not a directory: {root}")

        manifest_data: dict[str, str] = {}
        entries: list[_FileEntry] = []
        code_files: list[Path] = []

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
            current_dir = Path(dirpath)

            for filename in filenames:
                file_path = current_dir / filename
                if not file_path.is_file():
                    continue

                if filename in KNOWN_MANIFESTS and filename not in manifest_data:
                    manifest_data[filename] = self._safe_read(file_path, self.max_manifest_chars)

                entry = self._build_entry(root, file_path)
                if not entry:
                    continue

                entries.append(entry)
                if entry.kind == "code":
                    code_files.append(file_path)

        ranked_entries = self._rank_entries(entries)
        selected_entries = ranked_entries[: self.max_evidence_files]
        evidence = [self._entry_to_evidence(entry) for entry in selected_entries]

        stack = self._derive_stack(manifest_data, code_files)
        architecture_notes = self._build_architecture_notes(
            root=root,
            manifests=manifest_data,
            code_files=code_files,
            all_entries=entries,
            evidence=evidence,
        )
        repo_tree = self._build_repo_tree(root)

        return RepoContext(
            root_path=str(root),
            stack=stack,
            manifests=manifest_data,
            architecture_notes=architecture_notes,
            repo_tree=repo_tree,
            evidence=evidence,
        )

    def select_relevant_evidence(self, context: RepoContext, query: str, limit: int = 10) -> list[RepoEvidence]:
        if limit <= 0:
            return []
        if not context.evidence:
            return []

        terms = self._tokenize(query)
        scored: list[tuple[int, int, RepoEvidence]] = []
        for idx, item in enumerate(context.evidence):
            score = self._score_evidence(item, terms)
            # Keep earlier baseline order stable as tie-breaker.
            scored.append((score, -idx, item))

        scored.sort(reverse=True, key=lambda x: (x[0], x[1]))
        selected = [item for score, _, item in scored if score > 0][:limit]
        selected_paths = {item.path for item in selected}

        # Always provide baseline breadth so broad questions still get usable context.
        baseline_target = min(limit, len(context.evidence), 6)
        if len(selected) < baseline_target:
            for item in context.evidence:
                if item.path in selected_paths:
                    continue
                selected.append(item)
                selected_paths.add(item.path)
                if len(selected) >= baseline_target:
                    break

        if not selected:
            return context.evidence[: min(limit, len(context.evidence))]
        return selected[:limit]

    def get_path_context(self, context: RepoContext, requested_path: str) -> str:
        root = Path(context.root_path).resolve()
        candidate = self._resolve_requested_path(root, requested_path)
        if candidate is None:
            return f"Requested path not found or not allowed: {requested_path}"

        if candidate.is_dir():
            lines = [f"Directory context for `{candidate.relative_to(root)}/`:"]
            try:
                children = sorted(candidate.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except OSError:
                return f"Could not read directory: {requested_path}"

            for child in children[:80]:
                suffix = "/" if child.is_dir() else ""
                lines.append(f"- {child.name}{suffix}")
            if len(children) > 80:
                lines.append(f"- ... ({len(children) - 80} more entries)")
            return "\n".join(lines)

        rel = str(candidate.relative_to(root))
        ext = candidate.suffix.lower()
        content = self._safe_read(candidate, self.max_excerpt_chars * 4)
        symbols = self._extract_symbols(ext, content) if ext in CODE_EXTENSIONS else []
        excerpt = self._condense_excerpt(content, max_lines=28)
        symbols_text = ", ".join(symbols[:12]) if symbols else "(none)"
        return (
            f"File context for `{rel}`\n"
            f"Symbols: {symbols_text}\n"
            f"Excerpt:\n{excerpt}"
        )

    @staticmethod
    def _resolve_requested_path(root: Path, requested_path: str) -> Path | None:
        raw = requested_path.strip().strip("`").strip()
        if not raw:
            return None
        candidate = (root / raw).resolve()
        if not str(candidate).startswith(str(root)):
            return None
        if not candidate.exists():
            return None
        return candidate

    def _build_entry(self, root: Path, file_path: Path) -> _FileEntry | None:
        try:
            size = file_path.stat().st_size
        except OSError:
            return None

        if size <= 0 or size > self.max_index_file_bytes:
            return None

        ext = file_path.suffix.lower()
        if not self._is_text_file(file_path, ext):
            return None

        rel = str(file_path.relative_to(root))
        depth = len(file_path.relative_to(root).parts)
        kind = self._classify_kind(file_path.name.lower(), ext)
        return _FileEntry(path=file_path, rel_path=rel, ext=ext, size=size, depth=depth, kind=kind)

    def _entry_to_evidence(self, entry: _FileEntry) -> RepoEvidence:
        # Read a larger window, then compress structurally into concise excerpts.
        content = self._safe_read(entry.path, self.max_excerpt_chars * 3)
        max_lines = 20 if entry.kind in {"doc", "config"} else 16
        excerpt = self._condense_excerpt(content, max_lines=max_lines)
        symbols = self._extract_symbols(entry.ext, content) if entry.kind == "code" else []
        return RepoEvidence(path=entry.rel_path, symbols=symbols[:8], excerpt=excerpt)

    def _rank_entries(self, entries: list[_FileEntry]) -> list[_FileEntry]:
        if not entries:
            return []

        by_kind: dict[str, list[_FileEntry]] = {"code": [], "doc": [], "config": [], "other": []}
        for entry in entries:
            by_kind.setdefault(entry.kind, []).append(entry)

        for kind in by_kind:
            by_kind[kind].sort(
                key=lambda e: (
                    -self._entry_score(e),
                    e.depth,
                    abs(e.size - 12_000),
                    e.rel_path,
                )
            )

        ranked: list[_FileEntry] = []
        order = ("code", "doc", "config", "other")
        while len(ranked) < len(entries):
            progressed = False
            for kind in order:
                bucket = by_kind.get(kind, [])
                if not bucket:
                    continue
                ranked.append(bucket.pop(0))
                progressed = True
            if not progressed:
                break
        return ranked

    def _entry_score(self, entry: _FileEntry) -> int:
        score = 0
        kind_weights = {"code": 60, "doc": 50, "config": 40, "other": 20}
        score += kind_weights.get(entry.kind, 20)

        if entry.depth == 1:
            score += 12
        elif entry.depth == 2:
            score += 8
        elif entry.depth == 3:
            score += 4

        if 200 <= entry.size <= 40_000:
            score += 8
        elif entry.size < 200:
            score -= 6
        elif entry.size > 120_000:
            score -= 8

        return score

    @staticmethod
    def _classify_kind(name_lower: str, ext: str) -> str:
        if ext in CODE_EXTENSIONS:
            return "code"
        if ext in DOC_EXTENSIONS:
            return "doc"
        if ext in CONFIG_EXTENSIONS or name_lower.endswith(".lock") or name_lower.startswith(".env"):
            return "config"
        return "other"

    @staticmethod
    def _is_text_file(path: Path, ext: str) -> bool:
        if ext in KNOWN_TEXT_EXTENSIONS or path.name.lower().startswith(".env"):
            return True
        try:
            raw = path.read_bytes()[:4096]
        except OSError:
            return False
        if not raw:
            return False
        if b"\x00" in raw:
            return False
        sample = raw.decode("utf-8", errors="ignore")
        if not sample.strip():
            return False
        printable = sum(1 for ch in sample if ch.isprintable() or ch in "\n\r\t")
        return printable / max(len(sample), 1) >= 0.85

    @staticmethod
    def _score_evidence(item: RepoEvidence, terms: list[str]) -> int:
        if not terms:
            return 0
        path_text = item.path.lower()
        symbol_text = " ".join(item.symbols).lower()
        excerpt_text = item.excerpt.lower()
        score = 0
        for term in terms:
            score += 6 * path_text.count(term)
            score += 4 * symbol_text.count(term)
            score += 1 * excerpt_text.count(term)
        return score

    @staticmethod
    def _safe_read(path: Path, max_chars: int) -> str:
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
            return raw[:max_chars]
        except OSError:
            return ""

    @staticmethod
    def _condense_excerpt(content: str, max_lines: int = 16) -> str:
        lines = [line.rstrip() for line in content.splitlines() if line.strip()]
        if len(lines) <= max_lines:
            return "\n".join(lines)

        head = max(4, max_lines // 2)
        tail = max(4, max_lines // 3)
        middle = max(2, max_lines - head - tail)
        middle_start = max(head, (len(lines) // 2) - (middle // 2))

        chunk = (
            lines[:head]
            + ["..."]
            + lines[middle_start : middle_start + middle]
            + ["..."]
            + lines[-tail:]
        )
        return "\n".join(chunk[: max_lines + 2])

    @staticmethod
    def _extract_symbols(extension: str, content: str) -> list[str]:
        patterns = {
            ".py": [r"^def\s+([a-zA-Z_][a-zA-Z0-9_]*)", r"^class\s+([a-zA-Z_][a-zA-Z0-9_]*)"],
            ".ts": [r"function\s+([a-zA-Z_][a-zA-Z0-9_]*)", r"class\s+([a-zA-Z_][a-zA-Z0-9_]*)"],
            ".tsx": [r"function\s+([a-zA-Z_][a-zA-Z0-9_]*)", r"const\s+([A-Z][a-zA-Z0-9_]*)\s*=\s*\("],
            ".js": [r"function\s+([a-zA-Z_][a-zA-Z0-9_]*)", r"class\s+([a-zA-Z_][a-zA-Z0-9_]*)"],
            ".jsx": [r"function\s+([a-zA-Z_][a-zA-Z0-9_]*)", r"const\s+([A-Z][a-zA-Z0-9_]*)\s*=\s*\("],
            ".go": [r"func\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("],
            ".rb": [r"^def\s+([a-zA-Z_][a-zA-Z0-9_!?=]*)", r"^class\s+([A-Z][a-zA-Z0-9_:]*)"],
            ".java": [r"class\s+([A-Z][a-zA-Z0-9_]*)", r"(?:public|private|protected)\s+[\w<>\[\]]+\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\("],
            ".cs": [r"class\s+([A-Z][a-zA-Z0-9_]*)", r"(?:public|private|protected)\s+[\w<>\[\]]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("],
            ".rs": [r"fn\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\("],
            ".php": [r"function\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\("],
            ".ex": [r"def\s+([a-zA-Z_][a-zA-Z0-9_!?]*)\s*\("],
            ".exs": [r"def\s+([a-zA-Z_][a-zA-Z0-9_!?]*)\s*\("],
            ".kt": [r"fun\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", r"class\s+([A-Z][a-zA-Z0-9_]*)"],
            ".swift": [r"func\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", r"class\s+([A-Z][a-zA-Z0-9_]*)"],
            ".cpp": [r"(?:void|int|bool|auto|float|double|char|std::\w+)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\("],
            ".c": [r"(?:void|int|bool|float|double|char)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\("],
        }

        symbols: list[str] = []
        for pattern in patterns.get(extension, []):
            symbols.extend(re.findall(pattern, content, flags=re.MULTILINE))
        deduped: list[str] = []
        for symbol in symbols:
            if symbol not in deduped:
                deduped.append(symbol)
        return deduped

    @staticmethod
    def _tokenize(query: str) -> list[str]:
        tokens = re.findall(r"[a-zA-Z0-9_./-]{3,}", query.lower())
        blacklist = {
            "what",
            "when",
            "where",
            "which",
            "should",
            "would",
            "about",
            "this",
            "that",
            "with",
            "have",
            "does",
            "could",
            "please",
            "your",
            "you",
            "can",
            "see",
            "the",
            "and",
        }
        normalized: list[str] = []
        for token in tokens:
            cleaned = token.strip("./-")
            if not cleaned or cleaned in blacklist:
                continue
            normalized.append(cleaned)
        return normalized

    def _derive_stack(self, manifests: dict[str, str], code_files: list[Path]) -> list[str]:
        stack: list[str] = []
        by_extension: dict[str, int] = {}
        for path in code_files:
            ext = path.suffix.lower()
            by_extension[ext] = by_extension.get(ext, 0) + 1

        ext_to_stack = {
            ".py": "Python",
            ".ts": "TypeScript",
            ".tsx": "TypeScript/React",
            ".js": "JavaScript",
            ".jsx": "JavaScript/React",
            ".go": "Go",
            ".rb": "Ruby",
            ".java": "Java",
            ".cs": "C#",
            ".rs": "Rust",
            ".php": "PHP",
            ".ex": "Elixir",
            ".exs": "Elixir",
            ".c": "C",
            ".cc": "C++",
            ".cpp": "C++",
            ".swift": "Swift",
            ".kt": "Kotlin",
            ".scala": "Scala",
        }
        for ext, _count in sorted(by_extension.items(), key=lambda x: x[1], reverse=True):
            if ext in ext_to_stack:
                label = ext_to_stack[ext]
                if label not in stack:
                    stack.append(label)

        package_json = manifests.get("package.json")
        if package_json:
            try:
                parsed = json.loads(package_json)
                deps = {**parsed.get("dependencies", {}), **parsed.get("devDependencies", {})}
                if "react" in deps and "React" not in stack:
                    stack.append("React")
                if "next" in deps and "Next.js" not in stack:
                    stack.append("Next.js")
                if "fastify" in deps and "Fastify" not in stack:
                    stack.append("Fastify")
                if "express" in deps and "Express" not in stack:
                    stack.append("Express")
            except json.JSONDecodeError:
                pass

        requirements_txt = manifests.get("requirements.txt", "")
        if "fastapi" in requirements_txt.lower() and "FastAPI" not in stack:
            stack.append("FastAPI")
        if "django" in requirements_txt.lower() and "Django" not in stack:
            stack.append("Django")
        if "flask" in requirements_txt.lower() and "Flask" not in stack:
            stack.append("Flask")

        if "go.mod" in manifests and "Go Modules" not in stack:
            stack.append("Go Modules")
        if "Cargo.toml" in manifests and "Cargo" not in stack:
            stack.append("Cargo")

        return stack[:14]

    @staticmethod
    def _build_architecture_notes(
        root: Path,
        manifests: dict[str, str],
        code_files: list[Path],
        all_entries: list[_FileEntry],
        evidence: list[RepoEvidence],
    ) -> list[str]:
        notes: list[str] = []
        notes.append(f"Repository root: {root}")
        notes.append(f"Detected manifests: {', '.join(sorted(manifests.keys())) if manifests else 'none'}")
        notes.append(f"Indexed text files: {len(all_entries)} (showing evidence for {len(evidence)})")
        notes.append(f"Indexed code files: {len(code_files)}")

        kind_counts = {"code": 0, "doc": 0, "config": 0, "other": 0}
        for item in all_entries:
            kind_counts[item.kind] = kind_counts.get(item.kind, 0) + 1
        notes.append(
            "Indexed mix: "
            + ", ".join(f"{kind}={count}" for kind, count in kind_counts.items())
        )

        top_dirs: dict[str, int] = {}
        for path in code_files:
            rel_parts = path.relative_to(root).parts
            top_dir = rel_parts[0] if rel_parts else "."
            top_dirs[top_dir] = top_dirs.get(top_dir, 0) + 1
        if top_dirs:
            major = sorted(top_dirs.items(), key=lambda x: x[1], reverse=True)[:5]
            notes.append("Largest code areas: " + ", ".join(f"{name} ({count})" for name, count in major))

        return notes

    @staticmethod
    def _build_repo_tree(root: Path, max_depth: int = 5, max_lines: int = 320) -> str:
        lines: list[str] = ["."]
        line_count = 1

        def walk(path: Path, prefix: str, depth: int) -> None:
            nonlocal line_count
            if line_count >= max_lines or depth > max_depth:
                return
            try:
                entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except OSError:
                return

            visible: list[Path] = []
            for entry in entries:
                if entry.is_dir() and entry.name in IGNORE_DIRS:
                    continue
                visible.append(entry)

            for idx, entry in enumerate(visible):
                if line_count >= max_lines:
                    return
                is_last = idx == len(visible) - 1
                connector = "`-- " if is_last else "|-- "
                suffix = "/" if entry.is_dir() else ""
                lines.append(f"{prefix}{connector}{entry.name}{suffix}")
                line_count += 1
                if entry.is_dir():
                    child_prefix = prefix + ("    " if is_last else "|   ")
                    walk(entry, child_prefix, depth + 1)

        walk(root, "", 1)
        if line_count >= max_lines:
            lines.append("... (tree truncated)")
        return "\n".join(lines)
