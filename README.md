# mcpx

Nano-agent that turns any MCP server into a shell command.

```
mcpx <server> <tool> [ARGS] | jq .
```

No daemon. No state. Each invocation spins up the MCP server, executes a tool, prints the result to stdout, and exits. Logs and UI go to stderr — stdout is clean for piping.

## Install

```bash
uv tool install .
```

## Quick Start

```bash
# Register a server manually
mcpx add weather --command npx --args -y --args @example/weather-mcp

# Or let the agent figure it out from a URL
mcpx install https://github.com/example/weather-mcp

# List servers
mcpx list

# Browse tools
mcpx weather --help

# Call a tool
mcpx weather get_forecast --city "Berlin"

# JSON output for scripting
mcpx weather get_forecast --city "Berlin" --json

# Debug JSON-RPC traffic
mcpx weather get_forecast --city "Berlin" --debug

# Dry run (show payload only)
mcpx weather get_forecast --city "Berlin" --dry-run

# Remove a server
mcpx remove weather
```

## AI-powered install

`mcpx install` takes a URL (GitHub, npm, PyPI, mcpservers.org, etc.), fetches the repo, finds the package name, verifies it on a registry, and registers the server — all via an LLM agent.

```bash
mcpx install https://github.com/modelcontextprotocol/servers/tree/main/src/time

# Analyzing https://github.com/... with claude-sonnet-4-20250514...
#   Step 1: fetch_url(url='https://github.com/...')
#   Step 2: fetch_url(url='https://raw.githubusercontent.com/.../package.json')
#   Step 3: search_npm(package_name='@modelcontextprotocol/server-time')
#   Step 4: install_server(alias='time', command='npx', ...)
#
# Generated command: mcpx add time --command npx --args -y --args @modelcontextprotocol/server-time
# Proceed? [y/N]: y
# Server time added.
```

Configure the LLM model:

```bash
mcpx config-llm --model claude-sonnet-4-20250514
```

## Configuration

Server configs are stored in `~/.config/mcpx/config.json`:

```json
{
  "servers": {
    "time": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-time"],
      "env": {},
      "timeout": 30
    }
  }
}
```

### Environment variables priority

1. Inline `--env KEY=VAL` (highest)
2. Local `.env` in current directory
3. `env_file` from config

Env vars are masked in `--debug` output.

## Schema caching

Tool schemas are cached in `~/.config/mcpx/cache/` with a 24h TTL. Use `--refresh` to force re-fetch:

```bash
mcpx --refresh weather --help
```

## License

[Apache 2.0](LICENSE)
