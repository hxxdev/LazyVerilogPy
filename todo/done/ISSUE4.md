Fix these issues
1. AutoInst(0) should not update the port already residing.

For example @tests/demo/memory_top.sv 

    memory u_memory (
        .i_clk      (i_clk),
        .address    (addr),
        .data_in    (data_in),
        .data_out   (data_out),
        .read_write (read_write)
    );

AutoInst(0) on above instance should not update the port connection on `address`. It should be be `addr`.

2. Make sure algorithm of AutoFunc, AutoInst, and RtlTree is pyslang AST-based not regex based. If not, change it pyslang AST-based algorithm

