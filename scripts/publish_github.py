"""Publish the committed source and Windows bundle using existing Git credentials.

Authentication is read from Git Credential Manager into memory. Credentials are
never printed, placed in URLs, or written to the repository.
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
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"


def git(*args: str, capture: bool = True) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", *args], cwd=ROOT,
        text=True, encoding="utf-8", capture_output=capture, check=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"},
    )
    return result.stdout.strip() if capture else ""


class GitHub:
    def __init__(self) -> None:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={ROOT.as_posix()}", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n", cwd=ROOT,
            text=True, capture_output=True, timeout=30,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"},
        )
        fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if result.returncode or not fields.get("password"):
            raise RuntimeError("No GitHub credential available. Sign in through Git Credential Manager first.")
        self._token = fields["password"]

    def request(self, route: str, *, method: str = "GET", data: dict | None = None,
                file: Path | None = None, missing_ok: bool = False) -> dict | None:
        url = route if route.startswith("https://") else API + route
        if urllib.parse.urlparse(url).netloc not in {"api.github.com", "uploads.github.com"}:
            raise RuntimeError("Refusing to send GitHub credentials to another host")
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "QubitSequencer-publisher",
        }
        payload = None
        if file is not None:
            payload = file.open("rb")
            headers["Content-Type"] = "application/octet-stream"
            headers["Content-Length"] = str(file.stat().st_size)
        elif data is not None:
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            request = urllib.request.Request(url, data=payload, headers=headers, method=method)
            with urllib.request.urlopen(request, timeout=300 if file is not None else 60) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if missing_ok and exc.code in (404, 409):
                return None
            raise RuntimeError(f"GitHub returned HTTP {exc.code} for {method} {urllib.parse.urlparse(url).path}") from None
        finally:
            if file is not None and payload is not None:
                payload.close()


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def publish(client: GitHub, login: str, args: argparse.Namespace) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.repository):
        raise RuntimeError("Invalid repository name")
    owner = args.owner or login
    if not re.fullmatch(r"[A-Za-z0-9-]+", owner):
        raise RuntimeError("Invalid repository owner")
    if git("status", "--porcelain"):
        raise RuntimeError("Commit the prepared source before publishing")
    tracked = git("ls-files").splitlines()
    for name in tracked:
        if name.startswith(("projects/", ".build_deps/")) or (
            name.startswith("output/") and name != "output/pdf/user-guide.pdf"
        ):
            raise RuntimeError(f"Local-only file is tracked: {name}")
        if ".qchip" in name and name not in {
            "examples/square_lattice.qchip", "examples/surface_d3.qchip",
            "examples/surface_d3_complete.qchip",
            "examples/chip_design_release_demo.qchip",
            "examples/fridge_wiring_demo_samples/sample_A.qchip",
            "examples/fridge_wiring_demo_samples/sample_B.qchip",
        }:
            raise RuntimeError(f"Non-demo chip data is tracked: {name}")

    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    tag = f"v{version}"
    notes = ROOT / "docs" / "releases" / f"{tag}.md"
    archive = ROOT / "output" / "release" / f"QubitSequencer-{version}-win64.zip"
    manual = ROOT / "output" / "pdf" / "user-guide.pdf"
    for path in (notes, archive, manual):
        if not path.is_file():
            raise RuntimeError(f"Missing release file: {path.name}")
    with zipfile.ZipFile(archive) as bundle:
        names = {name.replace("\\", "/") for name in bundle.namelist()}
        for relative in ("QubitSequencer.exe", "THIRD_PARTY_NOTICES.md",
                         "examples/fridge_wiring_demo.qfridge",
                         "examples/fridge_wiring_demo_samples/sample_A.qchip",
                         "examples/fridge_wiring_demo_samples/sample_B.qchip",
                         "licenses/third-party/Qt-LGPL-3.0.txt",
                         "licenses/third-party/PySide6-LGPL-3.0.txt"):
            if "QubitSequencer/" + relative not in names:
                raise RuntimeError(f"Release archive is missing {relative}")
        if (ROOT / "LICENSE").is_file() and "QubitSequencer/LICENSE" not in names:
            raise RuntimeError("Copy the application LICENSE into the Windows bundle before publishing")
    checksums = archive.parent / "SHA256SUMS.txt"
    checksums.write_text(
        "".join(f"{digest(path)}  {path.name}\n" for path in (archive, manual)), encoding="ascii"
    )
    assets = (archive, manual, checksums)
    commit = git("rev-parse", "HEAD")
    repository_path = f"/repos/{owner}/{args.repository}"
    repository = client.request(repository_path, missing_ok=True)
    if repository is None:
        create_path = "/user/repos" if owner == login else f"/orgs/{owner}/repos"
        repository = client.request(create_path, method="POST", data={
            "name": args.repository,
            "description": "Superconducting quantum chip registry with a Python/PySide6 desktop interface",
            "private": args.visibility == "private", "auto_init": False,
        })
        print(f"Created repository: {repository['html_url']}", flush=True)
    if repository["private"] != (args.visibility == "private"):
        raise RuntimeError("Existing repository visibility differs from the requested visibility")
    branch = client.request(repository_path + "/git/ref/heads/main", missing_ok=True)
    if branch is not None and branch["object"]["sha"] != commit:
        raise RuntimeError("Existing main branch contains another commit; refusing to overwrite it")
    release = client.request(repository_path + f"/releases/tags/{tag}", missing_ok=True)
    if release is None:
        # The tag endpoint may omit drafts; resume an interrupted upload by ID.
        release = next((item for item in client.request(repository_path + "/releases?per_page=100")
                        if item["tag_name"] == tag), None)
    if release is not None and not release["draft"]:
        raise RuntimeError("This version is already published; refusing to replace it")

    remote = f"https://github.com/{owner}/{args.repository}.git"
    if "origin" in git("remote").splitlines():
        if git("remote", "get-url", "origin") != remote:
            raise RuntimeError("Existing origin points to a different repository")
    else:
        git("remote", "add", "origin", remote)
    if tag in git("tag", "--list", tag).splitlines():
        if git("rev-parse", f"{tag}^{{commit}}") != commit:
            raise RuntimeError("Existing version tag points to another commit")
    else:
        git("tag", "-a", tag, "-m", f"Qubit Sequencer {version}")
    print("Pushing main branch and release tag", flush=True)
    git("push", "--atomic", "origin", "HEAD:refs/heads/main", f"refs/tags/{tag}", capture=False)
    if release is None:
        release = client.request(repository_path + "/releases", method="POST", data={
            "tag_name": tag, "target_commitish": commit,
            "name": f"Qubit Sequencer {version}",
            "body": notes.read_text(encoding="utf-8"), "draft": True, "prerelease": False,
        })
    existing = {asset["name"]: asset for asset in release["assets"]}
    upload_url = release["upload_url"].split("{", 1)[0]
    for path in assets:
        if path.name in existing:
            asset = existing[path.name]
            if asset["size"] != path.stat().st_size or asset.get("digest") != "sha256:" + digest(path):
                raise RuntimeError(f"Existing draft asset differs: {path.name}")
            continue
        print(f"Uploading {path.name} ({path.stat().st_size:,} bytes)", flush=True)
        asset = client.request(upload_url + "?" + urllib.parse.urlencode({"name": path.name}),
                               method="POST", file=path)
        if asset["size"] != path.stat().st_size or asset["state"] != "uploaded":
            raise RuntimeError(f"Upload verification failed: {path.name}")
        if asset.get("digest") and asset["digest"] != "sha256:" + digest(path):
            raise RuntimeError(f"Uploaded checksum differs: {path.name}")
    release = client.request(repository_path + f"/releases/{release['id']}", method="PATCH",
                             data={"draft": False})
    print(f"Repository: {repository['html_url']}", flush=True)
    print(f"Release: {release['html_url']}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-auth", action="store_true", help="check login without changing GitHub")
    parser.add_argument("--repository", default="QubitSequencer")
    parser.add_argument("--owner", help="organization owner; defaults to the authenticated account")
    parser.add_argument("--visibility", choices=("public", "private"))
    args = parser.parse_args()
    if not args.check_auth and args.visibility is None:
        parser.error("--visibility is required for publishing")
    try:
        client = GitHub()
        login = client.request("/user")["login"]
        print(f"Authenticated GitHub account: {login}", flush=True)
        if not args.check_auth:
            publish(client, login, args)
        return 0
    except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
        print(f"Publish stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
