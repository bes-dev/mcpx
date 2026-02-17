# mcpx

A stateless CLI proxy that maps shell commands to [MCP](https://modelcontextprotocol.io/) JSON-RPC calls over stdio.

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
# Register a server
mcpx add weather --command uv --args run --args weather_server.py --env API_KEY=sk-xxx

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

## Configuration

Server configs are stored in `~/.config/mcpx/config.json`:

```json
{
  "servers": {
    "weather": {
      "command": "uv",
      "args": ["run", "weather_server.py"],
      "env": {"API_KEY": "..."},
      "env_file": "/path/to/.env",
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
