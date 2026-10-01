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
DYNAMIC_ACTION_WRAPPERS = {
    ("publish_command", "publish"): "shared_publish_dispatch",
    ("publish_command", "on_repeat"): "history_replay",
    ("publish_tracked", "_run_text_prank_input"): "prompted_text_prank_dispatch",
    ("simple_command", "on_cmd_prank_generic"): "xlex_prank_dispatch",
    ("publish_tracked", "_simple_command_unlocked"): "simple_command_dispatch",
}


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


def _literal_dict_keys(tree: ast.AST, name: str) -> set[str]:
    """Read string keys from a named top-level literal dict without importing it."""
    for node in getattr(tree, "body", []):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        value = node.value
        if isinstance(value, ast.Dict):
            return {
                key.value for key in value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
    return set()


def _literal_status_values(node: ast.AST) -> set[str]:
    """Read result literals, skipping condition expressions and their labels."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        return _literal_status_values(node.body) | _literal_status_values(node.orelse)
    return set()


def _feature_status_inventory(path: Path) -> dict[str, list[str]]:
    """Collect feature keys and literal status values from the agent's status function."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    function = next(
        (node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
         and node.name == "_detect_feature_status"),
        None,
    )
    keys: set[str] = set()
    states: set[str] = set()
    if function is None:
        return {"keys": [], "states": []}

    for node in ast.walk(function):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    continue
                keys.add(key.value)
                states.update(_literal_status_values(value))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Subscript) or not isinstance(target.value, ast.Name):
                    continue
                if target.value.id != "statuses":
                    continue
                index = target.slice
                if isinstance(index, ast.Constant) and isinstance(index.value, str):
                    keys.add(index.value)
                    states.update(_literal_status_values(node.value))

    return {"keys": sorted(keys), "states": sorted(states)}


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


def _enclosing_function_name(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    parent = parents.get(node)
    while parent is not None:
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return parent.name
        parent = parents.get(parent)
    return None


def build_report(root: Path) -> dict:
    bot_path = root / "TG-BOT-SERVER" / "bot.py"
    tree = ast.parse(bot_path.read_text(encoding="utf-8"), filename=str(bot_path))
    capability_features = _literal_dict_keys(tree, "_CAPABILITY_LABELS")
    capability_states = _literal_dict_keys(tree, "_CAPABILITY_STATES")
    callbacks: set[str] = set()
    actions: set[str] = set()
    dynamic_action_sites: list[dict[str, str | int]] = []
    dynamic_action_wrappers: list[dict[str, str | int]] = []
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
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
            function_name = _enclosing_function_name(node, parents)
            wrapper_kind = DYNAMIC_ACTION_WRAPPERS.get((name, function_name or ""))
            site = {"line": node.lineno, "call": name, "function": function_name or "<module>"}
            if wrapper_kind:
                dynamic_action_wrappers.append({**site, "classification": wrapper_kind})
            else:
                dynamic_action_sites.append(site)

    platforms = {
        "windows": _agent_inventory(root / "XGENT-WDS" / "xgent_wds.py"),
        "macos": _agent_inventory(root / "XGENT-MCS" / "xgent_mcs.py"),
    }
    feature_status = {
        "expected_features": sorted(capability_features),
        "allowed_states": sorted(capability_states),
        "source_only": True,
        "platforms": {},
    }
    for platform, agent_path in {
        "windows": root / "XGENT-WDS" / "xgent_wds.py",
        "macos": root / "XGENT-MCS" / "xgent_mcs.py",
    }.items():
        inventory = _feature_status_inventory(agent_path)
        agent_features = set(inventory["keys"])
        agent_states = set(inventory["states"])
        feature_status["platforms"][platform] = {
            **inventory,
            "missing_features": sorted(capability_features - agent_features),
            "unexpected_features": sorted(agent_features - capability_features),
            "unrecognized_states": sorted(agent_states - capability_states),
        }
    guardians = {
        "windows": _guardian_commands(root / "XGENT-WDS" / "xider_guardian_wds.py"),
        "macos": _guardian_commands(root / "XGENT-MCS" / "xider_guardian.py"),
    }
    windows = set(platforms["windows"]["supported"])
    macos = set(platforms["macos"]["supported"])
    worker_actions = actions - {"guardian"}
    return {
        "schema": "x-map-static-audit-v4",
        "source_only": True,
        "bot_callbacks": sorted(callbacks),
        "callback_count": len(callbacks),
        "bot_actions": sorted(actions),
        "dynamic_action_calls_not_classified": len(dynamic_action_sites),
        "dynamic_action_sites": sorted(dynamic_action_sites, key=lambda item: int(item["line"])),
        "dynamic_action_wrappers": sorted(dynamic_action_wrappers, key=lambda item: int(item["line"])),
        "action_agent_coverage": {
            "covered_on_both": sorted(worker_actions & windows & macos),
            "missing_on_windows": sorted(worker_actions - windows),
            "missing_on_macos": sorted(worker_actions - macos),
        },
        "guardian_commands": guardians,
        "feature_status": feature_status,
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
