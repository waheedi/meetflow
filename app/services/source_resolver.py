from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app.core.config import Settings

ARCHIVE_SUFFIXES = (
    ".zip",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz2",
    ".tar.xz",
    ".txz",
    ".gz",
)


class SourceResolverError(Exception):
    pass


@dataclass
class ResolvedSource:
    local_path: str
    source_input: str
    source_kind: str
    cache_hit: bool


class SourceResolver:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cache_dir = Path(settings.resolver_cache_dir).expanduser().resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def resolve(self, source_input: str, source_type: Optional[str] = None, ref: Optional[str] = None) -> ResolvedSource:
        src = (source_input or "").strip()
        if not src:
            raise SourceResolverError("Source input is empty.")

        normalized_type = (source_type or "auto").strip().lower()
        if normalized_type not in {"auto", "local", "git", "archive"}:
            raise SourceResolverError("source_type must be one of: auto, local, git, archive")

        local_candidate = Path(src).expanduser()
        if local_candidate.exists() and local_candidate.is_dir() and normalized_type in {"auto", "local"}:
            return ResolvedSource(
                local_path=str(local_candidate.resolve()),
                source_input=src,
                source_kind="local",
                cache_hit=False,
            )

        if local_candidate.exists() and local_candidate.is_file() and self._is_archive_name(local_candidate.name):
            if normalized_type not in {"auto", "archive", "local"}:
                raise SourceResolverError("Provided source points to a local archive, but source_type is not archive/auto.")
            return self._resolve_archive_file(local_candidate.resolve(), source_input=src)

        if self._is_http_url(src):
            if normalized_type == "archive" or (normalized_type == "auto" and self._is_archive_name(urlparse(src).path)):
                return self._resolve_archive_url(src)
            return self._resolve_git_url(src, ref=ref)

        if self._looks_like_git_source(src) and normalized_type in {"auto", "git"}:
            return self._resolve_git_url(src, ref=ref)

        if normalized_type == "local":
            raise SourceResolverError(f"Local path does not exist or is not a directory: {local_candidate}")

        raise SourceResolverError(
            "Could not resolve source. Provide a local directory, a git URL, or an archive URL/path."
        )

    def _resolve_git_url(self, source_url: str, ref: Optional[str]) -> ResolvedSource:
        cache_key = self._cache_key(source_url=source_url, source_kind="git", ref=ref)
        entry_dir = self.cache_dir / cache_key
        work_dir = entry_dir / "work"
        meta_file = entry_dir / "meta.json"

        if work_dir.exists() and meta_file.exists():
            return ResolvedSource(
                local_path=str(work_dir),
                source_input=source_url,
                source_kind="git",
                cache_hit=True,
            )

        if shutil.which("git") is None:
            raise SourceResolverError("`git` is not available on PATH.")

        self._clean_path(entry_dir)
        entry_dir.mkdir(parents=True, exist_ok=True)
        staging_dir = entry_dir / "work.staging"
        self._clean_path(staging_dir)

        clone_cmd = ["git", "clone", "--depth", "1", "--recurse-submodules=no"]
        if ref:
            clone_cmd.extend(["--branch", ref])
        clone_cmd.extend([source_url, str(staging_dir)])

        result = subprocess.run(clone_cmd, capture_output=True, text=True)
        if result.returncode != 0 and ref:
            fallback_clone = subprocess.run(
                ["git", "clone", "--depth", "1", "--recurse-submodules=no", source_url, str(staging_dir)],
                capture_output=True,
                text=True,
            )
            if fallback_clone.returncode == 0:
                fetch_result = subprocess.run(
                    ["git", "-C", str(staging_dir), "fetch", "--depth", "1", "origin", ref],
                    capture_output=True,
                    text=True,
                )
                if fetch_result.returncode == 0:
                    checkout_result = subprocess.run(
                        ["git", "-C", str(staging_dir), "checkout", "FETCH_HEAD"],
                        capture_output=True,
                        text=True,
                    )
                    if checkout_result.returncode != 0:
                        err = checkout_result.stderr.strip() or checkout_result.stdout.strip()
                        raise SourceResolverError(f"Git checkout failed for ref `{ref}`: {err}")
                else:
                    err = fetch_result.stderr.strip() or fetch_result.stdout.strip()
                    raise SourceResolverError(f"Git fetch failed for ref `{ref}`: {err}")
            else:
                err = fallback_clone.stderr.strip() or fallback_clone.stdout.strip()
                raise SourceResolverError(f"Git clone failed: {err}")
        elif result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            raise SourceResolverError(f"Git clone failed: {err}")

        staging_dir.rename(work_dir)
        self._write_meta(
            meta_file,
            {
                "source_input": source_url,
                "source_kind": "git",
                "ref": ref,
                "local_path": str(work_dir),
            },
        )

        return ResolvedSource(local_path=str(work_dir), source_input=source_url, source_kind="git", cache_hit=False)

    def _resolve_archive_url(self, archive_url: str) -> ResolvedSource:
        cache_key = self._cache_key(source_url=archive_url, source_kind="archive-url", ref=None)
        entry_dir = self.cache_dir / cache_key
        work_dir = entry_dir / "work"
        meta_file = entry_dir / "meta.json"

        if work_dir.exists() and meta_file.exists():
            return ResolvedSource(
                local_path=self._pick_root_dir(work_dir),
                source_input=archive_url,
                source_kind="archive-url",
                cache_hit=True,
            )

        self._clean_path(entry_dir)
        entry_dir.mkdir(parents=True, exist_ok=True)

        parsed = urlparse(archive_url)
        suffix = self._archive_suffix(parsed.path)
        download_path = entry_dir / f"archive{suffix}"
        extract_staging = entry_dir / "work.staging"

        self._download_file(archive_url, download_path)
        self._extract_archive(download_path, extract_staging)
        extract_staging.rename(work_dir)

        root_dir = self._pick_root_dir(work_dir)
        self._write_meta(
            meta_file,
            {
                "source_input": archive_url,
                "source_kind": "archive-url",
                "local_path": root_dir,
            },
        )
        return ResolvedSource(local_path=root_dir, source_input=archive_url, source_kind="archive-url", cache_hit=False)

    def _resolve_archive_file(self, archive_path: Path, source_input: str) -> ResolvedSource:
        cache_key = self._cache_key(
            source_url=f"{archive_path}:{int(archive_path.stat().st_mtime)}:{archive_path.stat().st_size}",
            source_kind="archive-file",
            ref=None,
        )
        entry_dir = self.cache_dir / cache_key
        work_dir = entry_dir / "work"
        meta_file = entry_dir / "meta.json"

        if work_dir.exists() and meta_file.exists():
            return ResolvedSource(
                local_path=self._pick_root_dir(work_dir),
                source_input=source_input,
                source_kind="archive-file",
                cache_hit=True,
            )

        self._clean_path(entry_dir)
        entry_dir.mkdir(parents=True, exist_ok=True)
        extract_staging = entry_dir / "work.staging"
        self._extract_archive(archive_path, extract_staging)
        extract_staging.rename(work_dir)

        root_dir = self._pick_root_dir(work_dir)
        self._write_meta(
            meta_file,
            {
                "source_input": source_input,
                "source_kind": "archive-file",
                "local_path": root_dir,
            },
        )
        return ResolvedSource(local_path=root_dir, source_input=source_input, source_kind="archive-file", cache_hit=False)

    def _extract_archive(self, archive_path: Path, output_dir: Path) -> None:
        self._clean_path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        name = archive_path.name.lower()
        if name.endswith(".zip"):
            self._extract_zip(archive_path, output_dir)
            return

        try:
            self._extract_tar_like(archive_path, output_dir)
            return
        except tarfile.ReadError as exc:
            raise SourceResolverError(f"Unsupported or invalid archive format: {archive_path.name}") from exc

    def _extract_zip(self, archive_path: Path, output_dir: Path) -> None:
        max_files = self.settings.resolver_max_extract_files
        max_bytes = self.settings.resolver_max_extract_bytes
        total_bytes = 0

        with zipfile.ZipFile(archive_path, "r") as zf:
            infos = zf.infolist()
            if len(infos) > max_files:
                raise SourceResolverError(f"Archive has too many entries ({len(infos)} > {max_files}).")

            for info in infos:
                dest = self._safe_destination(output_dir, info.filename)
                is_dir = info.is_dir()
                is_symlink = ((info.external_attr >> 16) & 0o170000) == 0o120000
                if is_symlink:
                    raise SourceResolverError(f"Archive contains symlink entry, which is not allowed: {info.filename}")

                if is_dir:
                    dest.mkdir(parents=True, exist_ok=True)
                    continue

                total_bytes += int(info.file_size)
                if total_bytes > max_bytes:
                    raise SourceResolverError(
                        f"Extracted data would exceed limit ({total_bytes} > {max_bytes} bytes)."
                    )

                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info, "r") as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)

    def _extract_tar_like(self, archive_path: Path, output_dir: Path) -> None:
        max_files = self.settings.resolver_max_extract_files
        max_bytes = self.settings.resolver_max_extract_bytes
        total_bytes = 0

        with tarfile.open(archive_path, "r:*") as tf:
            members = tf.getmembers()
            if len(members) > max_files:
                raise SourceResolverError(f"Archive has too many entries ({len(members)} > {max_files}).")

            for member in members:
                dest = self._safe_destination(output_dir, member.name)

                if member.issym() or member.islnk():
                    raise SourceResolverError(f"Archive contains link entry, which is not allowed: {member.name}")

                if member.isdir():
                    dest.mkdir(parents=True, exist_ok=True)
                    continue

                if not member.isfile():
                    continue

                total_bytes += int(member.size)
                if total_bytes > max_bytes:
                    raise SourceResolverError(
                        f"Extracted data would exceed limit ({total_bytes} > {max_bytes} bytes)."
                    )

                dest.parent.mkdir(parents=True, exist_ok=True)
                extracted = tf.extractfile(member)
                if extracted is None:
                    continue
                with extracted as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)

    def _download_file(self, url: str, destination: Path) -> None:
        timeout = float(self.settings.resolver_download_timeout_seconds)
        max_bytes = int(self.settings.resolver_max_download_bytes)

        req = Request(url, headers={"User-Agent": "TechFlow-Dev-Team-Simulator/1.0"})
        total = 0

        with urlopen(req, timeout=timeout) as resp, open(destination, "wb") as dst:
            content_length = resp.headers.get("Content-Length")
            if content_length:
                declared = int(content_length)
                if declared > max_bytes:
                    raise SourceResolverError(f"Download too large ({declared} > {max_bytes} bytes).")

            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise SourceResolverError(f"Download too large ({total} > {max_bytes} bytes).")
                dst.write(chunk)

    @staticmethod
    def _safe_destination(base_dir: Path, member_name: str) -> Path:
        target = (base_dir / member_name).resolve()
        base_resolved = base_dir.resolve()
        if base_resolved not in target.parents and target != base_resolved:
            raise SourceResolverError(f"Unsafe archive entry path: {member_name}")
        return target

    @staticmethod
    def _write_meta(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def _clean_path(path: Path) -> None:
        if path.exists():
            shutil.rmtree(path)

    @staticmethod
    def _is_http_url(value: str) -> bool:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    @staticmethod
    def _looks_like_git_source(value: str) -> bool:
        if value.startswith(("git@", "ssh://", "git://")):
            return True
        if value.endswith(".git"):
            return True
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    @staticmethod
    def _is_archive_name(value: str) -> bool:
        lower = value.lower()
        return any(lower.endswith(ext) for ext in ARCHIVE_SUFFIXES)

    @staticmethod
    def _archive_suffix(path_part: str) -> str:
        lower = path_part.lower()
        for ext in sorted(ARCHIVE_SUFFIXES, key=len, reverse=True):
            if lower.endswith(ext):
                return ext
        return ".archive"

    @staticmethod
    def _cache_key(source_url: str, source_kind: str, ref: Optional[str]) -> str:
        payload = json.dumps(
            {
                "source": source_url,
                "kind": source_kind,
                "ref": ref,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _pick_root_dir(extract_dir: Path) -> str:
        children = [item for item in extract_dir.iterdir() if item.name != "__MACOSX"]
        if len(children) == 1 and children[0].is_dir():
            return str(children[0])
        return str(extract_dir)
