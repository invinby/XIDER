"""Path-bound loader for the canonical command verifier and replay ledger."""

import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path


_MODULE_NAMES = frozenset({"command_signing", "command_replay"})
_CACHE = {}


def _same_absolute_path(left, right) -> bool:
    return os.path.normcase(os.path.abspath(os.fspath(left))) == os.path.normcase(os.path.abspath(os.fspath(right)))


def _source_component_spec(module_name: str, source_root: str | os.PathLike):
    root = Path(source_root).resolve(strict=True)
    expected = root / f"{module_name}.py"
    if expected.is_symlink() or not expected.is_file():
        raise ImportError("Trusted command approval source is missing or not a regular file")
    try:
        resolved = expected.resolve(strict=True)
        if not _same_absolute_path(expected, resolved) or not _same_absolute_path(expected.parent.resolve(strict=True), root):
            raise ImportError("Trusted command approval source escaped its repository directory")
        spec = importlib.util.spec_from_file_location(module_name, expected)
        if (
            spec is None
            or spec.name != module_name
            or type(spec.loader) is not importlib.machinery.SourceFileLoader
            or not spec.origin
            or not _same_absolute_path(spec.origin, expected)
            or not _same_absolute_path(spec.loader.get_filename(module_name), expected)
        ):
            raise ImportError("Trusted command approval source has an unexpected import spec")
        return spec, ("source", module_name, os.path.normcase(os.path.abspath(os.fspath(expected))))
    except (OSError, RuntimeError, ValueError) as exc:
        raise ImportError("Trusted command approval source could not be validated") from exc


def _frozen_component_spec(module_name: str, *, runtime=None, meipass=None):
    runtime = sys.modules.get("pyimod02_importers") if runtime is None else runtime
    finder_type = getattr(runtime, "PyiFrozenFinder", None)
    loader_type = getattr(runtime, "PyiFrozenLoader", None)
    archive = getattr(runtime, "pyz_archive", None)
    if (
        runtime is None
        or not isinstance(finder_type, type)
        or finder_type.__name__ != "PyiFrozenFinder"
        or finder_type.__module__ != "pyimod02_importers"
        or not isinstance(loader_type, type)
        or loader_type.__name__ != "PyiFrozenLoader"
        or loader_type.__module__ != "pyimod02_importers"
        or archive is None
        or not isinstance(getattr(archive, "toc", None), dict)
    ):
        raise ImportError("Trusted PyInstaller PYZ loader is unavailable")
    meipass = getattr(sys, "_MEIPASS", None) if meipass is None else meipass
    if not isinstance(meipass, (str, os.PathLike)) or not os.fspath(meipass):
        raise ImportError("PyInstaller extraction directory is unavailable")
    try:
        finder = finder_type(os.fspath(meipass))
        if (
            type(finder) is not finder_type
            or getattr(finder, "_pyz_archive", None) is not archive
            or not _same_absolute_path(getattr(finder, "_path", ""), meipass)
        ):
            raise ImportError("PyInstaller finder is not bound to the active PYZ archive")
        spec = finder.find_spec(module_name)
        loader = getattr(spec, "loader", None)
        expected_origin = Path(meipass) / f"{module_name}.py"
        if (
            spec is None
            or getattr(spec, "name", None) != module_name
            or type(loader) is not loader_type
            or getattr(loader, "name", None) != module_name
            or getattr(loader, "_pyz_archive", None) is not archive
            or getattr(loader, "_pyz_entry_name", None) != module_name
            or getattr(loader, "_is_package", None) is not False
            or module_name not in archive.toc
            or not getattr(loader, "path", None)
            or not getattr(spec, "origin", None)
            or not _same_absolute_path(loader.path, expected_origin)
            or not _same_absolute_path(spec.origin, expected_origin)
            or getattr(spec, "submodule_search_locations", None) is not None
        ):
            raise ImportError("Command approval module is not an exact bundled PYZ entry")
        return spec, ("frozen", module_name, id(archive))
    except ImportError:
        raise
    except Exception as exc:
        raise ImportError("Bundled command approval module could not be resolved") from exc


def load_component(module_name: str, *, source_root: str | os.PathLike, frozen: bool | None = None):
    """Load an approval helper only from its trusted source sibling or active PYZ entry."""
    if module_name not in _MODULE_NAMES:
        raise ImportError("Unexpected command approval module")
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    try:
        spec, cache_key = (
            _frozen_component_spec(module_name)
            if frozen
            else _source_component_spec(module_name, source_root)
        )
        cached = _CACHE.get(cache_key)
        if cached is not None:
            cached_spec = getattr(cached, "__spec__", None)
            if (
                cached_spec is not None
                and cached_spec.name == spec.name
                and cached_spec.origin
                and spec.origin
                and _same_absolute_path(cached_spec.origin, spec.origin)
                and type(cached_spec.loader) is type(spec.loader)
            ):
                return cached
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _CACHE[cache_key] = module
        return module
    except ImportError:
        raise
    except Exception as exc:
        raise ImportError("Trusted command approval component could not be loaded") from exc
