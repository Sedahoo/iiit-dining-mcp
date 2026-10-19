# IIIT Dining MCP

An [MCP](https://modelcontextprotocol.io) server for the **IIIT Hyderabad
Dining Portal** ([dining.iiit.ac.in](https://dining.iiit.ac.in/)). It lets
Claude browse mess menus, timings and ratings, and — with your personal auth
key — view and manage your own meal registrations, feedback, bills and balance.

## Tools

**Public (no auth key needed):**

| Tool | What it does |
|------|--------------|
| `list_messes` | All messes with id, name, tags, rating |
| `get_menu` | Menu for a day, by mess and meal |
| `get_meal_timings` | Meal start/end times per mess |

**Account (require an auth key):**

| Tool | What it does |
|------|--------------|
| `dining_whoami` | Your profile — use this to verify your key works |
| `get_preferences` | Your saved dining preferences |
| `get_registration` | Your registrations for one day |
| `list_registrations` | Your registrations over a date range |
| `get_bill` / `list_bills` | Monthly mess bill(s) |
| `get_mess_rates` / `get_mess_capacities` | Per-meal pricing / remaining capacity |
| `list_extras` | À la carte items for a meal |
| `register_meal` | **Book** a meal (writes) |
| `set_cancel_meal` | **Cancel / un-cancel** a meal (writes) |
| `set_skip_meal` | **Skip / un-skip** a meal (writes) |
| `submit_feedback` | **Submit** 1–5★ anonymous feedback (writes) |
| `book_extra` | **Book** an extra item (writes) |

> The **write** tools change your real mess account and are subject to the
> portal's registration / cancellation / feedback / skip windows.

## Setup

```bash
# install deps (any of these)
uv sync                       # or:
pip install -e .              # or:
pip install "mcp" "httpx"
```

### Get your auth key

1. Log in at <https://dining.iiit.ac.in/>.
2. Go to **Settings → Auth Keys → Create Auth key**.
3. Copy the key (you can only see it once).

### Configure Claude Desktop / Claude Code

Add to your MCP config (e.g. `claude_desktop_config.json`, or
`claude mcp add`):

```json
{
  "mcpServers": {
    "iiit-dining": {
      "command": "python",
      "args": ["-m", "iiit_dining_mcp.server"],
      "env": {
        "DINING_AUTH_KEY": "paste-your-auth-key-here"
      }
    }
  }
}
```

Run the server directly to smoke-test:

```bash
DINING_AUTH_KEY=... python -m iiit_dining_mcp.server
```

## Authentication scheme

The auth key is sent as `Authorization: <key>` by default (`raw` scheme —
confirmed working against the live portal). If a future change breaks this and
`dining_whoami` returns a **401**, set `DINING_AUTH_SCHEME` to try another
transport:

| `DINING_AUTH_SCHEME` | Sends |
|----------------------|-------|
| `raw` (default) | `Authorization: <key>` |
| `bearer` | `Authorization: Bearer <key>` |
| `token` | `Authorization: Token <key>` |
| `header:X-Api-Key` | `X-Api-Key: <key>` (any header name) |
| `cookie:session` | `Cookie: session=<key>` (any cookie name) |

After changing it, call `dining_whoami` again — once it returns your profile,
the scheme is correct.

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `DINING_AUTH_KEY` | *(empty)* | Your auth key; public tools work without it |
| `DINING_AUTH_SCHEME` | `raw` | How the key is transmitted (see above) |
| `DINING_BASE_URL` | `https://dining.iiit.ac.in` | Override the API host |

## Notes

- Dates are `YYYY-MM-DD`; meal is one of `breakfast`, `lunch`, `snacks`,
  `dinner`. Mess ids come from `list_messes` (e.g. `yuktahar`, `kadamba-veg`).
- This is an unofficial client built against the portal's public JSON API.
