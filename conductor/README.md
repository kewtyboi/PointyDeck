# conductor/

Runtime assets and helper scripts for the conductor feature (Telegram / Slack /
Discord ↔ agent-deck conductor sessions).

## Where is `bridge.py`?

The conductor bridge script has **exactly one canonical source** in the repo:

```
conductor/conductor_bridge.py
```

It is embedded into the agent-deck binary (`//go:embed`, see
`conductor/conductor_bridge_embed.go`). `agent-deck conductor setup`
(`InstallBridgeScript`) and `agent-deck update` (`update.UpdateBridgePy`)
materialize the user-facing runtime copy at:

```
<data-dir>/conductor/bridge.py        # e.g. ~/.local/share/agent-deck/conductor/bridge.py
```

So there is **no `conductor/bridge.py` checked into the repo** - editing the
embedded canonical file (`conductor/conductor_bridge.py`) is the only place to
change the bridge. Tests under `conductor/tests/` load that same canonical file
(see `conductor/tests/conftest.py`), so the tested bytes are exactly the deployed
bytes.

### Git subtree note

The `conductor/` directory is structured as a self-contained subtree boundary
so that PointyTooling can vendor the bridge via `git subtree`:

```
git subtree add --prefix=conductor/bridge \
    https://github.com/asheshgoplani/agent-deck.git main \
    --squash -- conductor/
```

All bridge source, tests, and setup scripts live under this prefix. The Go embed
plumbing (`conductor_bridge_embed.go`) is also here so that `go:embed` (which
cannot reference parent directories) can embed `conductor_bridge.py` from the
same directory.
