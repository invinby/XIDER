#!/usr/bin/env python3
"""Read-only static inventory of XIDER bot callbacks and agent commands.

This tool parses Python ASTs only. It does not import or execute the bot/agents,
and its report is a source-code compatibility check, not proof of live support.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path


CALLBACK_RE = re.compile(r"^cmd:([a-zA-Z0-9_]+)$")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
CALLBACK_ALIASES = {"check_update": "agent_update"}


def _string_constants(node: ast.AST) -> set[str]:
    return {
        item.value
        for item in ast.walk(node)
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    }


def _agent_inventory(path: Path) -> dict[str, list[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    declared: set[str] = set()
    handlers: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == "SUPPORTED_COMMANDS" for target in targets):
                declared.update(_string_constants(node.value))
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and isinstance(value, ast.Attribute)
                    and value.attr.startswith("_do_")
                ):
                    handlers.add(key.value)

    declared = {item for item in declared if NAME_RE.fullmatch(item)}
    return {
        "supported": sorted(declared),
        "handlers": sorted(handlers),
        "declared_without_handler": sorted(declared - handlers),
        "handler_without_declaration": sorted(handlers - declared),
    }


def _guardian_commands(path: Path) -> list[str]:
    """Collect literal command branches from a Guardian.handle(command) method."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    commands: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Name):
            continue
        if node.left.id != "command":
            continue
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                if NAME_RE.fullmatch(comparator.value):
                    commands.add(comparator.value)
    return sorted(commands)


def build_report(root: Path) -> dict:
    bot_path = root / "TG-BOT-SERVER" / "bot.py"
    tree = ast.parse(bot_path.read_text(encoding="utf-8"), filename=str(bot_path))
    callbacks: set[str] = set()
    actions: set[str] = set()
    dynamic_action_sites: list[dict[str, str | int]] = []
    for node in ast.walk(tree):
        for value in _string_constants(node):
            match = CALLBACK_RE.fullmatch(value)
            if match:
                callbacks.add(match.group(1))
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = function.id if isinstance(function, ast.Name) else (
            function.attr if isinstance(function, ast.Attribute) else ""
        )
        if name == "publish_tracked":
            action_arg = node.args[0] if node.args else None
        elif name == "publish_command":
            action_arg = node.args[1] if len(node.args) > 1 else None
        elif name == "simple_command":
            action_arg = node.args[1] if len(node.args) > 1 else None
        else:
            continue
        if isinstance(action_arg, ast.Constant) and isinstance(action_arg.value, str):
            actions.add(CALLBACK_ALIASES.get(action_arg.value, action_arg.value))
        else:
            dynamic_action_sites.append({"line": node.lineno, "call": name})

    platforms = {
        "windows": _agent_inventory(root / "XGENT-WDS" / "xgent_wds.py"),
        "macos": _agent_inventory(root / "XGENT-MCS" / "xgent_mcs.py"),
    }
    guardians = {
        "windows": _guardian_commands(root / "XGENT-WDS" / "xider_guardian_wds.py"),
        "macos": _guardian_commands(root / "XGENT-MCS" / "xider_guardian.py"),
    }
    windows = set(platforms["windows"]["supported"])
    macos = set(platforms["macos"]["supported"])
    worker_actions = actions - {"guardian"}
    return {
        "schema": "x-map-static-audit-v2",
        "source_only": True,
        "bot_callbacks": sorted(callbacks),
        "callback_count": len(callbacks),
        "bot_actions": sorted(actions),
        "dynamic_action_calls_not_classified": len(dynamic_action_sites),
        "dynamic_action_sites": sorted(dynamic_action_sites, key=lambda item: int(item["line"])),
        "action_agent_coverage": {
            "covered_on_both": sorted(worker_actions & windows & macos),
            "missing_on_windows": sorted(worker_actions - windows),
            "missing_on_macos": sorted(worker_actions - macos),
        },
        "guardian_commands": guardians,
        "guardian_coverage": {
            "required": "guardian" in actions,
            "missing_on_windows": ["guardian"] if "guardian" in actions and not guardians["windows"] else [],
            "missing_on_macos": ["guardian"] if "guardian" in actions and not guardians["macos"] else [],
        },
        "platforms": platforms,
        "platform_parity": {
            "windows_only": sorted(windows - macos),
            "macos_only": sorted(macos - windows),
            "shared_count": len(windows & macos),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only XIDER command compatibility audit")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, help="write JSON report to this path")
    args = parser.parse_args()
    report = build_report(args.root.resolve())
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
