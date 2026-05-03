# LaunchAgent Setup (MSG-SILENT-DEATH-54 Layer 1)

Installs a macOS LaunchAgent that auto-spawns the invasion bot on boot and
auto-restarts it on crash (or on `os._exit(3)` from the Layer 3 watchdog).
Replaces the manual `nohup python3 -m invasion` workflow.

## Prerequisites

- macOS with `launchctl` available.
- Bot repo at `/Users/jinyoon/Projects/auto_invasion_mk1-main` (plist assumes
  this absolute path — edit `WorkingDirectory` in `scripts/invasion_watchdog.plist`
  if the repo lives elsewhere).
- Python 3 on the PATH (`/opt/homebrew/bin` is pre-seeded in the plist).

## Install

```bash
cp scripts/invasion_watchdog.plist ~/Library/LaunchAgents/com.invasion.bot.plist
launchctl load ~/Library/LaunchAgents/com.invasion.bot.plist
```

After `load`, the bot is now managed — `KeepAlive` restarts it whenever
it exits (unless you explicitly `unload`).

## Verify

```bash
# Bot should be spawned within 10s
sleep 12; pgrep -f "[Pp]ython.*-m invasion"

# LaunchAgent status
launchctl list | grep com.invasion.bot
```

## Kill test (confirm auto-restart)

```bash
kill -9 $(pgrep -f "[Pp]ython.*-m invasion" | head -1)
sleep 15; pgrep -f "[Pp]ython.*-m invasion"   # new PID, bot restarted
```

## Uninstall / pause

```bash
launchctl unload ~/Library/LaunchAgents/com.invasion.bot.plist
# Optional — remove the plist file entirely
rm ~/Library/LaunchAgents/com.invasion.bot.plist
```

## Conflict with manual nohup

If you previously ran `nohup python3 -m invasion --headless &`, kill the
manual process first to avoid two bots writing to the same state:

```bash
pgrep -f "[Pp]ython.*-m invasion" | xargs kill
```

Then reload the LaunchAgent. Do not run both at once — portfolio_state.json
cross-writes corrupt SSOT.

## Related layers

- Layer 2 (signal handler / atexit): always active in `invasion/main.py`.
- Layer 3 (watchdog_thread): 180s log-stall self-restart via `os._exit(3)`.
  LaunchAgent KeepAlive then respawns.
- Layer 4 (heartbeat HEALTH): `psutil` RSS/FD log every 5min if installed.

## Log locations

- Bot stdout/stderr: `data/launchagent.stdout.log` / `data/launchagent.stderr.log`
- Bot main log: `data/invasion.log`
- Watchdog stall marker: `data/bot_stall_detected.flag`
