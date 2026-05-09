## Multi-file design loading

### `[design]` section

To give the language server visibility into the rest of your design — enabling
cross-file hover, go-to-definition, and accurate diagnostics — point it at a
Verilog/SystemVerilog filelist (`.f`) file.

```toml
[design]
vcode = "rtl/files.f"
```

The path is resolved relative to `lazyverilog.toml`.  An absolute path is also
accepted.

#### `.f` file format

Each non-blank line that does not start with `#`, `//`, or `-` is treated as a
file path.  Relative paths are resolved relative to the `.f` file itself.

```
# line comment
// also a comment
-timescale 1ns/1ps   ← compiler flags are skipped

rtl/memory.sv
rtl/memory_top.sv
/absolute/path/pkg.sv
```

#### How it works

- All listed files are parsed into a `SyntaxIndex` on startup (one-time cost).
- The open buffer is re-parsed on every keystroke; the extra-file index is reused
  unchanged, so keystroke latency is constant regardless of `.f` list size.
- If the open file is also present in the list it is **not** added a second time.
- If another listed file is open in the editor its **in-memory (unsaved) text**
  is used, so port-name changes are reflected immediately in inlay hints and
  auto-instantiation of files that reference it.

---

### `[perf]` section

Controls LSP server performance behaviour.

```toml
[perf]
background_compilation = false  # default
nice_value = 10                 # default
log_timing = false              # default
```

#### `background_compilation`

When `false` (default): only syntax-parse diagnostics and lint rules are
published.  Response is instantaneous — suitable for shared/HPC environments.

When `true`: a separate subprocess runs full pyslang semantic elaboration after
each file open or change.  Publishes additional semantic diagnostics (undeclared
signals, type mismatches, port errors) once elaboration completes.  The
subprocess never blocks the LSP event loop.

#### `nice_value`

Unix process priority for the background compilation subprocess (0 = normal,
19 = lowest).  Only applies when `background_compilation = true`.  Set to `10`
or higher on shared HPC nodes to avoid competing with other users.

#### `log_timing`

When `true`, emits `[perf]` timing lines to the LSP log for each hot-path
operation:

```
[perf] parse_syntax                          0.19 ms  memory_top.sv
[perf] rebuild_syntax_index (buffers only)   1.05 ms
[perf] rebuild_extra_syntax_index (500 files) 521.00 ms
[perf] _parse/compilation (500 extra files) 8200.00 ms
```

Filter with: `grep '\[perf\]' lazyverilog.log`

#### Diagnostics tiers

| Tier | Source | Requires |
|------|--------|----------|
| Syntax errors | `SyntaxTree.diagnostics` | always (no compilation) |
| Lint rules | `[lint.*]` config | always (no compilation) |
| Semantic errors | pyslang `Compilation` | `background_compilation = true` |
