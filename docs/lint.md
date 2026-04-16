## Multi-file codebase loading

### `[codebase]` section

To give the language server visibility into the rest of your codebase — enabling
cross-file hover, go-to-definition, and accurate diagnostics — point it at a
Verilog/SystemVerilog filelist (`.f`) file.

```toml
[codebase]
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

- All listed files are added to pyslang's `Compilation` alongside the file
  currently open in the editor.
- If the open file is also present in the list it is **not** added a second time,
  so there is no "redefinition" error.
- If another listed file is open in the editor its **in-memory (unsaved) text**
  is used, so port-name changes and other edits are reflected immediately in
  hover tooltips and diagnostics of files that instantiate it.
