# Raffita — VOSS / Fabric Engine Config Rollout Toolkit

**raffita** is a Python toolkit for generating, staging, and deploying
configuration to Extreme VOSS / Fabric Engine switches via SSH (netmiko).
It provides both a batch runner (`Oc.py`) and an interactive REPL
(`raffita_interpreter.py`) with live push, rollback, and host inventory support.

---

## File Structure

```
raffita/
│
├── colors.py                  # Single source of truth for all ANSI colors
├── param_filling.py           # Unified parameter resolution (type, validate, prompt)
├── history_manager.py         # Persistent REPL history with age/size pruning
├── inventory.py               # YAML host/group inventory loader
├── rollback.py                # Pre-state capture and per-host rollback stacks
│
├── switch_backend.py          # SSH session management (netmiko wrapper)
├── raffita_gen_lib.py         # Jinja2 helpers, file I/O, argparse schema utils
├── raffita_objects.py         # Object registry (schemas, builders, delete/show cmds)
├── raffita_interpreter.py     # Interactive REPL (main entry point)
│
├── Oc.py                      # Batch .raffita file runner (no REPL)
│
├── inventory.yaml             # Your host/group inventory (create this yourself)
│
└── *.j2                       # Jinja2 config templates (one per object type)
```

---

## Installation

```bash
pip install netmiko jinja2 pyyaml
```

`pyyaml` is only needed if you use the inventory feature.
`readline` is part of the Python standard library on Linux/macOS.
On Windows install `pyreadline3` for history and tab-completion.

---

## Quick Start

### Batch mode — Oc.py

Create one `.raffita` file per switch, named `<hostname>.raffita`:

```
# sw-core-01.raffita
vlan create 100 name C010001000000_24
interface vlan 100
  ip address 10.1.0.1 255.255.255.0
```

Then run:

```bash
python Oc.py
# or target specific files:
python Oc.py sw-core-01.raffita sw-core-02.raffita
```

Oc.py asks for credentials once, previews every file, and confirms
before each switch before sending commands.

---

### Interactive REPL — raffita_interpreter.py

```bash
python raffita_interpreter.py
```

The interpreter starts in **dry-run mode** — it builds and previews
configs but sends nothing until you explicitly enable live push.

#### Minimal workflow

```
raffita> login
Username: admin
Password: ****

raffita> target sw-core-01

raffita> create anycast \
    --VLAN_ID 100 \
    --IP_ADDRESS 10.1.0.1 \
    --SUBNET_MASK 255.255.255.0 \
    --VRF_NAME GlobalRouter \
    --I_SID 1000100

# Preview shown. When happy:
raffita> live on
raffita> create anycast --VLAN_ID 100 ...
Push to sw-core-01? [y/N]: y
```

---

## Inventory File

Create `inventory.yaml` to predefine hosts, groups, and per-host settings.

```yaml
defaults:
  save_command: "save config"
  reconnect_attempts: 3
  reconnect_delay: 5

hosts:
  sw-core-01:
    description: "Core Switch 1, Building A"
  sw-core-02:
    description: "Core Switch 2, Building A"
  sw-acc-21:
    description: "Access Switch, Floor 2"
  sw-acc-22:
    description: "Access Switch, Floor 2"

groups:
  core:
    - sw-core-01
    - sw-core-02
  access-floor2:
    - sw-acc-21
    - sw-acc-22
  all-switches:       # groups can reference other groups with @
    - "@core"
    - "@access-floor2"
```

Load in the REPL:

```
raffita> inventory load inventory.yaml
raffita> inventory show          # summary
raffita> inventory hosts         # list all hosts
raffita> inventory groups        # list all groups with members
```

### Targeting groups

```
raffita> target @core
raffita> create anycast --VLAN_ID 100 ...
# runs against sw-core-01, then sw-core-02 in sequence
```

Per-host settings from the inventory (save command, reconnect config)
are automatically applied when a session is opened.

---

## Rollback

Every **live** `create` push automatically:
1. Captures a pre-deployment snapshot (`show` commands).
2. Registers an undo entry on a per-host stack.

### Commands

| Command | Effect |
|---|---|
| `rollback` | Undo the last deployment on the active target |
| `rollback last` | Same as above |
| `rollback all` | Undo all queued entries for the active target |
| `rollback list` | Show all queued entries (all hosts) |
| `rollback prestate` | Print pre-deployment show output for the active target |
| `rollback clear` | Clear the stack for the active target |
| `rollback clear --all` | Clear all stacks |

The stack is **session-scoped** (not persisted to disk).
Each host has an independent LIFO stack capped at 20 entries.

### Example

```
raffita> live on
raffita> target sw-core-01
raffita> create vrf --VRF_NAME PROD --VRF_ID 10 --L3_I_SID 1900010 \
         --VRF_MAX_ROUTES 2048 --ECMP_MAX_PATH 4
# [rollback] registered 'create vrf' on sw-core-01 (stack depth: 1/20)

# Oops — wrong VRF. Undo it:
raffita> rollback
  Rollback target: 'create vrf' on sw-core-01 (3s ago)
  Commands that will be executed:
    enable
    conf t
    term more disable
    no ip vrf PROD
  Execute rollback on sw-core-01? [y/N]: y
  Rollback OK
```

---

## Session Auto-Reconnect

If a switch session times out mid-deployment, the toolkit automatically
reconnects (up to `reconnect_attempts` times, with `reconnect_delay`
seconds between attempts). On reconnect:
- `enable` and `term more disable` are re-run.
- Config mode (`conf t`) is re-entered if the interrupted command was
  a config push.

You can also trigger a manual reconnect:

```
raffita> reconnect                  # reconnect active target
raffita> reconnect sw-core-01       # reconnect a specific host
raffita> reconnect --all            # reconnect all open sessions
```

---

## Config Mode — Save and Exit

After every successful `create` or `deploy` push, the session
automatically:
1. Sends `end` to leave config terminal mode.
2. Sends the configured save command (default: `save config`).

This means subsequent `show` commands (via the `command` verb) always
run from exec mode and never inherit stale config-mode context.

---

## Persistent History

Command history is written to `~/.raffita_history` in bash-compatible
format (timestamped). On startup, the file is pruned before loading:

| Limit | Default | Controlled by |
|---|---|---|
| Maximum entries | 2 000 | `HistoryManager(max_lines=...)` |
| Maximum age | 90 days | `HistoryManager(max_age_days=...)` |
| Maximum file size | 512 KB | `HistoryManager(max_size_kb=...)` |

Each command is appended immediately after entry so a crash does not
lose the session's history.  Tab-completion is available for all verbs,
object names, flags, and inventory host/group names.

---

## All Interpreter Commands

### Config management
| Command | Description |
|---|---|
| `create <obj> [--PARAM value ...]` | Build config and push live (or preview in dry-run) |
| `stage  <obj> [--PARAM value ...]` | Build config and write to `<host>.raffita` |
| `deploy [file.raffita ...]` | Push `.raffita` files (all in cwd, or named) |
| `command --CMD "..." [--HOSTNAME h]` | Send arbitrary exec-mode commands |

### Session management
| Command | Description |
|---|---|
| `target <host\|@group>\|none` | Set or clear the default target |
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
| `inventory load <file>` | Load YAML inventory |
| `inventory show` | Print inventory summary |
| `inventory hosts` | List all hosts |
| `inventory groups` | List all groups with members |

### Settings
| Command | Description |
|---|---|
| `live on\|off` | Enable / disable live push (`off` = dry-run) |
| `dryrun on\|off` | Same as `live` (inverted) |
| `confirm on\|off` | Ask before each push (default: on) |
| `status` | Show current settings |
| `objects` | List all available object types |
| `help [obj]` | General help or object parameter reference |
| `exit` / `quit` | Exit the interpreter |

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
| `cluster` | vIST cluster (generates config for two nodes) |

Use `help <obj>` to see all parameters, types, defaults, and validation rules.

---

## Parameter Validation

Every schema parameter that has a sensible range carries a `validate`
rule. Validation runs **before** the config is built or sent — the
interpreter will tell you exactly which field failed and why:

```
raffita> create anycast --VLAN_ID 9999 ...
--VLAN_ID: VLAN_ID must be 1-4094.
```

Validated fields include:
- `VLAN_ID` — 1–4094
- `I_SID` / `L3_I_SID` — 256–16 777 214
- `VRF_ID` — 1–511
- `VRRP_ID` — 1–255
- `PRIORITY` — 1–254
- `ECMP_MAX_PATH` — 1–8
- `IP_ADDRESS`, `VRRP_IP`, `NEXT_HOP` — valid IPv4 addresses
- `SUBNET_MASK` — valid subnet mask / prefix length
- `IST_NETWORK` — valid /30 network

---

## Staging and Batch Deploy

You can use `stage` to generate `.raffita` files in the REPL and then
run Oc.py for the actual batch deployment — useful when you want to
review or edit configs before sending:

```
# Generate
raffita> stage anycast --HOSTNAME sw-core-01 --VLAN_ID 100 ...
# Config written to sw-core-01.raffita

# Review / edit sw-core-01.raffita with any text editor, then:
python Oc.py sw-core-01.raffita

# Or deploy directly from the interpreter:
raffita> deploy sw-core-01.raffita
```

---

## .raffita File Format

Each file contains plain VOSS CLI commands, one per line:

```
# This is a comment — skipped during execution
vlan create 100 name C010001000000_24
interface vlan 100
  ip address 10.1.0.1 255.255.255.0
  ip vrf forwarding GlobalRouter
router isis
  i-sid 1000100 vlan 100
```

- Lines starting with `#` are comments and are skipped.
- Empty lines are skipped.
- `(y/n)` prompts are answered automatically with `y`.

---

## Extending — Adding a New Object Type

1. Add a `SCHEMA_<NAME>` dict to `raffita_objects.py`.
   Include `validate` and `validate_msg` for any field with a range.
2. Write a `build_<name>(params)` function that calls `render_template()`.
3. Write a `delete_<name>(params)` function returning VOSS `no ...` commands.
4. Write a `show_<name>(opts)` function returning a list of show commands.
5. Add the entry to `OBJECTS` dict at the bottom of `raffita_objects.py`.
6. Create the matching Jinja2 template (`<name>_template.j2`).

No changes needed in the interpreter — it reads everything from `OBJECTS`.

---

## Logging

All actions are logged to `raffita_interpreter.log` (configurable):
- Every command sent to a switch
- Full switch output (unfiltered)
- Connect / disconnect / reconnect events
- DRY-RUN entries (what *would* have been sent)
- Errors and warnings

The console output is filtered for readability; the log contains the
raw unfiltered switch responses.
