from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def validate_site_artifact(
    site_dir: Path,
    *,
    required_publication_status: str | None = None,
) -> dict[str, Any]:
    site_dir = site_dir.resolve()
    manifest_path = site_dir / "data" / "site-manifest.json"
    if not manifest_path.is_file():
        raise ValueError("site manifest is missing: data/site-manifest.json")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "1.0":
        raise ValueError("unsupported site manifest schema_version")
    publication_status = manifest.get("publication_status")
    if required_publication_status and publication_status != required_publication_status:
        raise ValueError(
            "site publication_status is "
            f"{publication_status!r}; expected {required_publication_status!r}"
        )

    file_hashes = manifest.get("files")
    if not isinstance(file_hashes, dict) or not file_hashes:
        raise ValueError("site manifest files must be a non-empty object")
    expected_files = set(file_hashes) | {"data/site-manifest.json"}
    actual_files: set[str] = set()
    for path in site_dir.rglob("*"):
        if path.is_symlink():
            raise ValueError(
                f"site artifact must not contain symbolic links: {path.relative_to(site_dir)}"
            )
        if path.is_file():
            actual_files.add(path.relative_to(site_dir).as_posix())

    missing = sorted(expected_files - actual_files)
    unexpected = sorted(actual_files - expected_files)
    if missing or unexpected:
        raise ValueError(
            f"site artifact file set mismatch; missing={missing}, unexpected={unexpected}"
        )

    mismatches: list[str] = []
    for relative_path, expected_hash in sorted(file_hashes.items()):
        actual_hash = hashlib.sha256((site_dir / relative_path).read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            mismatches.append(relative_path)
    if mismatches:
        raise ValueError(f"site artifact SHA-256 mismatch: {mismatches}")
    if "index.html" not in file_hashes:
        raise ValueError("site artifact has no top-level index.html")

    return {
        "site_dir": site_dir.as_posix(),
        "publication_status": publication_status,
        "files_verified": len(expected_files),
        "papers": manifest.get("counts", {}).get("papers", 0),
        "archives": manifest.get("counts", {}).get("archives", 0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate a generated static-site artifact and its SHA-256 manifest."
    )
    parser.add_argument("--site-dir", type=Path, default=Path("site"))
    parser.add_argument(
        "--require-publication-status",
        choices=("preview", "published"),
    )
    args = parser.parse_args()
    result = validate_site_artifact(
        args.site_dir,
        required_publication_status=args.require_publication_status,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
