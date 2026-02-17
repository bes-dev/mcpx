import json
import shlex
import sys
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from mcpx import __version__
from mcpx.bridge import MCPClient, ServerError, run_with_client
from mcpx.cache import invalidate_cache, load_cached_tools, save_tools_cache
from mcpx.config import ConfigManager, LLMConfig, ServerConfig
from mcpx.installer import InstallSpec, run_agent
from mcpx.schema import build_click_params, validate_args

err_console = Console(stderr=True)


# --- Static commands ---


@click.command()
@click.argument("alias")
@click.option("--command", "cmd", required=True, help="Server command to run.")
@click.option("--args", "args_", multiple=True, help="Arguments for the command.")
@click.option("--env", "envs", multiple=True, help="Environment variable KEY=VAL.")
@click.option("--env-file", default=None, help="Path to .env file.")
@click.option("--timeout", default=30, type=int, help="Connection timeout in seconds.")
def add(alias: str, cmd: str, args_: tuple[str, ...], envs: tuple[str, ...], env_file: str | None, timeout: int) -> None:
    """Add an MCP server configuration."""
    env = {}
    for e in envs:
        if "=" not in e:
            raise click.ClickException(f"Invalid env format: {e!r}. Use KEY=VAL.")
        k, v = e.split("=", 1)
        env[k] = v
    server = ServerConfig(
        command=cmd,
        args=list(args_),
        env=env,
        env_file=env_file,
        timeout=timeout,
    )
    cm = ConfigManager()
    cm.add_server(alias, server)
    err_console.print(f"Server [bold]{alias}[/bold] added.")


@click.command(name="list")
def list_servers() -> None:
    """List configured MCP servers."""
    cm = ConfigManager()
    servers = cm.list_servers()
    if not servers:
        err_console.print("No servers configured.")
        return
    table = Table(title="MCP Servers")
    table.add_column("Alias", style="bold")
    table.add_column("Command")
    table.add_column("Args")
    table.add_column("Env")
    table.add_column("Timeout")
    for alias, srv in servers.items():
        env_keys = ", ".join(srv.env) if srv.env else ""
        table.add_row(alias, srv.command, " ".join(srv.args), env_keys, str(srv.timeout))
    err_console.print(table)


@click.command()
@click.argument("alias")
def remove(alias: str) -> None:
    """Remove an MCP server configuration."""
    cm = ConfigManager()
    if cm.remove_server(alias):
        invalidate_cache(alias)
        err_console.print(f"Server [bold]{alias}[/bold] removed.")
    else:
        raise click.ClickException(f"Server {alias!r} not found.")


@click.command(name="config-llm")
@click.option("--model", required=True, help="LLM model name (e.g. claude-sonnet-4-20250514).")
def config_llm(model: str) -> None:
    """Configure the LLM model for install command."""
    cm = ConfigManager()
    cm.set_llm(LLMConfig(model=model))
    err_console.print(f"LLM model set to [bold]{model}[/bold].")


def _format_add_command(spec: InstallSpec) -> str:
    parts = ["mcpx", "add", spec.alias, "--command", spec.command]
    for a in spec.args:
        parts.extend(["--args", a])
    return shlex.join(parts)


@click.command()
@click.argument("url")
@click.option("--alias", default=None, help="Override the server alias.")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt.")
def install(url: str, alias: str | None, yes: bool) -> None:
    """Install an MCP server from a URL (GitHub, PyPI, npm, etc.)."""
    cm = ConfigManager()
    model = cm.config.llm.model
    err_console.print(f"Analyzing [bold]{url}[/bold] with [bold]{model}[/bold]...")
    try:
        spec = run_agent(
            model,
            url,
            on_step=lambda step, name, args: err_console.print(
                f"  Step {step}: {name}({', '.join(f'{k}={v!r}' for k, v in args.items())})"
            ),
            on_text=lambda text: err_console.print(text),
        )
    except Exception as e:
        raise click.ClickException(f"Agent error: {e}")

    if spec is None:
        raise SystemExit(1)

    if alias:
        spec.alias = alias

    err_console.print(f"\n[bold]Generated command:[/bold] {_format_add_command(spec)}")
    if spec.env_vars:
        err_console.print(f"[yellow]Required env vars:[/yellow] {', '.join(spec.env_vars)}")
    if spec.notes:
        err_console.print(f"[dim]Notes: {spec.notes}[/dim]")

    if not yes:
        if not click.confirm("\nProceed?"):
            raise SystemExit(0)

    env: dict[str, str] = {}
    if not yes:
        for var in spec.env_vars:
            value = click.prompt(f"  {var}", default="", show_default=False)
            if value:
                env[var] = value

    server = ServerConfig(command=spec.command, args=spec.args, env=env)
    cm.add_server(spec.alias, server)
    err_console.print(f"\nServer [bold]{spec.alias}[/bold] added.")


# --- Dynamic server/tool dispatch ---


def _mask_env(env: dict[str, str]) -> dict[str, str]:
    return {k: "***" for k in env}


def _fetch_tools_cached(alias: str, server: ServerConfig, cm: ConfigManager, refresh: bool) -> list[dict[str, Any]]:
    if not refresh:
        cached = load_cached_tools(alias)
        if cached is not None:
            return cached

    async def _fetch(client: MCPClient) -> list[dict[str, Any]]:
        tools = await client.list_tools()
        return [t.model_dump(mode="json") for t in tools]

    try:
        tools_data = run_with_client(server, cm, _fetch)
    except ServerError as e:
        raise click.ClickException(str(e))
    save_tools_cache(alias, tools_data)
    return tools_data


def _show_tools_help(alias: str, tools: list[dict[str, Any]]) -> None:
    table = Table(title=f"Tools for [bold]{alias}[/bold]")
    table.add_column("Tool", style="bold cyan")
    table.add_column("Description")
    for t in tools:
        table.add_row(t["name"], t.get("description", ""))
    err_console.print(table)


class ToolCommand(click.Command):
    """A dynamically-generated command for a single MCP tool."""

    def __init__(self, alias: str, tool_data: dict[str, Any], cm: ConfigManager, **kwargs: Any) -> None:
        self.alias = alias
        self.tool_data = tool_data
        self.cm = cm
        schema = tool_data.get("inputSchema", {})
        params = build_click_params(schema)
        params.append(click.Option(["--json", "as_json"], is_flag=True, default=False, help="Output raw JSON result."))
        params.append(click.Option(["--debug"], is_flag=True, default=False, help="Show JSON-RPC debug info on stderr."))
        params.append(click.Option(["--dry-run"], is_flag=True, default=False, help="Show payload without executing."))
        super().__init__(
            name=tool_data["name"],
            help=tool_data.get("description", ""),
            params=params,
            callback=self._execute,
            **kwargs,
        )

    def _execute(self, as_json: bool, debug: bool, dry_run: bool, **kwargs: Any) -> None:
        schema = self.tool_data.get("inputSchema", {})
        args = {k: v for k, v in kwargs.items() if v is not None}
        validate_args(schema, args)

        if dry_run:
            payload = {"tool": self.tool_data["name"], "arguments": args}
            err_console.print_json(json.dumps(payload))
            return

        server = self.cm.get_server(self.alias)
        if not server:
            raise click.ClickException(f"Server {self.alias!r} not found.")

        if debug:
            err_console.print(f"[dim]Server: {server.command} {' '.join(server.args)}[/dim]")
            err_console.print(f"[dim]Env: {_mask_env(self.cm.resolve_env(server))}[/dim]")
            err_console.print(f"[dim]Request: tool={self.tool_data['name']}, args={args}[/dim]")

        async def _call(client: MCPClient) -> None:
            result = await client.call_tool(self.tool_data["name"], args)
            if debug:
                err_console.print(f"[dim]Response: isError={result.isError}, content_count={len(result.content)}[/dim]")
            if as_json:
                print(json.dumps(result.model_dump(mode="json"), indent=2))
            else:
                for block in result.content:
                    if hasattr(block, "text"):
                        print(block.text)
                    else:
                        print(json.dumps(block.model_dump(mode="json"), indent=2))
            if result.isError:
                sys.exit(1)

        try:
            run_with_client(server, self.cm, _call)
        except ServerError as e:
            if "Method not found" in str(e):
                invalidate_cache(self.alias)
            raise click.ClickException(str(e))


class ServerGroup(click.MultiCommand):
    """Dynamic command group for a configured server — lists its tools as subcommands."""

    def __init__(self, alias: str, server: ServerConfig, cm: ConfigManager, **kwargs: Any) -> None:
        super().__init__(name=alias, help=f"Tools for server '{alias}'", **kwargs)
        self.alias = alias
        self.server = server
        self.cm = cm
        self._tools: list[dict[str, Any]] | None = None

    @staticmethod
    def _is_refresh(ctx: click.Context) -> bool:
        parent = ctx.parent
        return bool(parent and parent.params.get("refresh", False))

    def _load_tools(self, refresh: bool = False) -> list[dict[str, Any]]:
        if self._tools is None or refresh:
            self._tools = _fetch_tools_cached(self.alias, self.server, self.cm, refresh)
        return self._tools

    def list_commands(self, ctx: click.Context) -> list[str]:
        tools = self._load_tools(refresh=self._is_refresh(ctx))
        return [t["name"] for t in tools]

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        tools = self._load_tools(refresh=self._is_refresh(ctx))
        for t in tools:
            if t["name"] == cmd_name:
                return ToolCommand(self.alias, t, self.cm)
        return None

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        tools = self._load_tools(refresh=self._is_refresh(ctx))
        _show_tools_help(self.alias, tools)


class McpxCLI(click.MultiCommand):
    """Root CLI that dispatches to static commands or dynamic server groups."""

    STATIC_COMMANDS = {"add": add, "list": list_servers, "remove": remove, "install": install, "config-llm": config_llm}

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cm = ConfigManager()

    def list_commands(self, ctx: click.Context) -> list[str]:
        cmds = list(self.STATIC_COMMANDS.keys())
        cmds.extend(sorted(self.cm.list_servers().keys()))
        return cmds

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        if cmd_name in self.STATIC_COMMANDS:
            return self.STATIC_COMMANDS[cmd_name]
        server = self.cm.get_server(cmd_name)
        if server:
            return ServerGroup(cmd_name, server, self.cm)
        return None


@click.command(cls=McpxCLI, invoke_without_command=True)
@click.version_option(__version__, prog_name="mcpx")
@click.option("--refresh", is_flag=True, default=False, help="Refresh tool schema cache.")
@click.pass_context
def main(ctx: click.Context, refresh: bool) -> None:
    """mcpx — A stateless CLI proxy for MCP servers."""
    ctx.ensure_object(dict)
    ctx.obj["refresh"] = refresh
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help(), err=True)
