#!/usr/bin/env python3
# colors.py
#
# Single source of truth for all ANSI terminal colors and effects.
# Every other Raffita module imports from here — never re-define colors locally.
#
# Usage:
#   from colors import RED, LIGHT_GREEN, ORANGE, RESET

# ── Reset ────────────────────────────────────────────────────────────────────
RESET = "\033[0m"

# ── Effects ──────────────────────────────────────────────────────────────────
BOLD      = "\033[1m"
UNDERLINE = "\033[4m"
BLINK     = "\033[5m"

# ── RED → ORANGE → YELLOW ────────────────────────────────────────────────────
RED           = "\033[31m"          # standard ANSI red (broad terminal compat)
RED_1         = "\033[38;5;196m"
RED_2         = "\033[38;5;202m"
RED_3         = "\033[38;5;208m"
ORANGE        = "\033[38;5;214m"
BRIGHT_ORANGE = "\033[38;5;215m"
ORANGE_3      = "\033[38;5;216m"
YELLOW_1      = "\033[38;5;220m"
YELLOW_2      = "\033[38;5;226m"
YELLOW_3      = "\033[38;5;229m"

# ── YELLOW-GREEN → GREEN ─────────────────────────────────────────────────────
YELLOW_GREEN_1 = "\033[38;5;190m"
YELLOW_GREEN_2 = "\033[38;5;154m"
GREEN_1        = "\033[38;5;118m"
GREEN_2        = "\033[38;5;82m"
LIGHT_GREEN    = "\033[92m"
GREEN_3        = "\033[38;5;40m"
GREEN_4        = "\033[38;5;34m"

# ── GREEN → CYAN ─────────────────────────────────────────────────────────────
GREEN_CYAN_1 = "\033[38;5;37m"
GREEN_CYAN_2 = "\033[38;5;43m"
CYAN_1       = "\033[38;5;44m"
CYAN_2       = "\033[38;5;45m"
CYAN         = "\033[0;36m"
LIGHT_CYAN   = "\033[1;36m"

# ── CYAN → BLUE ──────────────────────────────────────────────────────────────
BLUE_1       = "\033[38;5;39m"
BLUE_2       = "\033[38;5;33m"
LIGHT_BLUE   = "\033[94m"
LIGHT_BLUE_2 = "\033[1;34m"
BLUE_3       = "\033[38;5;27m"
BLUE_4       = "\033[38;5;21m"

# ── BLUE → MAGENTA / PINK ────────────────────────────────────────────────────
BLUE_MAGENTA_1 = "\033[38;5;57m"
BLUE_MAGENTA_2 = "\033[38;5;93m"
MAGENTA_1      = "\033[38;5;129m"
MAGENTA_2      = "\033[38;5;165m"
PINK_1         = "\033[38;5;201m"
PINK           = "\033[95m"

# ── Grayscale ────────────────────────────────────────────────────────────────
BLACK  = "\033[30m"
WHITE  = "\033[97m"
GRAY_1  = "\033[38;5;232m"
GRAY_2  = "\033[38;5;233m"
GRAY_3  = "\033[38;5;235m"
GRAY_4  = "\033[38;5;237m"
GRAY_5  = "\033[38;5;239m"
GRAY_6  = "\033[38;5;241m"
GRAY_7  = "\033[38;5;243m"
GRAY_8  = "\033[38;5;245m"
GRAY_9  = "\033[38;5;247m"
GRAY_10 = "\033[38;5;249m"
GRAY_11 = "\033[38;5;251m"
GRAY_12 = "\033[38;5;253m"
GRAY_13 = "\033[38;5;255m"
