import ipaddress
import json
import socket
from typing import Any, Callable
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from mcpx.agent import ToolDef, agent_loop

SYSTEM_PROMPT = """\
You are an expert at configuring MCP (Model Context Protocol) servers.
You receive a URL and have tools to investigate it and install the server.

Strategy:
1. Fetch the given URL to understand what MCP server it describes.
2. Find the GitHub repo owner/name. Fetch the RAW package manifest:
   - Node: https://raw.githubusercontent.com/<owner>/<repo>/HEAD/package.json
   - Python: https://raw.githubusercontent.com/<owner>/<repo>/HEAD/pyproject.toml
   Read the exact "name" field from the manifest.
3. Search for that exact name on npm (search_npm) or PyPI (search_pypi).
   If not found, also try common variations (scoped name @owner/name, with -mcp suffix).
4. Install based on what you found:

   a) Published on npm:  command="npx", args=["-y", "<exact-npm-name>"]
   b) Published on PyPI: command="uvx", args=["<exact-pypi-name>"]
   c) NOT on any registry, Python repo with [project.scripts] entry point:
      command="uvx", args=["--from", "git+https://github.com/<owner>/<repo>", "<entry-point>"]

5. Only respond with text (no install_server) if the package truly cannot be \
installed via npx or uvx (no entry point, Docker-only, requires build, etc.).

IMPORTANT: `npx github:<owner>/<repo>` is unreliable — it fails for TypeScript \
repos that need compilation. NEVER use it. Always prefer registry-published packages \
(strategy 4a/4b) or uvx --from git+ for Python (4c).

Rules:
- command MUST be `npx` or `uvx`. Never use node, python, or absolute paths.
- For npx, always include `-y` as the first arg.
- alias should be short, lowercase, descriptive (e.g. "time", "github", "slack").
- env_vars: list only the NAMES of required environment variables, not values.
"""

MAX_TEXT_LENGTH = 8000
ALLOWED_COMMANDS = {"npx", "uvx"}


def _validate_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Blocked URL scheme: {parsed.scheme!r}. Only http/https allowed."
    hostname = parsed.hostname or ""
    if not hostname:
        return "Missing hostname in URL."
    try:
        addrinfos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return f"Cannot resolve hostname: {hostname}"
    for _, _, _, _, sockaddr in addrinfos:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return f"Blocked: {hostname} resolves to private/loopback IP {ip}"
    return None


class InstallSpec(BaseModel):
    alias: str
    command: str
    args: list[str] = Field(default_factory=list)
    env_vars: list[str] = Field(default_factory=list)
    notes: str = ""


def _validate_install(args: dict) -> InstallSpec:
    if args.get("command") not in ALLOWED_COMMANDS:
        raise ValueError(f"Invalid command {args.get('command')!r}. Allowed: {ALLOWED_COMMANDS}")
    return InstallSpec(**args)


def _build_tools(http: httpx.Client) -> list[ToolDef]:
    def exec_fetch_url(args: dict[str, Any]) -> str:
        url = args["url"]
        err = _validate_url(url)
        if err:
            return err
        try:
            resp = http.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            return f"Error fetching URL: {e}"
        ct = resp.headers.get("content-type", "")
        if "html" in ct:
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
        else:
            text = resp.text
        return text[:MAX_TEXT_LENGTH]

    def exec_search_npm(args: dict[str, Any]) -> str:
        package_name = args["package_name"]
        try:
            resp = http.get(f"https://registry.npmjs.org/{package_name}")
            if resp.status_code == 404:
                return f"Package '{package_name}' not found on npm."
            resp.raise_for_status()
            data = resp.json()
            latest = data.get("dist-tags", {}).get("latest", "unknown")
            version_data = data.get("versions", {}).get(latest, {})
            return json.dumps({
                "name": data.get("name"),
                "version": latest,
                "description": data.get("description", ""),
                "bin": version_data.get("bin", {}),
            })
        except Exception as e:
            return f"Error searching npm: {e}"

    def exec_search_pypi(args: dict[str, Any]) -> str:
        package_name = args["package_name"]
        try:
            resp = http.get(f"https://pypi.org/pypi/{package_name}/json")
            if resp.status_code == 404:
                return f"Package '{package_name}' not found on PyPI."
            resp.raise_for_status()
            info = resp.json().get("info", {})
            return json.dumps({
                "name": info.get("name"),
                "version": info.get("version"),
                "summary": info.get("summary", ""),
            })
        except Exception as e:
            return f"Error searching PyPI: {e}"

    return [
        ToolDef(
            name="fetch_url",
            description="Fetch a web page and return its text content (HTML stripped to text). Use for GitHub pages, READMEs, package.json, etc.",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string", "description": "URL to fetch"}},
                "required": ["url"],
            },
            execute=exec_fetch_url,
        ),
        ToolDef(
            name="search_npm",
            description="Check if an npm package exists. Returns name, version, description, and bin entries.",
            parameters={
                "type": "object",
                "properties": {"package_name": {"type": "string", "description": "npm package name"}},
                "required": ["package_name"],
            },
            execute=exec_search_npm,
        ),
        ToolDef(
            name="search_pypi",
            description="Check if a Python package exists on PyPI. Returns name, version, and summary.",
            parameters={
                "type": "object",
                "properties": {"package_name": {"type": "string", "description": "PyPI package name"}},
                "required": ["package_name"],
            },
            execute=exec_search_pypi,
        ),
        ToolDef(
            name="install_server",
            description="Install the MCP server. Only call this after verifying the package exists.",
            parameters={
                "type": "object",
                "properties": {
                    "alias": {"type": "string", "description": "Short lowercase alias for the server"},
                    "command": {"type": "string", "description": "Command to run: 'npx' or 'uvx'"},
                    "args": {"type": "array", "items": {"type": "string"}, "description": "Command arguments"},
                    "env_vars": {"type": "array", "items": {"type": "string"}, "description": "Required environment variable names"},
                    "notes": {"type": "string", "description": "Any notes for the user"},
                },
                "required": ["alias", "command", "args"],
            },
        ),
    ]


def run_agent(
    model: str,
    url: str,
    on_step: Callable[[int, str, dict], None] | None = None,
    on_text: Callable[[str], None] | None = None,
    http_client: httpx.Client | None = None,
) -> InstallSpec | None:
    http = http_client or httpx.Client(follow_redirects=True, timeout=15)
    try:
        tools = _build_tools(http)
        result = agent_loop(
            model=model,
            system_prompt=SYSTEM_PROMPT,
            user_message=f"Install MCP server from this URL: {url}",
            tools=tools,
            terminal_tool="install_server",
            max_steps=10,
            on_step=on_step,
            on_text=on_text,
        )
    finally:
        if http_client is None:
            http.close()
    if result is None:
        return None
    return _validate_install(result)
