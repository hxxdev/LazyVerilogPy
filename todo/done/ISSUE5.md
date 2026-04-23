I have open @tests/demo/memory_top.sv and @tests/demo/memory.sv but removing port i_clk from memory and doing AutoInst at memory_top does not reflect the removed port i_clk.

Fix this.

This issue does not happen if I quit and restart the editor.

Fix this not-synced issue for all AutoInst, AutoFunc, RtlTree ... etc.
