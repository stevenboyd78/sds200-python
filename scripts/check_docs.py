from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = (
    Path("README.md"),
    Path("CHANGELOG.md"),
    Path("CONTRIBUTING.md"),
    Path("CODE_OF_CONDUCT.md"),
    Path("SECURITY.md"),
    Path("SUPPORT.md"),
    Path("docs/releasing.md"),
    Path("docs/supported-models.md"),
    Path(".github/PULL_REQUEST_TEMPLATE.md"),
    Path(".github/ISSUE_TEMPLATE/bug_report.yml"),
    Path(".github/ISSUE_TEMPLATE/feature_request.yml"),
)

MARKDOWN_LINK = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")


def markdown_files() -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for path in ROOT.rglob("*.md")
            if not any(part.startswith(".") and part != ".github" for part in path.parts)
            and ".venv" not in path.parts
            and "build" not in path.parts
            and "dist" not in path.parts
        )
    )


def local_link_target(markdown: Path, raw_target: str) -> Path | None:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    target = target.split(maxsplit=1)[0].strip("\"'")
    if not target or target.startswith(("#", "http://", "https://", "mailto:")):
        return None

    target = unquote(target.split("#", 1)[0])
    if not target:
        return None

    resolved = (markdown.parent / target).resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError(f"{markdown.relative_to(ROOT)} links outside the repository") from exc
    return resolved


def main() -> int:
    errors: list[str] = []

    for required in REQUIRED_FILES:
        if not (ROOT / required).exists():
            errors.append(f"Missing required project file: {required}")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if "Milestone 1 provides:" in readme:
        errors.append("README still leads with obsolete Milestone 1 documentation.")
    if "docs/transports.md" not in readme:
        errors.append("README does not link to transport documentation.")
    if "SECURITY.md" not in readme:
        errors.append("README does not link to the security policy.")

    for markdown in markdown_files():
        text = markdown.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(text):
            try:
                target = local_link_target(markdown, raw_target)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if target is not None and not target.exists():
                errors.append(
                    f"{markdown.relative_to(ROOT)} has a broken link: {raw_target}"
                )

    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"Documentation checks passed for {len(markdown_files())} Markdown files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
