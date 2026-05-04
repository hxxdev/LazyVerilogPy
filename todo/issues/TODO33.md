# TODO33: Stale Autoinst Diagnostic

Emit a lint warning when a module's port list has changed but its instantiation in the current file is outdated.

## Details
- In `run_lint` (or a new pass), find all module instantiations in the current file
- For each instance, compare actual port list (from pyslang compilation) with connected ports in the instantiation body
- Warn if ports present in module but missing from instantiation, or vice versa
- Reuse existing `_find_instance_at_line` and `autoinst_impl` infrastructure
- Severity: Warning

## Priority
High — lowest effort (reuses existing code), catches real RTL bugs early.
