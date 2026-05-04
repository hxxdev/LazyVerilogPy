"""CLI entry point for lazyverilogpy-fmt binary.

Usage:
  lazyverilogpy-fmt [file ...]       # format in-place
  lazyverilogpy-fmt                  # read stdin, write stdout
  lazyverilogpy-fmt --check [file ..]# exit 1 if any file would change
"""
import argparse
import sys
from pathlib import Path

from lazyverilogpy.formatter import FormatOptions, SafeModeError, format_source
from lazyverilogpy.server import _find_config_toml, _load_fmt_options_from_toml


def _load_options(safe_mode: bool = False) -> FormatOptions:
    toml = _find_config_toml(Path.cwd())
    opts = _load_fmt_options_from_toml(toml) if toml is not None else FormatOptions()
    opts.safe_mode = safe_mode
    return opts


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="lazyverilogpy-fmt",
        description="Format SystemVerilog source files.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        metavar="FILE",
        help="Files to format in-place. If omitted, read stdin / write stdout.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if formatting would change any file (or stdin). No files are written.",
    )
    parser.add_argument(
        "--safe-mode",
        action="store_true",
        help="After formatting, verify no non-whitespace content was lost. Exit 2 on violation.",
    )
    args = parser.parse_args()

    options = _load_options(safe_mode=args.safe_mode)

    if not args.files:
        source = sys.stdin.read()
        try:
            result = format_source(source, options)
        except SafeModeError as exc:
            sys.stderr.write(f"<stdin>: {exc}\n")
            return 2
        if args.check:
            if result != source:
                sys.stderr.write("<stdin>: would reformat\n")
                return 1
            return 0
        sys.stdout.write(result)
        return 0

    changed = []
    for path in args.files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                source = f.read()
        except OSError as e:
            sys.stderr.write(f"{path}: {e}\n")
            return 2
        try:
            result = format_source(source, options)
        except SafeModeError as exc:
            sys.stderr.write(f"{path}: {exc}\n")
            return 2
        if result != source:
            changed.append(path)
            if not args.check:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(result)

    if args.check and changed:
        for p in changed:
            sys.stderr.write(f"{p}: would reformat\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
