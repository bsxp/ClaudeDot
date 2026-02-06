# Contributing to Claude Helper

Thanks for your interest in contributing! This document outlines how to get involved.

## License

By contributing to this project, you agree that your contributions will be licensed under the [CC BY-NC 4.0](LICENSE) license. This means contributions cannot be used for commercial purposes.

## Getting Started

1. Fork the repository
2. Create a feature branch from `main`
3. Run `./setup.sh` (macOS/Linux) or `.\setup.ps1` (Windows) to set up your environment
4. Make your changes
5. Test on your platform (macOS or Windows)
6. Submit a pull request

## Reporting Bugs

Open an issue with:
- Your OS and Python version
- Steps to reproduce the problem
- Expected vs actual behavior
- Any relevant log output

## Suggesting Features

Open an issue describing:
- The problem you're trying to solve
- Your proposed solution
- Any alternatives you've considered

## Pull Requests

- Keep PRs focused — one feature or fix per PR
- Update the README if your change affects setup, usage, or behavior
- Test your changes with at least one active Claude Code session
- Make sure the tray app starts and polls correctly after your changes

## Code Style

- Python code should be readable and straightforward
- Follow existing patterns in the codebase
- No external dependencies without discussion first — the dependency footprint is intentionally small

## Architecture Notes

- `info.json` is the sole source of truth for display state (icon color, status labels)
- Pending files (`pending/*.json`) are for sub-menu content only and must never influence the icon or status
- Hooks write to `~/.claude-helper/sessions/[SESSION_ID]/` on every lifecycle event
- The tray app polls the state directory; hooks and the tray app do not communicate directly

## Questions?

Open an issue — happy to help.
