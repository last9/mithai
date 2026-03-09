"""GitHub skill — uses `gh` CLI for all operations.

Requires: gh CLI installed and authenticated (`gh auth login`).
No Node.js or MCP server needed.
"""

import json
import subprocess


def _gh(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """Run a gh command and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["gh"] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


def _gh_api(endpoint: str, method: str = "GET", body: dict | None = None,
            timeout: int = 30) -> tuple[bool, str]:
    """Call the GitHub API via `gh api` and return (ok, response_text)."""
    args = ["api", endpoint, "--method", method]
    if body:
        for k, v in body.items():
            args.extend(["-f", f"{k}={v}"])
    code, stdout, stderr = _gh(args, timeout=timeout)
    if code != 0:
        return False, stderr.strip() or stdout.strip()
    return True, stdout


TOOLS = [
    {
        "name": "get_file_contents",
        "description": "Get the contents of a file from a GitHub repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "path": {"type": "string", "description": "File path in the repository"},
                "ref": {"type": "string", "description": "Branch, tag, or commit SHA (default: default branch)"},
            },
            "required": ["owner", "repo", "path"],
        },
    },
    {
        "name": "search_code",
        "description": "Search for code across GitHub repositories.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (GitHub code search syntax)"},
                "owner": {"type": "string", "description": "Limit search to this owner/org"},
                "repo": {"type": "string", "description": "Limit search to this repo (owner/repo format)"},
                "limit": {"type": "integer", "description": "Max results (default: 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_commits",
        "description": "List recent commits on a branch.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "branch": {"type": "string", "description": "Branch name (default: default branch)"},
                "limit": {"type": "integer", "description": "Max commits to return (default: 10)"},
            },
            "required": ["owner", "repo"],
        },
    },
    {
        "name": "get_pull_request",
        "description": "Get details of a pull request including status checks and reviews.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "number": {"type": "integer", "description": "PR number"},
            },
            "required": ["owner", "repo", "number"],
        },
    },
    {
        "name": "list_pull_requests",
        "description": "List pull requests for a repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "state": {"type": "string", "description": "Filter by state: open, closed, merged, all (default: open)"},
                "limit": {"type": "integer", "description": "Max PRs to return (default: 10)"},
            },
            "required": ["owner", "repo"],
        },
    },
    {
        "name": "create_pull_request",
        "description": "Create a new pull request.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "title": {"type": "string", "description": "PR title"},
                "body": {"type": "string", "description": "PR description (markdown)"},
                "head": {"type": "string", "description": "Branch containing changes"},
                "base": {"type": "string", "description": "Branch to merge into (default: default branch)"},
            },
            "required": ["owner", "repo", "title", "head"],
        },
        "human": "approve",
    },
    {
        "name": "create_branch",
        "description": "Create a new branch in a repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "branch": {"type": "string", "description": "New branch name"},
                "from_branch": {"type": "string", "description": "Source branch (default: default branch)"},
            },
            "required": ["owner", "repo", "branch"],
        },
        "human": "approve",
    },
    {
        "name": "create_or_update_file",
        "description": "Create or update a file in a repository (commits directly).",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "path": {"type": "string", "description": "File path"},
                "content": {"type": "string", "description": "File content"},
                "message": {"type": "string", "description": "Commit message"},
                "branch": {"type": "string", "description": "Branch to commit to"},
                "sha": {"type": "string", "description": "SHA of file being replaced (required for updates)"},
            },
            "required": ["owner", "repo", "path", "content", "message", "branch"],
        },
        "human": "approve",
    },
    {
        "name": "list_issues",
        "description": "List issues for a repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "state": {"type": "string", "description": "Filter: open, closed, all (default: open)"},
                "labels": {"type": "string", "description": "Comma-separated label filter"},
                "limit": {"type": "integer", "description": "Max issues (default: 10)"},
            },
            "required": ["owner", "repo"],
        },
    },
    {
        "name": "get_issue",
        "description": "Get details of a specific issue including comments.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "number": {"type": "integer", "description": "Issue number"},
            },
            "required": ["owner", "repo", "number"],
        },
    },
    {
        "name": "list_releases",
        "description": "List releases for a repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "limit": {"type": "integer", "description": "Max releases (default: 5)"},
            },
            "required": ["owner", "repo"],
        },
    },
    {
        "name": "get_workflow_runs",
        "description": "List recent CI/CD workflow runs for a repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "branch": {"type": "string", "description": "Filter by branch"},
                "status": {"type": "string", "description": "Filter: completed, in_progress, queued"},
                "limit": {"type": "integer", "description": "Max runs (default: 5)"},
            },
            "required": ["owner", "repo"],
        },
    },
]


def _get_file_contents(inp: dict) -> str:
    owner, repo, path = inp["owner"], inp["repo"], inp["path"]
    ref = inp.get("ref", "")
    endpoint = f"/repos/{owner}/{repo}/contents/{path}"
    if ref:
        endpoint += f"?ref={ref}"
    ok, resp = _gh_api(endpoint)
    if not ok:
        return json.dumps({"error": resp})
    try:
        data = json.loads(resp)
        if data.get("encoding") == "base64":
            import base64
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
            return json.dumps({
                "path": data.get("path", path),
                "size": data.get("size", 0),
                "sha": data.get("sha", ""),
                "content": content,
            })
        return resp
    except (json.JSONDecodeError, KeyError):
        return resp


def _search_code(inp: dict) -> str:
    query = inp["query"]
    if inp.get("repo"):
        query += f" repo:{inp['repo']}"
    elif inp.get("owner"):
        query += f" org:{inp['owner']}"
    limit = inp.get("limit", 10)

    code, stdout, stderr = _gh(
        ["search", "code", query, "--limit", str(limit), "--json",
         "path,repository,textMatches"],
        timeout=30,
    )
    if code != 0:
        return json.dumps({"error": stderr.strip()})
    return stdout


def _list_commits(inp: dict) -> str:
    owner, repo = inp["owner"], inp["repo"]
    limit = inp.get("limit", 10)
    args = ["api", f"/repos/{owner}/{repo}/commits", "--method", "GET",
            "-f", f"per_page={limit}"]
    if inp.get("branch"):
        args.extend(["-f", f"sha={inp['branch']}"])
    code, stdout, stderr = _gh(args)
    if code != 0:
        return json.dumps({"error": stderr.strip()})
    try:
        commits = json.loads(stdout)
        return json.dumps([{
            "sha": c["sha"][:8],
            "message": c["commit"]["message"].split("\n")[0],
            "author": c["commit"]["author"]["name"],
            "date": c["commit"]["author"]["date"],
        } for c in commits])
    except (json.JSONDecodeError, KeyError):
        return stdout


def _get_pull_request(inp: dict) -> str:
    owner, repo, number = inp["owner"], inp["repo"], inp["number"]
    code, stdout, stderr = _gh(
        ["pr", "view", str(number), "--repo", f"{owner}/{repo}",
         "--json", "number,title,state,body,headRefName,baseRefName,"
                   "author,mergeable,reviewDecision,statusCheckRollup,"
                   "additions,deletions,changedFiles"],
    )
    if code != 0:
        return json.dumps({"error": stderr.strip()})
    return stdout


def _list_pull_requests(inp: dict) -> str:
    owner, repo = inp["owner"], inp["repo"]
    state = inp.get("state", "open")
    limit = inp.get("limit", 10)
    code, stdout, stderr = _gh(
        ["pr", "list", "--repo", f"{owner}/{repo}",
         "--state", state, "--limit", str(limit),
         "--json", "number,title,state,author,headRefName,updatedAt"],
    )
    if code != 0:
        return json.dumps({"error": stderr.strip()})
    return stdout


def _create_pull_request(inp: dict) -> str:
    owner, repo = inp["owner"], inp["repo"]
    args = ["pr", "create", "--repo", f"{owner}/{repo}",
            "--title", inp["title"],
            "--head", inp["head"]]
    if inp.get("base"):
        args.extend(["--base", inp["base"]])
    if inp.get("body"):
        args.extend(["--body", inp["body"]])
    code, stdout, stderr = _gh(args, timeout=30)
    if code != 0:
        return json.dumps({"error": stderr.strip()})
    return json.dumps({"url": stdout.strip()})


def _create_branch(inp: dict) -> str:
    owner, repo = inp["owner"], inp["repo"]
    branch = inp["branch"]
    from_branch = inp.get("from_branch", "")

    # Get the SHA of the source branch
    source = from_branch or "HEAD"
    ok, resp = _gh_api(f"/repos/{owner}/{repo}/git/ref/heads/{source if from_branch else ''}")
    if not from_branch:
        # Get default branch SHA
        ok, resp = _gh_api(f"/repos/{owner}/{repo}")
        if not ok:
            return json.dumps({"error": resp})
        try:
            default_branch = json.loads(resp)["default_branch"]
        except (json.JSONDecodeError, KeyError):
            return json.dumps({"error": "Could not determine default branch"})
        ok, resp = _gh_api(f"/repos/{owner}/{repo}/git/ref/heads/{default_branch}")

    if not ok:
        return json.dumps({"error": resp})
    try:
        sha = json.loads(resp)["object"]["sha"]
    except (json.JSONDecodeError, KeyError):
        return json.dumps({"error": "Could not get source branch SHA"})

    # Create the new branch ref
    code, stdout, stderr = _gh(
        ["api", f"/repos/{owner}/{repo}/git/refs",
         "--method", "POST",
         "-f", f"ref=refs/heads/{branch}",
         "-f", f"sha={sha}"],
    )
    if code != 0:
        return json.dumps({"error": stderr.strip()})
    return json.dumps({"branch": branch, "sha": sha[:8]})


def _create_or_update_file(inp: dict) -> str:
    owner, repo = inp["owner"], inp["repo"]
    path, content = inp["path"], inp["content"]
    message, branch = inp["message"], inp["branch"]

    import base64
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")

    args = ["api", f"/repos/{owner}/{repo}/contents/{path}",
            "--method", "PUT",
            "-f", f"message={message}",
            "-f", f"content={encoded}",
            "-f", f"branch={branch}"]
    if inp.get("sha"):
        args.extend(["-f", f"sha={inp['sha']}"])

    code, stdout, stderr = _gh(args, timeout=30)
    if code != 0:
        return json.dumps({"error": stderr.strip()})
    try:
        data = json.loads(stdout)
        return json.dumps({
            "path": data.get("content", {}).get("path", path),
            "sha": data.get("content", {}).get("sha", "")[:8],
            "commit_sha": data.get("commit", {}).get("sha", "")[:8],
        })
    except json.JSONDecodeError:
        return stdout


def _list_issues(inp: dict) -> str:
    owner, repo = inp["owner"], inp["repo"]
    state = inp.get("state", "open")
    limit = inp.get("limit", 10)
    args = ["issue", "list", "--repo", f"{owner}/{repo}",
            "--state", state, "--limit", str(limit),
            "--json", "number,title,state,author,labels,updatedAt"]
    if inp.get("labels"):
        args.extend(["--label", inp["labels"]])
    code, stdout, stderr = _gh(args)
    if code != 0:
        return json.dumps({"error": stderr.strip()})
    return stdout


def _get_issue(inp: dict) -> str:
    owner, repo, number = inp["owner"], inp["repo"], inp["number"]
    code, stdout, stderr = _gh(
        ["issue", "view", str(number), "--repo", f"{owner}/{repo}",
         "--json", "number,title,state,body,author,labels,comments,assignees"],
    )
    if code != 0:
        return json.dumps({"error": stderr.strip()})
    return stdout


def _list_releases(inp: dict) -> str:
    owner, repo = inp["owner"], inp["repo"]
    limit = inp.get("limit", 5)
    code, stdout, stderr = _gh(
        ["release", "list", "--repo", f"{owner}/{repo}",
         "--limit", str(limit)],
    )
    if code != 0:
        return json.dumps({"error": stderr.strip()})
    return stdout


def _get_workflow_runs(inp: dict) -> str:
    owner, repo = inp["owner"], inp["repo"]
    limit = inp.get("limit", 5)
    args = ["run", "list", "--repo", f"{owner}/{repo}",
            "--limit", str(limit),
            "--json", "databaseId,name,status,conclusion,headBranch,createdAt,url"]
    if inp.get("branch"):
        args.extend(["--branch", inp["branch"]])
    if inp.get("status"):
        args.extend(["--status", inp["status"]])
    code, stdout, stderr = _gh(args)
    if code != 0:
        return json.dumps({"error": stderr.strip()})
    return stdout


_HANDLERS = {
    "get_file_contents": _get_file_contents,
    "search_code": _search_code,
    "list_commits": _list_commits,
    "get_pull_request": _get_pull_request,
    "list_pull_requests": _list_pull_requests,
    "create_pull_request": _create_pull_request,
    "create_branch": _create_branch,
    "create_or_update_file": _create_or_update_file,
    "list_issues": _list_issues,
    "get_issue": _get_issue,
    "list_releases": _list_releases,
    "get_workflow_runs": _get_workflow_runs,
}


def handle(name: str, inp: dict, ctx: dict) -> str:
    handler = _HANDLERS.get(name)
    if handler:
        try:
            return handler(inp)
        except subprocess.TimeoutExpired:
            return json.dumps({"error": f"Command timed out: {name}"})
        except FileNotFoundError:
            return json.dumps({"error": "gh CLI not found. Install: https://cli.github.com/"})
        except Exception as e:
            return json.dumps({"error": str(e)})
    return json.dumps({"error": f"Unknown tool: {name}"})
