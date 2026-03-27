import json
import urllib.request

AGENT_URL = "http://127.0.0.1:8000"


def execute_action(
    action: str, args: dict | None = None, agent_url: str = AGENT_URL
) -> dict:
    payload = json.dumps({"action": action, "args": args or {}}).encode("utf-8")
    request = urllib.request.Request(
        f"{agent_url}/actions/execute",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def print_action(action: str, args: dict | None = None) -> None:
    print(f"\n=== {action} ===")
    result = execute_action(action, args)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    print_action("capabilities")
    print_action("github_auth_check")
    print_action("buildersclaw_me")
    print_action("buildersclaw_list_hackathons", {"status": "open"})
