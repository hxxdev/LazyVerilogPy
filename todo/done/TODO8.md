### Implement module instance port alignment

#### Trigger

FormatOptions align_instance_ports (bool type) is True.

**Expected behavior**

When enabled, format and vertically align all named port connections in a module instance so that:
- . (dot) characters are aligned
- ( opening parentheses are aligned
- ) closing parentheses are aligned
- Each port appears on its own line
- The instance body is expanded into a multi-line block

**Formatting rules**
- Break single-line instance into multi-line format.
- Place opening ( of instance on a new line.
- One port per line.
- Align the following columns:
- Dot (.) before port name
- Opening parenthesis (
- Closing parenthesis )
- Preserve original port ordering.
- Keep trailing comma except for the last port.
- Closing ); placed on its own line aligned with instance start.

**Additional FormatOptions**
| Option Name                          | Type | Description                                          |
| ------------------------------------ | ---- | ---------------------------------------------------- |
| `align_instance_ports`               | bool | Enable/disable instance port alignment               |
| `instance_port_indent_level`         | int  | Indent level (spaces) for ports                      |
| `instance_port_spacing_before_paren` | int  | Spaces between port name and `(`                     |
| `instance_port_spacing_inside_paren` | int  | Spaces between signal and `)`                        |

**Input example**
memory u_memory(.i_clk(i_clk), .address(address), .data_in(data_in), .data_out(data_out), .read_write(read_write), .chip_en(chip_en));

**Output example**
memory u_memory (
    .i_clk      (i_clk      ),
    .address    (address    ),
    .data_in    (data_in    ),
    .data_out   (data_out   ),
    .read_write (read_write ),
    .chip_en    (chip_en    )
);
Column layout
Column	Description
Col 1	. + port name
Col 2	spacing before ( (configurable)
Col 3	(
Col 4	connected signal
Col 5	spacing before ) (configurable)
Col 6	)
Col 7	trailing comma
