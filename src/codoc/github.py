from __future__ import annotations

import json
from typing import Any, Protocol
import urllib.error
import urllib.parse
import urllib.request


class GitHubClient(Protocol):
    def get_pull_request(self, repo: str, pr_number: int) -> dict[str, Any]:
        ...

    def list_comments(self, repo: str, pr_number: int) -> list[dict[str, Any]]:
        ...

    def create_comment(self, repo: str, pr_number: int, body: str) -> dict[str, Any]:
        ...

    def update_comment(self, repo: str, comment_id: int, body: str) -> dict[str, Any]:
        ...

    def upsert_pr_comment(
        self,
        repo: str,
        pr_number: int,
        body: str,
        marker: str = "<!-- codoc-report -->",
    ) -> dict[str, Any]:
        ...

    def get_branch(self, repo: str, branch: str) -> dict[str, Any] | None:
        ...

    def create_or_update_branch(self, repo: str, branch: str, sha: str) -> None:
        ...

    def create_or_update_file(
        self,
        repo: str,
        branch: str,
        path: str,
        content: str,
        message: str,
    ) -> None:
        ...

    def find_pull_request(
        self,
        repo: str,
        head: str,
        base: str,
    ) -> dict[str, Any] | None:
        ...

    def create_pull_request(
        self,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> dict[str, Any]:
        ...

    def update_pull_request(
        self,
        repo: str,
        pr_number: int,
        title: str | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        ...

    def enable_auto_merge(self, repo: str, pr_number: int) -> bool:
        ...


class BaseGitHubClient:
    def upsert_pr_comment(
        self,
        repo: str,
        pr_number: int,
        body: str,
        marker: str = "<!-- codoc-report -->",
    ) -> dict[str, Any]:
        comments = self.list_comments(repo, pr_number)  # type: ignore[attr-defined]
        for c in comments:
            if marker in c.get("body", ""):
                res: dict[str, Any] = self.update_comment(repo, c["id"], body)  # type: ignore[attr-defined]
                return res
        created: dict[str, Any] = self.create_comment(repo, pr_number, body)  # type: ignore[attr-defined]
        return created


class FakeGitHubClient(BaseGitHubClient):
    """In-memory test double for GitHub API interactions."""

    def __init__(self) -> None:
        self.comments: dict[str, dict[int, list[dict[str, Any]]]] = {}
        self.pull_requests: dict[str, list[dict[str, Any]]] = {}
        self.branches: dict[str, dict[str, str]] = {}
        self.files: dict[str, dict[str, dict[str, str]]] = {}
        self.auto_merge_requests: list[tuple[str, int]] = []
        self._next_comment_id = 1
        self._next_pr_number = 100

    def get_pull_request(self, repo: str, pr_number: int) -> dict[str, Any]:
        prs = self.pull_requests.get(repo, [])
        for pr in prs:
            if pr.get("number") == pr_number:
                return pr
        return {
            "number": pr_number,
            "head": {"ref": "feature", "sha": "headsha", "repo": {"full_name": repo}},
            "base": {"ref": "main", "sha": "basesha", "repo": {"full_name": repo}},
        }

    def list_comments(self, repo: str, pr_number: int) -> list[dict[str, Any]]:
        return list(self.comments.get(repo, {}).get(pr_number, []))

    def create_comment(self, repo: str, pr_number: int, body: str) -> dict[str, Any]:
        comment = {"id": self._next_comment_id, "body": body}
        self._next_comment_id += 1
        self.comments.setdefault(repo, {}).setdefault(pr_number, []).append(comment)
        return comment

    def update_comment(self, repo: str, comment_id: int, body: str) -> dict[str, Any]:
        for repo_comments in self.comments.values():
            for pr_comments in repo_comments.values():
                for c in pr_comments:
                    if c["id"] == comment_id:
                        c["body"] = body
                        return c
        raise KeyError(f"Comment {comment_id} not found")

    def get_branch(self, repo: str, branch: str) -> dict[str, Any] | None:
        repo_branches = self.branches.get(repo, {})
        if branch in repo_branches:
            return {"name": branch, "commit": {"sha": repo_branches[branch]}}
        return None

    def create_or_update_branch(self, repo: str, branch: str, sha: str) -> None:
        self.branches.setdefault(repo, {})[branch] = sha

    def create_or_update_file(
        self,
        repo: str,
        branch: str,
        path: str,
        content: str,
        message: str,
    ) -> None:
        self.files.setdefault(repo, {}).setdefault(branch, {})[path] = content

    def find_pull_request(
        self,
        repo: str,
        head: str,
        base: str,
    ) -> dict[str, Any] | None:
        for pr in self.pull_requests.get(repo, []):
            if pr.get("head", {}).get("ref") == head and pr.get("base", {}).get("ref") == base:
                return pr
        return None

    def create_pull_request(
        self,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> dict[str, Any]:
        pr_number = self._next_pr_number
        self._next_pr_number += 1
        pr = {
            "number": pr_number,
            "title": title,
            "body": body,
            "html_url": f"https://github.com/{repo}/pull/{pr_number}",
            "head": {"ref": head, "repo": {"full_name": repo}},
            "base": {"ref": base, "repo": {"full_name": repo}},
        }
        self.pull_requests.setdefault(repo, []).append(pr)
        return pr

    def update_pull_request(
        self,
        repo: str,
        pr_number: int,
        title: str | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        for pr in self.pull_requests.get(repo, []):
            if pr.get("number") == pr_number:
                if title is not None:
                    pr["title"] = title
                if body is not None:
                    pr["body"] = body
                return pr
        raise KeyError(f"PR {pr_number} not found")

    def enable_auto_merge(self, repo: str, pr_number: int) -> bool:
        self.auto_merge_requests.append((repo, pr_number))
        return True


class DefaultGitHubClient(BaseGitHubClient):
    """Stdlib urllib-backed GitHub REST API client."""

    def __init__(self, token: str, base_url: str = "https://api.github.com") -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "CoDoc-Action",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as response:
                if response.status == 204:
                    return None
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8")
            raise RuntimeError(f"GitHub API {method} {path} failed ({err.code}): {err_body}") from err

    def get_pull_request(self, repo: str, pr_number: int) -> dict[str, Any]:
        return self._request("GET", f"/repos/{repo}/pulls/{pr_number}")  # type: ignore[no-any-return]

    def list_comments(self, repo: str, pr_number: int) -> list[dict[str, Any]]:
        return self._request("GET", f"/repos/{repo}/issues/{pr_number}/comments")  # type: ignore[no-any-return]

    def create_comment(self, repo: str, pr_number: int, body: str) -> dict[str, Any]:
        return self._request("POST", f"/repos/{repo}/issues/{pr_number}/comments", {"body": body})  # type: ignore[no-any-return]

    def update_comment(self, repo: str, comment_id: int, body: str) -> dict[str, Any]:
        return self._request("PATCH", f"/repos/{repo}/issues/comments/{comment_id}", {"body": body})  # type: ignore[no-any-return]

    def get_branch(self, repo: str, branch: str) -> dict[str, Any] | None:
        try:
            return self._request("GET", f"/repos/{repo}/branches/{urllib.parse.quote(branch, safe='')}")  # type: ignore[no-any-return]
        except RuntimeError as e:
            if "404" in str(e):
                return None
            raise

    def create_or_update_branch(self, repo: str, branch: str, sha: str) -> None:
        existing = self.get_branch(repo, branch)
        if existing is None:
            self._request("POST", f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": sha})
        else:
            self._request("PATCH", f"/repos/{repo}/git/refs/heads/{urllib.parse.quote(branch, safe='')}", {"sha": sha, "force": True})

    def create_or_update_file(
        self,
        repo: str,
        branch: str,
        path: str,
        content: str,
        message: str,
    ) -> None:
        import base64
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        # Check if file exists to get its sha
        sha: str | None = None
        try:
            current = self._request("GET", f"/repos/{repo}/contents/{path}?ref={branch}")
            if isinstance(current, dict) and "sha" in current:
                sha = current["sha"]
        except RuntimeError:
            pass
        body: dict[str, Any] = {
            "message": message,
            "content": encoded,
            "branch": branch,
        }
        if sha is not None:
            body["sha"] = sha
        self._request("PUT", f"/repos/{repo}/contents/{path}", body)

    def find_pull_request(
        self,
        repo: str,
        head: str,
        base: str,
    ) -> dict[str, Any] | None:
        # head can be 'owner:branch' or just 'branch'
        pulls = self._request("GET", f"/repos/{repo}/pulls?state=open")
        if isinstance(pulls, list):
            for pr in pulls:
                if isinstance(pr, dict) and pr.get("head", {}).get("ref") == head and pr.get("base", {}).get("ref") == base:
                    return dict(pr)
        return None

    def create_pull_request(
        self,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> dict[str, Any]:
        return self._request(  # type: ignore[no-any-return]
            "POST",
            f"/repos/{repo}/pulls",
            {"title": title, "body": body, "head": head, "base": base},
        )

    def update_pull_request(
        self,
        repo: str,
        pr_number: int,
        title: str | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        return self._request("PATCH", f"/repos/{repo}/pulls/{pr_number}", payload)  # type: ignore[no-any-return]

    def enable_auto_merge(self, repo: str, pr_number: int) -> bool:
        # GitHub GraphQL API is normally used for auto-merge, or REST where supported
        # We attempt auto-merge and catch refusal gracefully
        try:
            self._request("PUT", f"/repos/{repo}/pulls/{pr_number}/merge", {"merge_method": "squash", "auto_merge": True})
            return True
        except RuntimeError:
            return False
