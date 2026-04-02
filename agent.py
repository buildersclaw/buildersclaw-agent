import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from fastapi import HTTPException, Request

load_dotenv()

from bnbagent.apex.config import APEXConfig
from bnbagent.apex.server import create_apex_app


BUILDERSCLAW_BASE_URL = "https://www.buildersclaw.xyz/api/v1"

config = APEXConfig.from_env_optional() or APEXConfig()
config.service_price = "0"


def get_env(name: str, required: bool = True) -> str | None:
    value = os.getenv(name)
    if required and not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def env_flag(name: str) -> bool:
    return bool(os.getenv(name))


def run_cmd(command: list[str], cwd: str | None = None) -> dict:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def git_has_changes(local_path: str) -> bool:
    result = run_cmd(["git", "status", "--porcelain"], cwd=local_path)
    if not result["ok"]:
        raise ValueError(result["stderr"] or "Failed to inspect git status")
    return bool(result["stdout"])


def ensure_git_repo(local_path: str, branch: str) -> None:
    repo_path = Path(local_path)
    if not repo_path.exists() or not repo_path.is_dir():
        raise ValueError(f"Local path does not exist: {local_path}")

    if not (repo_path / ".git").exists():
        result = run_cmd(["git", "init", "-b", branch], cwd=local_path)
        if not result["ok"]:
            raise ValueError(result["stderr"] or "git init failed")


def parse_json_output(result: dict) -> dict | list | None:
    if not result.get("stdout"):
        return None
    try:
        return json.loads(result["stdout"])
    except json.JSONDecodeError:
        return None


def github_auth_check() -> dict:
    expected_username = get_env("GITHUB_USERNAME")

    status = run_cmd(["gh", "auth", "status"])
    if not status["ok"]:
        raise ValueError(status["stderr"] or "GitHub CLI is not authenticated")

    user = run_cmd(["gh", "api", "user"])
    if not user["ok"]:
        raise ValueError(user["stderr"] or "Failed to fetch GitHub user")

    payload = parse_json_output(user) or {}
    login = payload.get("login")
    matches = login == expected_username

    return {
        "authenticated": True,
        "login": login,
        "expected_username": expected_username,
        "username_matches": matches,
        "status": status["stdout"],
    }


def github_create_repo(args: dict) -> dict:
    username = get_env("GITHUB_USERNAME")
    name = args["name"]
    visibility_flag = "--public" if args.get("public", True) else "--private"

    command = [
        "gh",
        "repo",
        "create",
        f"{username}/{name}",
        visibility_flag,
        "--disable-wiki",
        "--confirm",
    ]
    if args.get("description"):
        command.extend(["--description", args["description"]])

    result = run_cmd(command)
    if not result["ok"]:
        raise ValueError(result["stderr"] or "Failed to create repo")

    return {
        "repo": f"{username}/{name}",
        "repo_url": f"https://github.com/{username}/{name}",
        "stdout": result["stdout"],
    }


def github_init_and_push(args: dict) -> dict:
    username = get_env("GITHUB_USERNAME")
    local_path = args["local_path"]
    repo_name = args["repo_name"]
    branch = args.get("branch", "main")
    commit_message = args.get("commit_message", "feat: initial BuildersClaw submission")
    repo_url = f"https://github.com/{username}/{repo_name}.git"

    ensure_git_repo(local_path, branch)

    auth_setup = run_cmd(["gh", "auth", "setup-git"], cwd=local_path)
    if not auth_setup["ok"]:
        raise ValueError(auth_setup["stderr"] or "Failed to configure git auth")

    remotes = run_cmd(["git", "remote"], cwd=local_path)
    if not remotes["ok"]:
        raise ValueError(remotes["stderr"] or "Failed to list remotes")
    if "origin" not in remotes["stdout"].split():
        add_remote = run_cmd(
            ["git", "remote", "add", "origin", repo_url], cwd=local_path
        )
        if not add_remote["ok"]:
            raise ValueError(add_remote["stderr"] or "Failed to add origin remote")
    else:
        set_remote = run_cmd(
            ["git", "remote", "set-url", "origin", repo_url], cwd=local_path
        )
        if not set_remote["ok"]:
            raise ValueError(set_remote["stderr"] or "Failed to update origin remote")

    add_result = run_cmd(["git", "add", "."], cwd=local_path)
    if not add_result["ok"]:
        raise ValueError(add_result["stderr"] or "git add failed")

    commit_created = False
    if git_has_changes(local_path):
        commit_result = run_cmd(["git", "commit", "-m", commit_message], cwd=local_path)
        if not commit_result["ok"]:
            raise ValueError(commit_result["stderr"] or "git commit failed")
        commit_created = True

    push_result = run_cmd(["git", "push", "-u", "origin", branch], cwd=local_path)
    if not push_result["ok"]:
        raise ValueError(push_result["stderr"] or "git push failed")

    return {
        "repo_url": f"https://github.com/{username}/{repo_name}",
        "branch": branch,
        "commit_created": commit_created,
        "stdout": push_result["stdout"],
    }


def github_clone_repo(args: dict) -> dict:
    repo = args["repo"]
    dest_path = args.get("dest_path")
    command = ["gh", "repo", "clone", repo]
    if dest_path:
        command.append(dest_path)
    result = run_cmd(command)
    if not result["ok"]:
        raise ValueError(result["stderr"] or "Failed to clone repo")
    return {"repo": repo, "stdout": result["stdout"]}


def github_add_collaborator(args: dict) -> dict:
    username = get_env("GITHUB_USERNAME")
    repo_name = args["repo_name"]
    collaborator = args["username"]
    permission = args.get("permission", "push")

    result = run_cmd(
        [
            "gh",
            "api",
            f"repos/{username}/{repo_name}/collaborators/{collaborator}",
            "-X",
            "PUT",
            "-f",
            f"permission={permission}",
        ]
    )
    if not result["ok"]:
        raise ValueError(result["stderr"] or "Failed to add collaborator")
    return {
        "repo": f"{username}/{repo_name}",
        "collaborator": collaborator,
        "permission": permission,
    }


def github_list_invitations() -> dict:
    result = run_cmd(["gh", "api", "user/repository_invitations"])
    if not result["ok"]:
        raise ValueError(result["stderr"] or "Failed to list invitations")
    return {"invitations": parse_json_output(result) or []}


def github_accept_invitation(args: dict) -> dict:
    invitation_id = str(args["invitation_id"])
    result = run_cmd(
        ["gh", "api", f"user/repository_invitations/{invitation_id}", "-X", "PATCH"]
    )
    if not result["ok"]:
        raise ValueError(result["stderr"] or "Failed to accept invitation")
    return {"invitation_id": invitation_id, "accepted": True}


def buildersclaw_request(
    method: str, path: str, data: dict | None = None, auth: bool = True
) -> dict:
    url = f"{BUILDERSCLAW_BASE_URL}{path}"
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {get_env('BUILDERSCLAW_API_KEY')}"

    payload = None
    if data is not None:
        payload = json.dumps(data).encode("utf-8")

    request = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        detail = body
        try:
            detail = json.loads(body)
        except json.JSONDecodeError:
            pass
        raise ValueError(f"BuildersClaw API error {exc.code}: {detail}") from exc


def buildersclaw_register(args: dict) -> dict:
    payload = {
        "name": args["name"],
        "display_name": args.get("display_name"),
        "wallet_address": args.get("wallet_address"),
        "github_username": args.get("github_username") or os.getenv("GITHUB_USERNAME"),
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    return buildersclaw_request("POST", "/agents/register", payload, auth=False)


def buildersclaw_me() -> dict:
    return buildersclaw_request("GET", "/agents/me")


def buildersclaw_list_hackathons(args: dict) -> dict:
    query = {}
    if args.get("status"):
        query["status"] = args["status"]
    suffix = f"?{urllib.parse.urlencode(query)}" if query else ""
    return buildersclaw_request("GET", f"/hackathons{suffix}", auth=False)


def buildersclaw_get_hackathon(args: dict) -> dict:
    return buildersclaw_request(
        "GET", f"/hackathons/{args['hackathon_id']}", auth=False
    )


def buildersclaw_get_contract(args: dict) -> dict:
    return buildersclaw_request(
        "GET", f"/hackathons/{args['hackathon_id']}/contract", auth=False
    )


def buildersclaw_join(args: dict) -> dict:
    hackathon_id = args["hackathon_id"]
    payload = args.get("payload") or {}
    return buildersclaw_request("POST", f"/hackathons/{hackathon_id}/join", payload)


def buildersclaw_submit_repo(args: dict) -> dict:
    payload = {
        "repo_url": args["repo_url"],
        "project_url": args.get("project_url"),
        "notes": args.get("notes"),
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    return buildersclaw_request(
        "POST",
        f"/hackathons/{args['hackathon_id']}/teams/{args['team_id']}/submit",
        payload,
    )


def buildersclaw_list_marketplace(args: dict) -> dict:
    query = {}
    for key in ("hackathon_id", "status"):
        if args.get(key):
            query[key] = args[key]
    suffix = f"?{urllib.parse.urlencode(query)}" if query else ""
    return buildersclaw_request("GET", f"/marketplace{suffix}", auth=False)


def buildersclaw_post_role(args: dict) -> dict:
    return buildersclaw_request("POST", "/marketplace", args)


def buildersclaw_take_role(args: dict) -> dict:
    return buildersclaw_request("POST", f"/marketplace/{args['listing_id']}/take", {})


def buildersclaw_claim_join_command(args: dict) -> dict:
    hackathon_id = args["hackathon_id"]
    contract = buildersclaw_get_contract({"hackathon_id": hackathon_id})

    escrow = contract.get("escrow_address") or contract.get("contract_address")
    entry_fee = contract.get("entry_fee_wei")
    rpc_url = (
        os.getenv("RPC_URL")
        or contract.get("rpc_url")
        or "https://base-sepolia.drpc.org"
    )
    wallet_address = None

    if env_flag("PRIVATE_KEY"):
        wallet = run_cmd(
            ["cast", "wallet", "address", "--private-key", get_env("PRIVATE_KEY")]
        )
        if wallet["ok"]:
            wallet_address = wallet["stdout"]

    return {
        "hackathon_id": hackathon_id,
        "wallet_address": wallet_address,
        "command": (
            f'cast send {escrow} "join()" --value {entry_fee} '
            f"--private-key $PRIVATE_KEY --rpc-url {rpc_url}"
        ),
        "contract": contract,
    }


def parse_job_payload(job: dict) -> dict:
    description = job.get("description", "")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("Job description must contain a JSON action payload")

    try:
        payload = json.loads(description)
    except json.JSONDecodeError as exc:
        raise ValueError("Job description must be valid JSON") from exc

    if not isinstance(payload, dict):
        raise ValueError("Job payload must be a JSON object")
    return payload


def capabilities() -> dict:
    return {
        "github": {
            "token_configured": env_flag("GITHUB_TOKEN"),
            "username_configured": env_flag("GITHUB_USERNAME"),
            "gh_installed": run_cmd(["gh", "--version"])["ok"],
        },
        "buildersclaw": {
            "api_key_configured": env_flag("BUILDERSCLAW_API_KEY"),
            "base_url": BUILDERSCLAW_BASE_URL,
        },
        "chain": {
            "private_key_configured": env_flag("PRIVATE_KEY"),
            "rpc_url_configured": env_flag("RPC_URL"),
            "cast_installed": run_cmd(["cast", "--version"])["ok"],
        },
    }


def handle_action(payload: dict) -> dict:
    action = payload.get("action")
    args = payload.get("args", {})
    if not action:
        raise ValueError("Missing action")
    if not isinstance(args, dict):
        raise ValueError("args must be an object")

    handlers = {
        "capabilities": lambda _: capabilities(),
        "github_auth_check": lambda _: github_auth_check(),
        "github_create_repo": github_create_repo,
        "github_clone_repo": github_clone_repo,
        "github_init_and_push": github_init_and_push,
        "github_add_collaborator": github_add_collaborator,
        "github_list_invitations": lambda _: github_list_invitations(),
        "github_accept_invitation": github_accept_invitation,
        "buildersclaw_register": buildersclaw_register,
        "buildersclaw_me": lambda _: buildersclaw_me(),
        "buildersclaw_list_hackathons": buildersclaw_list_hackathons,
        "buildersclaw_get_hackathon": buildersclaw_get_hackathon,
        "buildersclaw_get_contract": buildersclaw_get_contract,
        "buildersclaw_join": buildersclaw_join,
        "buildersclaw_submit_repo": buildersclaw_submit_repo,
        "buildersclaw_list_marketplace": buildersclaw_list_marketplace,
        "buildersclaw_post_role": buildersclaw_post_role,
        "buildersclaw_take_role": buildersclaw_take_role,
        "buildersclaw_claim_join_command": buildersclaw_claim_join_command,
    }

    if action not in handlers:
        raise ValueError(f"Unknown action: {action}")

    data = handlers[action](args)
    return {"ok": True, "action": action, "data": data}


def on_job(job: dict) -> str:
    try:
        payload = parse_job_payload(job)
        return json.dumps(handle_action(payload))
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


app = create_apex_app(config=config, on_job=on_job)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "buildersclaw-agent"}


@app.get("/capabilities")
def capabilities_endpoint():
    return capabilities()


@app.get("/buildersclaw/me")
def buildersclaw_me_endpoint():
    try:
        return buildersclaw_me()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/buildersclaw/hackathons")
def buildersclaw_hackathons_endpoint(status: str | None = None):
    try:
        return buildersclaw_list_hackathons({"status": status})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/actions/execute")
async def execute_action_endpoint(request: Request):
    try:
        payload = await request.json()
        return handle_action(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


agent_address = app.state.apex.job_ops.agent_address
print(f"Agent address: {agent_address}")
