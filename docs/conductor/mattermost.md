# Mattermost channel setup

Connect a Mattermost bot to your conductor for self-hosted, channel-based monitoring and control.
The bridge connects over WebSocket for inbound events and uses the REST API for outbound messages.

## What you need

- A self-hosted Mattermost instance (any recent version)
- A bot account with a personal access token (or a user token with sufficient permissions)
- The `mattermostautodriver` Python package installed (`pip install mattermostautodriver`)
- A conductor already created (`agent-deck conductor setup <name>`)

## Step-by-step setup

### 1. Create the bot account

In your Mattermost instance:

1. Create a dedicated bot account (System Console -> Integrations -> Bot Accounts -> Add Bot Account).
2. Generate a **personal access token** for the bot (or use a user account token if bot accounts are disabled).
3. Note the **team name** (the URL slug, e.g. `myteam` in `https://mattermost.example.com/myteam`).
4. Create or identify the **channel** the bot will use. Copy its **channel ID** (right-click the channel name -> Copy Link; the ID appears in the URL as the last path segment, or via the API).
5. Note the **Mattermost user ID(s)** of the people who should be allowed to send commands (System Console -> Users, or `/api/v4/users/me` while authenticated).

### 2. Configure `[conductor.mattermost]`

Add the following block to your conductor's `config.toml` (typically `~/.config/agent-deck/config.toml`):

```toml
[conductor.mattermost]
url             = "https://mattermost.example.com"   # base URL, no trailing slash
token           = "your-bot-token"                   # personal access token
team            = "myteam"                           # team URL slug
channel_id      = "abcdef1234567890"                 # channel ID (not name)
allowed_user_ids = ["user-id-1", "user-id-2"]        # Mattermost user IDs allowed to send commands
allow_all_users_for_dev = false                      # set true only for local dev/testing
```

#### Config key reference

| Key | Required | Description |
|-----|----------|-------------|
| `url` | Yes | Base URL of your Mattermost instance |
| `token` | Yes | Bot or user personal access token (Bearer auth) |
| `team` | Yes | Team URL slug |
| `channel_id` | Yes | ID of the channel the bot will monitor |
| `allowed_user_ids` | Yes (see note) | List of Mattermost user IDs authorised to send commands |
| `allow_all_users_for_dev` | No | If `true`, bypasses the allowlist check (dev only) |

### 3. Run conductor setup (or re-run it)

```bash
agent-deck conductor setup <name>
```

Answer **y** at the Mattermost prompt and provide the values above, or edit `config.toml` directly and restart.

### 4. Restart the conductor

```bash
agent-deck session restart conductor-<name>
```

### 5. Verify

Post a message in the configured channel:

```
/status
```

The bot should reply with the current fleet state.

## Authorisation and fail-closed behaviour

The Mattermost bridge is **fail-closed by default**:

- If `allowed_user_ids` is empty, **all inbound commands are refused**. The bot will still connect and send outbound notifications, but it will not act on any messages.
- If `allowed_user_ids` lists at least one user ID, only those users can send commands.
- Setting `allow_all_users_for_dev = true` bypasses the allowlist entirely. Use this only on a local or isolated instance -- never in production.

This default is intentional: a misconfigured or freshly deployed bridge should be inert rather than open to any channel member.

## One bot per conductor

Each conductor needs its own dedicated bot account or token.
Do not share a single token across multiple conductors -- the WebSocket subscription is single-consumer per connection.

## Debugging tips

### Bot connects but does not respond to commands

1. Confirm `allowed_user_ids` contains your Mattermost user ID (not your username).
2. Check that `allow_all_users_for_dev` is `true` if you are testing without an allowlist.
3. Check bridge logs: `tail -f ~/.local/share/agent-deck/conductor/bridge.log`

### WebSocket disconnects or does not reconnect

The bridge reconnects automatically after a socket close. If reconnects are failing:

1. Verify the Mattermost URL is reachable from the machine running the bridge.
2. Confirm the token is still valid (personal access tokens can expire or be revoked).
3. Check that WebSocket connections are not blocked by a reverse proxy (ensure `wss://` passes through).

### Conductor does not reply to messages

Verify the conductor session is running:

```bash
agent-deck conductor status <name>
```

Check that the channel ID is correct -- the bridge matches on the exact channel ID, not the channel name.
