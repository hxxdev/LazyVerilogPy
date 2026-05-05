1. Change the Option section name 'formatter' to 'format'. Update the docs and lazyverilog.toml accordingly.

2. Active parameter highlighting is not working.

Repro:
- In demo/memory_top.sv
- While typing: sum(3|
- The first parameter (input packet_t i_a) should be highlighted as the active parameter

Expected:
- Active parameter should be highlighted via LSP signatureHelp

Check:
- LSP signatureHelp response (activeParameter index)
- blink.cmp highlight configuration (see /home/hxxdev/dotfiles/nvim/.config/nvim/lua/plugins/blink_cmp.lua)

3. Change options under [formatter.instance]
- port_spacing_before_paren -> instance_port_name_width
- port_spacing_inside_paren -> instance_port_between_paren_width

- port_spacing_before_paren → instance_port_name_width
  - Defines minimum width for port name (between '.' and '(' in named connections)

- port_spacing_inside_paren → instance_port_between_paren_width
  - Defines minimum spacing between '(' and ')'

Add bool type option `align_instance_port_adaptive` under section [formatter.instance]
- If true, ( in line with port name longer than the instance_port_name_width, ( of corresponding line is adaptively not aligned.
- If false, all '(' are aligned to the maximum port name width across all ports.(strict column alignment)
Add it to the docs and lazyverilog.toml.
- Same thing applies to instance_port_between_paren_width. If wire name is longer than instance_port_between_paren_width, same principle is applied by `align_instance_port_adaptive`.

Update the docs and lazyverilog.toml accordingly.

4. Remove option module_param_per_line_enabled and make it always 1 parameter per line(always behave like as if the option was true!)

5. Fix formatter to also be able to handle ansi port style. should be one port per line.

Requirements:
- One port per line
- Preserve direction, type, and width
- Align according to section [formatter.port_declaration]
    - Align columns `align` is set true. If false, left align.
    - If `align` is set true, align complying section1_min_width, section2_min_width, section3_min_width, section4_min_width, and section5_min_width.

Example:
```
module alu #(
    parameter WIDTH = 32
)(
    input  logic                  clk,
    input  logic                  rst_n,
    input  logic [WIDTH-1:0]      a,
    input  logic [WIDTH-1:0]      b,
    input  logic [1:0]            op,
    output logic [WIDTH-1:0]      result,
    output logic                  valid
);
```

Update the docs and lazyverilog.toml accordingly.

6. Write Makefile that builds a binary of linter
Linter Requirements:
- looks for lazyverilog.toml from the running path toward the root. If not found, warn user about it and use the default option.
- include pyslang diagnostics
- include lazyverilogpy-lint diagnostics.

Makefile Requirements:
- should be seamlessly buildable in any environment(No dependency/import issue)
- use pyinstaller option `--optimize 2` for maximum performance

7. review the Makefile that builds formatter and make sure the followings:
- all imports(dynamic and static) are included in --collect-all options.

8. Leave a commit for each job.
