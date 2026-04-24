1.
move these options to [formatter.port] and change names
- `module_ports_per_line_enabled` -> change name to `non_ansi_port_per_line_enabled`
- `module_ports_per_line` -> change name to `non_ansi_port_per_line`
- `module_max_line_length_for_ports_enabled` -> change name to -`non_ansi_port_max_line_length_enabled`
- `module_max_line_length_for_ports` -> change name to `non_ansi_port_max_line_length`

2.
change name `disable_format_on_save` to `enable_format_on_save` and change the source codes accordingly. The default value should be false.

3.
@tests/demo/memory_top.sv aligning of assign operators inside always_comb is not normal.
Current behavior:
```
always_comb begin
    // tte
    a      = 3;
    // tte
    c      = sum(.i_a(i_a), .i_b(i_b));
    // tte
    d       = {1'b0, a};
    tddd    = 3;
    ddddddd = 3;

    if (a == 3) begin
        tddd   = 3;
    end
    else begin
        tddd += 4;
    end
end
```
problem: it is not tab aligned even if tab_align is set true @lazyverilog.toml.
assign operators are not aligned. lhs_min_width(=6) is not complied at tddd += 4;

Expected behavior:
```
always_comb begin
    // tte
    a           = 3;
    // tte
    c           = sum(.i_a(i_a), .i_b(i_b));
    // tte
    d           = {1'b0, a};
    tddd        = 3;
    ddddddd     = 3;

    if (a == 3) begin
        tddd    = 3;
    end
    else begin
        tddd    += 4;
    end
end

```
