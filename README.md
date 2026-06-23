# Raffita — VOSS / Fabric Engine Config Rollout Toolkit

**raffita** is a Python 3.9+ toolkit for generating, staging, and deploying
configuration to Extreme VOSS / Fabric Engine switches via SSH.
It provides an interactive REPL (`raffita_interpreter.py`) with live push,
parallel group deployment, rollback, host inventory with tags, and session logging.

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
│   ├── rollback.py            # Pre-state capture + per-host rollback stacks
│   ├── backend.py             # SSH session management (netmiko wrapper)
│   ├── gen_lib.py             # Jinja2 helpers, file I/O, argparse utilities
│   └── objects.py             # Object registry (schemas, builders, delete/show)
│
├── templates/                 # Jinja2 config templates (one per object type)
│   └── *.j2
│
├── staging/                   # Generated .raffita files land here
├── logs/                      # Session logs + per-session summary files
│
└── inventory.yaml             # Your host/group/tag inventory (create this yourself)
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

The interpreter starts in **dry-run mode** — it builds and previews config but
sends nothing until you enable live push.

```
raffita> login
  Username: admin
  Password: ****

raffita> inventory load inventory.yaml

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

Load and inspect:

```
raffita> inventory load inventory.yaml
raffita> inventory show          # full summary (hosts, groups, tags)
raffita> inventory hosts         # list all hosts
raffita> inventory groups        # list groups with members
raffita> inventory tags          # list tags with member hosts
```

### Targeting

```
raffita> target sw-core-01           # single host
raffita> target @core                # group → sw-core-01, sw-core-02
raffita> target @tag:access          # all hosts tagged "access"
raffita> target sw-core-01 sw-acc-21 # explicit multi-host
raffita> target none                 # clear target
```

---

## Multi-Target and Parallel Push

When multiple targets are set, `create` and `deploy` iterate over all of them.

Enable parallel push to send to all targets concurrently (SSH in parallel threads,
output buffered per host and printed in order after all complete):

```
raffita> parallel on
raffita> target @core
raffita> create anycast --VLAN_ID 100 ...
  Push to 2 hosts (sw-core-01, sw-core-02)? [y/N]: y
  ⇶  parallel push → 2 hosts
```

Stop on the first error with:

```
raffita> halt on
```

---

## Rollback

Every live `create` push automatically:
1. Captures a pre-deployment snapshot (`show` commands).
2. Registers an undo entry on a per-host LIFO stack (max 20 entries per host).

```
raffita> rollback              # undo last push on active target
raffita> rollback all          # undo all pushes on active target
raffita> rollback list         # show rollback stack (all hosts)
raffita> rollback prestate     # show pre-push snapshot for active target
raffita> rollback clear        # clear stack for active target
raffita> rollback clear --all  # clear all stacks
```

The stack is session-scoped (not persisted to disk).

---

## Pre-Flight Check

Before opening an SSH connection, the toolkit verifies TCP port 22 is reachable.
If the host is down or firewalled, you get a clear error immediately rather than
waiting for netmiko's full timeout.

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

## Exec-Mode Commands

Send arbitrary exec-mode (non-config) commands to one or more targets:

```
raffita> command --CMD show run
raffita> command --CMD show isis spbm --HOSTNAME sw-core-01
raffita> target @core
raffita> command --CMD show virtual-ist
```

Multi-word commands work correctly — everything after `--CMD` until the next
`--` flag is treated as a single command string.

---

## Session / Connection Management

```
raffita> connect [host]             # open SSH connection
raffita> disconnect [host]          # close connection(s)
raffita> reconnect [host|--all]     # reconnect dropped session(s)
raffita> targets                    # list sessions + rollback depth + active marker
raffita> sessions                   # alias for targets
```

After every successful config push, the session automatically exits config mode
(`end`) and saves (`save config` by default, overridable per-host in inventory).

Auto-reconnect retries up to `reconnect_attempts` times (default: 3) with
`reconnect_delay` seconds between attempts (default: 5).

---

## Session Summary Log

On exit, a human-readable summary is written to `logs/summary_YYYYMMDD_HHMMSS.log`
listing every push action with time, host, action type, status, and command count.

---

## Staging and Batch Deploy

```
raffita> stage anycast --HOSTNAME sw-core-01 --VLAN_ID 100 ...
# Config written to staging/sw-core-01.raffita

# Review / edit the file, then deploy:
raffita> deploy                        # all files in staging/
raffita> deploy staging/sw-core-01.raffita   # specific file
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
| `create <obj> [--PARAM value ...]` | Build config and push live (or preview in dry-run) |
| `stage  <obj> [--PARAM value ...]` | Build config and write to `staging/<host>.raffita` |
| `deploy [file.raffita ...]` | Push `.raffita` files from `staging/` (or named) |
| `command --CMD "..." [--HOSTNAME h]` | Send arbitrary exec-mode command(s) |

### Session

| Command | Description |
|---|---|
| `target <host\|@group\|@tag:X> ...` | Set default target(s); `none` to clear |
| `connect [host]` | Open SSH connection |
| `disconnect [host]` | Close connection(s) |
| `reconnect [host\|--all]` | Reconnect dropped session(s) |
| `targets` / `sessions` | List open sessions with rollback depth |
| `login` | Set username and password |

### Rollback

| Command | Description |
|---|---|
| `rollback [last]` | Undo last deployment on active target |
| `rollback all` | Undo all deployments on active target |
| `rollback list` | Show all queued entries |
| `rollback prestate` | Show pre-deployment snapshot |
| `rollback clear [--all]` | Clear rollback stack |

### Inventory

| Command | Description |
|---|---|
| `inventory load <file.yaml>` | Load YAML inventory |
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
| `parallel on\|off` | Push to all targets concurrently (default: off) |
| `halt on\|off` | Stop sequence on first push error (default: off) |
| `status` | Show current settings |
| `objects` | List all available object types |
| `help [obj]` | General help or object parameter reference |
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

Every schema field with a sensible range carries a `validate` rule checked
before config is built or sent:

```
raffita> create anycast --VLAN_ID 9999 ...
  ✖  VLAN_ID must be 1-4094.
```

Validated fields include VLAN_ID (1-4094), I_SID (256-16 777 214), VRF_ID (1-511),
VRRP_ID (1-255), PRIORITY (1-254), ECMP_MAX_PATH (1-8), IP addresses, subnet masks,
and IST_NETWORK (/30 only).

---

## Adding a New Object Type

1. Add `SCHEMA_<NAME>` to `raffita/objects.py` with `validate`/`validate_msg` on range fields.
2. Write `build_<name>(params)` calling `render_template()`.
3. Write `delete_<name>(params)` returning VOSS `no ...` commands (or `None`).
4. Write `show_<name>(opts)` returning a list of show commands.
5. Add the entry to `OBJECTS` at the bottom of `raffita/objects.py`.
6. Create the matching Jinja2 template at `templates/<name>_template.j2`.

No changes needed in the interpreter — it reads everything from `OBJECTS`.

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
