# Executable Manager

## Overview

This python application is an utility to discover and manage executuables from the configured locations.

## Tech Stack

- CLI: Python (>3.13)

## Coding Rules

- Do not do any test case creation, testing, verification, compilation unless asked

## Setup

Activate the local virtual environment before running the CLI:

- PowerShell: `.\.venv\Scripts\Activate.ps1`
- Git Bash: `source .venv/Scripts/activate`

The project has no third-party dependencies; `requirements.txt` documents the standard-library-only setup.

Run the CLI from the repository root:

- Show available commands: `python main.py --help`
- Scan the built-in Windows locations: `python main.py scan`
- Scan one or more custom directories: `python main.py scan --root "D:\Tools" --root "D:\Scripts"`
- Search the existing catalog: `python main.py find claude`
- Refresh custom locations before searching: `python main.py find --fresh --root "D:\Tools" claude`
- Limit a search to executable files: `python main.py find claude --extension .exe`
- Inspect a result returned by `find`: `python main.py show RECORD_ID`
- Review collisions, source provenance, potential wrappers, and stale records: `python main.py quality`
- Use a project-local catalog instead of the default local-app-data catalog: `python main.py --cache catalog.json scan --root "D:\Tools"`
