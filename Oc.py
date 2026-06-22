from switch_backend import SwitchSession
from inventory import get_inventory
from colors import PINK, RED, ORANGE, LIGHT_GREEN, BRIGHT_ORANGE, RESET
from getpass import getpass
import os
import time
import argparse

# Clear Screen
os.system("clear")

# ASCII-Banner
print("\n\n")
print(PINK + "                 _                      __      _          _                    _    ")
print("  ___ _ __  _ _ (_)  ___   __ ___ _ _  / _|  __| |___ _ __| |___ _  _ __ _ __ _| |_  ")
print(" / _ \\ '  \\| ' \\| | |___| / _/ _ \\ ' \\|  _| / _` / -_) '_ \\ / _ \\ || / _` / _` | ' \\ ")
print(" \\___/_|_|_|_||_|_|       \\__\\___/_||_|_|   \\__,_\\___| .__/_\\___/\\_, \\__,_\\__,_|_||_|")
print("                                                     |_|         |__/                ")
print("                                                                              by Segi" + RESET)
print("\n\n")

# Argumente parsen
parser = argparse.ArgumentParser(
    description="Process .raffita files and execute commands on switches."
)
parser.add_argument(
    "files",
    nargs="*",
    help="Specific .raffita files to process. If omitted, all files in cwd are used.",
)
parser.add_argument(
    "--inventory",
    help="Path to inventory YAML file for per-host connection settings.",
    default=None,
)
args = parser.parse_args()

# Load inventory (optional — used for per-host save_command / reconnect settings)
inventory = get_inventory()
if args.inventory:
    try:
        inventory.load(args.inventory)
    except Exception as e:
        print(RED + f"Could not load inventory: {e}" + RESET)

# Benutzerinformationen abfragen
user     = input("Enter Username to logon to devices: ")
password = getpass()

# Aktuelles Arbeitsverzeichnis ermitteln
current_directory = os.getcwd()

# Variablen initialisieren
files_processed   = 0
commands_executed = 0

# Startzeit speichern
start_time = time.time()

# .raffita-Dateien ermitteln
if args.files:
    raffita_files = [f for f in args.files if os.path.isfile(f) and f.endswith(".raffita")]
    if not raffita_files:
        print(RED + "No valid .raffita files provided." + RESET)
        exit(1)
else:
    raffita_files = sorted(f for f in os.listdir(current_directory) if f.endswith(".raffita"))

if not raffita_files:
    print(RED + f"No .raffita files found in '{current_directory}'." + RESET)
    exit(1)

print("\n---\n")
print("The following .raffita files will be processed:\n")

# Dateien und deren Inhalt anzeigen
for file in raffita_files:
    print(BRIGHT_ORANGE + f"File:     {file}" + RESET)
    try:
        with open(os.path.join(current_directory, file), "r") as f:
            content = f.readlines()
        if content:
            for line in content:
                print(LIGHT_GREEN + f"          {line.rstrip()}" + RESET)
        else:
            print("    (empty file)")
    except Exception as e:
        print(f"Error reading file {file}: {e}")
    print("\n---\n")

print("Press Enter to start executing commands, or CTRL-C to quit.")
input()

# Dateien verarbeiten und Befehle ausfuehren
for raffita_file in raffita_files:
    hostname = raffita_file[:-8]  # strip ".raffita"
    print(f"\n\nProcessing device '{hostname}' using file '{raffita_file}'...\n")

    print(ORANGE + "READY??? " + RESET + "Confirm with Enter, or CTRL-C to quit.")
    input()

    try:
        with open(os.path.join(current_directory, raffita_file), "r") as f:
            config = f.read()

        # Count non-empty, non-comment lines for the summary
        cmd_count = sum(
            1 for line in config.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )

        # Read per-host settings from inventory (falls back to defaults if not found)
        entry = inventory.get_host(hostname)

        session = SwitchSession(
            host=hostname,
            username=user,
            password=password,
            reconnect_attempts=entry.reconnect_attempts if entry else 3,
            reconnect_delay=entry.reconnect_delay       if entry else 5,
            save_command=entry.save_command             if entry else "save config",
        )

        session.connect()
        session.send_config(config)   # skips comments, handles y/n, exits+saves
        session.disconnect()

        commands_executed += cmd_count
        files_processed   += 1

    except Exception as e:
        print(RED + f"Error processing {raffita_file} for {hostname}: {e}" + RESET)
        continue

# Endzeit und Zusammenfassung
end_time     = time.time()
elapsed_time = end_time - start_time
minutes, seconds = divmod(int(elapsed_time), 60)

print("\n\n###################################\n\n")
print(f"Processed {files_processed} devices and executed {commands_executed} commands.")
print(f"Elapsed time: {minutes} minutes and {seconds} seconds.\n\nHave fun ...\n")
