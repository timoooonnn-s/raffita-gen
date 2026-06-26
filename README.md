# Raffita — VOSS / Fabric Engine Config Rollout Toolkit

**raffita** is a Python 3.9+ toolkit for generating, staging, and deploying
configuration to Extreme VOSS / Fabric Engine switches via SSH.
It provides an interactive REPL (`raffita_interpreter.py`) with live push,
parallel group deployment, host inventory with tags, and session logging.

---

## Project Structure

```
raffita-gen/
│
├── raffita_interpreter.py     # Interactive REPL — main entry point
│
├── raffita/                   # Package
│   ├── __init__.py
│   ├── colors.py              # ANSI color definitions + semantic aliases
│   ├── param_filling.py       # Parameter resolution (type, validate, prompt)
│   ├── history.py             # Persistent REPL history (age/size pruning)
│   ├── inventory.py           # YAML host/group/tag inventory loader
│   ├── backend.py             # SSH session management (netmiko wrapper)
│   ├── gen_lib.py             # Jinja2 helpers, file I/O, argparse utilities
│   └── objects.py             # Object registry (schemas, builders)
│
├── templates/                 # Jinja2 config templates (one per object type)
│   └── *.j2
│
├── staging/                   # Generated .raffita files land here
├── logs/                      # Session logs + per-session summary files
└── inventory/
    └── inventory.yaml         # Default inventory (auto-loaded at startup)
```

---

## Requirements

```bash
pip install netmiko jinja2 pyyaml
```

- Python **3.9+** (no 3.10+ syntax used)
- `netmiko` — SSH to switches
- `jinja2` — config template rendering
- `pyyaml` — inventory file parsing (only needed if you use inventory)
- `readline` — tab-completion and history (built-in on Linux/macOS; install `pyreadline3` on Windows)

---

## Quick Start

```bash
python3 raffita_interpreter.py
```

At startup the interpreter:
1. Looks for `inventory/inventory.yaml` and loads it automatically if found.
2. Starts in **dry-run mode** — builds and previews config but sends nothing until you enable live push.

```
raffita> login
  Username: admin
  Password: ****

raffita> target sw-core-01

raffita> create anycast --VLAN_ID 100 --IP_ADDRESS 10.1.0.1 \
             --SUBNET_MASK 255.255.255.0 --VRF_NAME GlobalRouter --I_SID 1000100
# Config preview shown. When satisfied:

raffita> live on
raffita> create anycast --VLAN_ID 100 ...
  Push to sw-core-01? [y/N]: y
```

---

## Inventory File

Place at `inventory/inventory.yaml` to have it loaded automatically at startup,
or load any file manually with `inventory load <path>`.

```yaml
defaults:
  save_command: "save config"
  reconnect_attempts: 3
  reconnect_delay: 5

hosts:
  sw-core-01:
    description: "Core Switch 1, Building A"
    tags: [core, building-a]
  sw-core-02:
    description: "Core Switch 2, Building A"
    tags: [core, building-a]
  sw-acc-21:
    description: "Access Switch, Floor 2"
    tags: [access, floor-2]
  sw-acc-22:
    description: "Access Switch, Floor 2"
    tags: [access, floor-2]

groups:
  core:
    - sw-core-01
    - sw-core-02
  access-floor2:
    - sw-acc-21
    - sw-acc-22
  all-switches:         # groups can reference other groups
    - "@core"
    - "@access-floor2"
```

```
raffita> inventory load                  # loads inventory/inventory.yaml (default)
raffita> inventory load /path/to/inv.yaml
raffita> inventory reload                # reload the currently loaded file
raffita> inventory show                  # full summary (hosts, groups, tags)
raffita> inventory hosts
raffita> inventory groups
raffita> inventory tags
```

### Targeting

No inventory is needed for ad-hoc targeting — bare hostnames always work:

```
raffita> target sw-core-01                     # single host
raffita> target sw-core-01 sw-core-02          # multiple hosts, no inventory needed
raffita> target sw-core-01 sw-core-02 @edge    # mix bare hosts with a group
raffita> target @core                          # inventory group → sw-core-01, sw-core-02
raffita> target @tag:access                    # all hosts tagged "access"
raffita> target none                           # clear target
```

All subsequent commands (`create`, `stage`, `ping`, `command`) automatically
run against every host in the current target set.

---

## Parallel Push (`--parallel`)

Pass `--parallel` to `create`, `deploy`, or `command` to push to all targets
concurrently. SSH runs in parallel threads; output is buffered per host and
printed in order with a color-coded header per host after all complete.

```
raffita> target @core
raffita> create --parallel anycast --VLAN_ID 100 ...
  Push to 2 hosts (sw-core-01, sw-core-02)? [y/N]: y
  ⇶  parallel push → 2 hosts
  ── sw-core-01 ──────────────────────────────────
  ...
  ── sw-core-02 ──────────────────────────────────
  ...
```

Stop on the first error:

```
raffita> halt on
```

---

## Per-Command Live / Dry Override

Override the global live/dry-run mode for a single command without changing the setting:

```
raffita> create --live anycast --VLAN_ID 100 ...   # push live even if dryrun is on
raffita> deploy --dry staging/sw-core-01.raffita   # preview even if live is on
raffita> command --dry --CMD "show isis spbm"      # dry-run a single command
```

---

## Exec-Mode Commands

Send arbitrary exec-mode (non-config) commands to one or more targets:

```
raffita> command --CMD "show run"
raffita> command --CMD "show isis spbm" --HOSTNAME sw-core-01
raffita> target @core
raffita> command --CMD "show virtual-ist"
raffita> command --parallel --CMD "show sys-info"   # runs on all targets concurrently
```

### Repeating commands (`--repeat`)

Pass `--repeat <seconds>` to run the same command on a fixed interval:

```
raffita> command --repeat 30 --CMD "show virtual-ist"
raffita> command --repeat 60 --CMD "show isis adjacency"
```

- **Ctrl-C during a push or confirm prompt** aborts that single iteration — the repeat loop keeps running.
- **Ctrl-C during the sleep between iterations** stops the entire repeat loop.

---

## Session / Connection Management

```
raffita> connect sw-core-01                  # connect + set as active target
raffita> connect sw-core-01 sw-core-02       # multiple hosts, no inventory needed
raffita> connect @core                       # connect a whole group + set as target
raffita> disconnect sw-core-01               # close one session
raffita> disconnect all                      # close all sessions
raffita> reconnect [host|--all]              # reconnect dropped session(s)
raffita> targets                             # list open sessions + active marker
raffita> sessions                            # alias for targets
```

- `connect` sets all connected hosts as the active target automatically.
- Multiple bare hostnames work without an inventory — `connect sw-core-01 sw-core-02`.
- Failed connection attempts are cleaned up immediately — no dead sessions left open.
- Ctrl-C during a slow or hanging connect aborts that one connection without exiting the REPL.
- Before every SSH connect, TCP port 22 is checked first for a fast failure on unreachable hosts.
- Auto-reconnect retries up to `reconnect_attempts` times (default: 3) with
  `reconnect_delay` seconds between attempts (default: 5).
- After every successful config push, the session automatically exits config mode
  (`end`) and saves (`save config` by default, overridable per-host in inventory).

### Prompt indicators

The prompt reflects live session state at a glance:

```
raffita[DRY]>                           # no target, dry-run mode
raffita(sw-core-01)[LIVE]>              # target set, live mode
raffita(@core)[DRY]>                    # group target, dry-run
```

| Prompt part | Meaning |
|---|---|
| `[LIVE]` (orange) | Live push enabled — commands will be sent to switches |
| `[DRY]` (dim) | Dry-run mode — config is previewed but nothing is sent |
| Target color: green | All resolved hosts are connected |
| Target color: orange | Partially connected |
| Target color: gray | Not connected |

Running `clear` reprints a compact status bar at the top of the fresh screen so you
always know your mode, target, and open sessions after clearing.

---

## Staging and Batch Deploy

```
raffita> stage anycast --HOSTNAME sw-core-01 --VLAN_ID 100 ...
# Config written to staging/sw-core-01.raffita

# Review / edit the file, then deploy:
raffita> deploy                        # all files in staging/
raffita> deploy staging/sw-core-01.raffita   # specific file
raffita> deploy --parallel             # deploy all staging files in parallel
```

---

## .raffita File Format

Plain VOSS CLI commands, one per line:

```
# Comment — skipped during execution
vlan create 100 name C010001000000_24
interface vlan 100
  ip address 10.1.0.1 255.255.255.0
  ip vrf forwarding GlobalRouter
router isis
  i-sid 1000100 vlan 100
```

- Lines starting with `#` are comments.
- Empty lines are skipped.
- `(y/n)` prompts are answered automatically with `y`.

---

## All Commands

### Config

| Command | Description |
|---|---|
| `create [--live\|--dry] [--parallel] <obj> [--PARAM v]` | Build config and push (or preview in dry-run) |
| `stage <obj> [--PARAM value ...]` | Build config and write to `staging/<host>.raffita` |
| `deploy [--live\|--dry] [--parallel] [file.raffita ...]` | Push `.raffita` files from `staging/` (or named) |
| `command [--live\|--dry] [--parallel] --CMD "..." [--HOSTNAME h] [--repeat <s>]` | Send arbitrary exec-mode command(s) |

### Session

| Command | Description |
|---|---|
| `target <host> [host ...] [@group]` | Set default target(s); bare hostnames work without inventory; `none` to clear |
| `connect [host] [host ...] [@group]` | Open SSH connection(s) and set as active target |
| `disconnect [host\|@group\|all] ...` | Close connection(s); `all` closes every session |
| `reconnect [host\|@group\|--all]` | Reconnect dropped session(s) |
| `ping [host\|@group] ...` | TCP:22 reachability check |
| `targets` / `sessions` | List open sessions with active marker |

### Credentials

| Command | Description |
|---|---|
| `login` | Set username and password for this session |

### Inventory

| Command | Description |
|---|---|
| `inventory load [file.yaml]` | Load YAML inventory (default: `inventory/inventory.yaml`) |
| `inventory reload` | Reload the currently loaded inventory file |
| `inventory show` | Print full inventory summary |
| `inventory hosts` | List all hosts |
| `inventory groups` | List all groups with members |
| `inventory tags` | List all tags with member hosts |

### Settings

| Command | Description |
|---|---|
| `live on\|off` | Enable / disable live push (`off` = dry-run) |
| `dryrun on\|off` | Same as `live` (inverted) |
| `confirm on\|off` | Ask before each push (default: on) |
| `halt on\|off` | Stop sequence on first push error (default: off) |
| `clear` | Clear screen and reprint compact status bar |
| `status` | Show current settings and sessions |
| `objects` | List all available object types |
| `man <verb>` | Detailed usage notes and examples for a command |
| `help <obj>` | Parameter reference for an object type |
| `exit` / `quit` | Exit (writes session summary to `logs/`) |

---

## Available Object Types

| Object | Description |
|---|---|
| `anycast` | Anycast-Gateway (one-ip) L3 VLAN interface |
| `dvr` | DVR one-IP gateway L3 VLAN interface |
| `rsmlt` | RSMLT L3 VLAN interface |
| `vrrp` | VRRP L3 VLAN interface |
| `loopback` | Circuitless IP / loopback interface |
| `mlt` | MultiLink Trunk + interface |
| `port` | Single interface |
| `isid` | I-SID on port/MLT (L2 service) |
| `route` | Static route in VRF |
| `vrf` | VRF with IPVPN |
| `vrf_multiarea` | VRF multi-area redistribution |
| `cluster` | vIST cluster — generates config for **two nodes** |
| `ntp` | NTP server configuration (stub — fill in template) |
| `snmp` | SNMP community / trap configuration (stub — fill in template) |
| `spbm` | Basic IS-IS / SPBM node configuration (stub — fill in template) |

Use `help <obj>` to see all parameters, types, defaults, and validation rules.

---

## Parameter Validation

Every schema field with a sensible range carries a `validate` rule.
Error messages include the value that was rejected and the valid range:

```
raffita> create anycast --VLAN_ID 9999 ...
  ✖  [sw-core-01] parameter errors:
  ✖  --VLAN_ID: '9999' — VLAN_ID must be 1-4094.
```

Validated fields include VLAN_ID (1-4094), I_SID (256-16 777 214), VRF_ID (1-511),
VRRP_ID (1-255), PRIORITY (1-254), ECMP_MAX_PATH (1-8), IP addresses, subnet masks,
and IST_NETWORK (/30 only).

---

## Two-Node Staging (cluster)

The `cluster` object generates separate configs for **both** nodes of a vIST pair:

```
raffita> stage cluster --NICKNAME 7.39.30 --IST_NETWORK 10.34.0.68/30 \
             --NODENAME1 sw-core-01 --NODENAME2 sw-core-02
  ✎  staged   → staging/sw-core-01.raffita
  ✎  staged   → staging/sw-core-02.raffita
```

Each node gets its own `.raffita` file with the correct IST IPs, nicknames, and
SMLT peer system-IDs derived automatically from the /30 IST network.

---

## Adding a New Object Type

1. Add `SCHEMA_<NAME>` to `raffita/objects.py` with `validate`/`validate_msg` on range fields.
2. Write `build_<name>(params)` calling `render_template()`.
3. Add the entry to `OBJECTS` at the bottom of `raffita/objects.py`.
4. Create the matching Jinja2 template at `templates/<name>_template.j2`.

No changes needed in the interpreter — it reads everything from `OBJECTS`.

---

## Tab Completion

The REPL has context-aware tab completion throughout:

- **Verbs** — Tab on an empty line completes command names
- **Object names** — `create <Tab>` lists all object types
- **Parameters** — `create vrf <Tab>` or `create vrf --<Tab>` lists all `--PARAM` flags;
  double-Tab shows them grouped by **required** and **optional** with help text and defaults
- **Hosts** — `target <Tab>`, `connect <Tab>`, `ping <Tab>` suggest inventory hosts,
  groups, tags, and any currently open sessions (even without an inventory loaded);
  hosts already on the line are excluded from suggestions
- **Subcommands** — `inventory <Tab>` completes sub-commands
- **`man <Tab>`** — completes verb names for manpages
- **`help <Tab>`** — completes object type names

A unique match inserts with a trailing space so you can keep typing immediately.

---

## Logging

All actions are logged to `logs/raffita.log`:

- Every command sent to a switch
- Full switch output (unfiltered)
- Connect / disconnect / reconnect events
- DRY-RUN entries (what *would* have been sent)
- Errors and warnings

The console output is filtered for readability; the log contains the raw
unfiltered switch responses.

Per-session summary files are written to `logs/summary_YYYYMMDD_HHMMSS.log` on exit.
