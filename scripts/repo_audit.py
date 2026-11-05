#!/usr/bin/env python3
"""Python/深度学习算法仓库的只读体检工具，无需第三方依赖。

扫描结果仅供人工或 Agent 进一步核实，不代表可以自动删除文件。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

GENERATED_DIRS = {"outputs", "output", "runs", "checkpoints", "logs", "wandb"}
PRUNE_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
    ".ruff_cache", "build", "dist", "data", "datasets", "third_party", "vendor",
    *GENERATED_DIRS,
}
ARTIFACT_SUFFIXES = {
    ".pt", ".pth", ".ckpt", ".safetensors", ".onnx", ".om", ".engine",
    ".tflite", ".h5", ".weights", ".pyc", ".log",
}
CONTENT_SUFFIXES = {".py", ".yaml", ".yml", ".toml", ".json", ".sh"}
VERSION_RE = re.compile(r"(?:^|[_-])(?:final|copy|bak|backup|new|old|v[0-9]+)(?=[_.-]|$)", re.I)
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
PRIORITY_LABELS = {"high": "高", "medium": "中", "low": "低"}
CATEGORY_LABELS = {
    "generated_artifact": "疑似生成产物",
    "duplicate_content": "内容重复",
    "versioned_name": "疑似版本式命名",
    "unexpected_top_level": "顶层目录待确认",
    "oversized_python": "Python 文件偏长",
}
STATUS_LABELS = {"tracked": "已跟踪", "untracked": "未跟踪", "on_disk": "磁盘文件", "mixed": "混合"}
MAX_HASH_BYTES = 2_000_000
MAX_SCAN_FILES = 30_000


def git_paths(root: Path, args: list[str]) -> list[str] | None:
    command = ["git", "-C", str(root), "ls-files", "-z", *args]
    try:
        result = subprocess.run(command, capture_output=True, check=False, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    return [os.fsdecode(value) for value in result.stdout.split(b"\0") if value]


def changed_paths(root: Path, base: str) -> list[str] | None:
    command = ["git", "-C", str(root), "diff", "--name-only", "-z",
               "--diff-filter=ACMR", base, "--"]
    try:
        result = subprocess.run(command, capture_output=True, check=False, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    return [os.fsdecode(value) for value in result.stdout.split(b"\0") if value]


def collect_files(root: Path, changed_from: str | None = None) -> tuple[list[tuple[Path, str]], str]:
    tracked = git_paths(root, ["--cached"])
    if tracked is not None:
        untracked = git_paths(root, ["--others", "--exclude-standard"]) or []
        tracked_set = set(tracked)
        if changed_from is not None:
            changed = changed_paths(root, changed_from)
            if changed is None:
                raise ValueError(f"无法解析 Git 变更基线：{changed_from}")
            files = [(root / p, "tracked") for p in changed if p in tracked_set]
            files.extend((root / p, "untracked") for p in untracked if p not in tracked_set)
            return files[:MAX_SCAN_FILES], "git-changed"
        files = [(root / p, "tracked") for p in tracked]
        files.extend((root / p, "untracked") for p in untracked if p not in tracked_set)
        return files[:MAX_SCAN_FILES], "git"
    if changed_from is not None:
        raise ValueError("--changed-from 需要在 Git 仓库中使用。")

    result: list[tuple[Path, str]] = []
    for directory, subdirs, filenames in os.walk(root, followlinks=False):
        subdirs[:] = sorted(d for d in subdirs if d not in PRUNE_DIRS and not d.startswith("."))
        for filename in sorted(filenames):
            result.append((Path(directory) / filename, "on_disk"))
            if len(result) >= MAX_SCAN_FILES:
                return result, "filesystem"
    return result, "filesystem"


def finding(path: str, category: str, priority: str, evidence: str, status: str) -> dict[str, str]:
    return {
        "path": path,
        "category": category,
        "priority": priority,
        "evidence": evidence,
        "status": status,
    }


def audit(root: Path, allowed_top: set[str] | None, max_lines: int,
          changed_from: str | None = None) -> dict:
    files, source = collect_files(root, changed_from)
    findings: list[dict[str, str]] = []
    duplicates: dict[tuple[int, str], list[str]] = {}
    top_reported: set[str] = set()
    inspected = 0
    for path, status in files:
        try:
            relative = path.relative_to(root)
            # 不读取符号链接，防止扫描到仓库外的文件。
            if path.is_symlink() or not path.is_file():
                continue
            size = path.stat().st_size
        except (OSError, ValueError):
            continue
        inspected += 1
        display = relative.as_posix()
        parts = relative.parts
        suffix = path.suffix.lower()

        if len(parts) > 1 and allowed_top is not None and parts[0] not in allowed_top:
            top = parts[0]
            if top not in top_reported:
                top_reported.add(top)
                findings.append(finding(top + "/", "unexpected_top_level", "low",
                                        "不在 --allowed-top 白名单中；请确认是否为有意保留的目录。", status))

        in_generated_dir = any(part.lower() in GENERATED_DIRS for part in parts[:-1])
        if suffix in ARTIFACT_SUFFIXES or (in_generated_dir and suffix not in {".md", ".gitkeep"}):
            priority = "high" if status == "tracked" and in_generated_dir else "medium"
            findings.append(finding(display, "generated_artifact", priority,
                                    "疑似权重、缓存或运行输出；请确认是否为刻意保留的测试样例。", status))

        if suffix == ".py" and VERSION_RE.search(path.stem):
            findings.append(finding(display, "versioned_name", "low",
                                    "文件名带版本、final 或 copy 等后缀；可能是有意保留的模型变体。", status))

        if suffix == ".py" and size <= MAX_HASH_BYTES:
            try:
                with path.open("rb") as stream:
                    line_count = sum(chunk.count(b"\n") for chunk in iter(lambda: stream.read(65536), b""))
                if line_count > max_lines:
                    findings.append(finding(display, "oversized_python", "low",
                                            f"共 {line_count} 行，超过建议阈值 {max_lines} 行。", status))
            except OSError:
                pass

        if suffix in CONTENT_SUFFIXES and 120 <= size <= MAX_HASH_BYTES and path.name != "__init__.py":
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
            duplicates.setdefault((size, digest), []).append(display)

    for paths in duplicates.values():
        if len(paths) > 1:
            findings.append(finding(", ".join(sorted(paths)), "duplicate_content", "medium",
                                    "源码或配置文件内容完全相同；合并前仍需核查实际用途。", "mixed"))

    findings.sort(key=lambda item: (PRIORITY_ORDER[item["priority"]], item["category"], item["path"]))
    return {
        "root": str(root),
        "source": source,
        "inspected_files": inspected,
        "max_file_limit_reached": len(files) >= MAX_SCAN_FILES,
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="仓库根目录，默认当前目录。")
    parser.add_argument("--json", action="store_true", help="输出可供程序处理的 JSON 报告。")
    parser.add_argument("--allowed-top", help="可选的顶层目录白名单，多个目录用逗号分隔。")
    parser.add_argument("--changed-from", metavar="REF",
                        help="仅检查相对 Git REF 的变更和未忽略的新文件。")
    parser.add_argument("--max-lines", type=int, default=800, help="Python 文件建议行数上限。")
    parser.add_argument("--fail-on", choices=["none", "high", "medium", "low"], default="none",
                        help="CI 失败阈值；默认 none，仅报告问题而不返回失败状态。")
    args = parser.parse_args()
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir() or args.max_lines < 1:
        parser.error("--root 必须是目录，且 --max-lines 必须大于零。")
    allowed_top = set(v.strip() for v in args.allowed_top.split(",") if v.strip()) if args.allowed_top else None
    try:
        report = audit(root, allowed_top, args.max_lines, args.changed_from)
    except ValueError as error:
        parser.error(str(error))

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"只读体检：检查 {report['inspected_files']} 个文件（来源：{report['source']}）")
        print(f"仓库：{root}")
        if report["max_file_limit_reached"]:
            print("警告：已达到扫描文件数量上限，报告可能不完整。")
        if not report["findings"]:
            print("未发现启发式告警；这不代表仓库完全没有问题。")
        for item in report["findings"]:
            severity = PRIORITY_LABELS[item["priority"]]
            category = CATEGORY_LABELS.get(item["category"], item["category"])
            status = STATUS_LABELS.get(item["status"], item["status"])
            print(f"[{severity}] {category}：{item['path']}")
            print(f"    依据：{item['evidence']}（{status}）")

    if args.fail_on == "none":
        return 0
    limit = PRIORITY_ORDER[args.fail_on]
    return int(any(PRIORITY_ORDER[item["priority"]] <= limit for item in report["findings"]))


if __name__ == "__main__":
    sys.exit(main())
