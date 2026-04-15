"""Generate formatter reference outputs for every RTL file in tests/rtl/.

Usage (from repo root):
    PYTHONPATH=src python tests/gen_answers.py

For each *.sv / *.v / *.svh / *.vh file found in tests/rtl/ (non-recursively)
the script runs format_source() and writes the result to tests/rtl/formatted/
with the same filename, creating the directory if needed.
"""

import sys
from pathlib import Path

# Resolve repo root relative to this file so the script works from any cwd.
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR   = REPO_ROOT / "src"
RTL_DIR   = REPO_ROOT / "tests" / "rtl"
OUT_DIR   = REPO_ROOT / "tests" / "formatted"

import logging
logging.disable(logging.CRITICAL)  # suppress DEBUG output from formatter

sys.path.insert(0, str(SRC_DIR))
from lazyverilogpy.formatter import FormatOptions, format_source  # noqa: E402

EXTENSIONS = {".sv", ".v", ".svh", ".vh"}
CONFIG_FILENAME = "lazyverilog.toml"


def _load_opts_from_toml(repo_root: Path) -> FormatOptions:
    """Load FormatOptions from lazyverilog.toml, walking up from repo_root.

    Falls back to default FormatOptions() if no config file is found or if
    no toml library is available.
    """
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError:
            tomllib = None  # type: ignore[assignment]

    current = repo_root.resolve()
    while True:
        candidate = current / CONFIG_FILENAME
        if candidate.is_file():
            if tomllib is None:
                print(f"  WARNING: found {candidate} but no toml library available; using defaults")
                return FormatOptions()
            with candidate.open("rb") as fh:
                data = tomllib.load(fh)
            cfg = data.get("formatter", {})
            opts = FormatOptions.from_dict(cfg)
            print(f"  config   {candidate}")
            return opts
        parent = current.parent
        if parent == current:
            break
        current = parent

    print(f"  config   (none found — using defaults)")
    return FormatOptions()


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    sources = sorted(
        p for p in RTL_DIR.iterdir()
        if p.is_file() and p.suffix in EXTENSIONS
    )

    if not sources:
        print(f"No RTL files found in {RTL_DIR}", file=sys.stderr)
        sys.exit(1)

    opts = _load_opts_from_toml(REPO_ROOT)
    ok = err = 0

    for src in sources:
        try:
            text = src.read_text(encoding="utf-8")
            formatted = format_source(text, opts)
            (OUT_DIR / src.name).write_text(formatted, encoding="utf-8")
            print(f"  formatted  {src.name}")
            ok += 1
        except Exception as exc:
            print(f"  ERROR      {src.name}: {exc}", file=sys.stderr)
            err += 1

    print(f"\n{ok} file(s) written to {OUT_DIR}")
    if err:
        print(f"{err} file(s) failed", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
