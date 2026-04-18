#include < params.svh >

function sum(input int a, input fifo_entry_t b, input int c);
    return a + b;
endfunction

module memory_inst(input i_pclk, input[2:0] i_data, input i_valid);

logic [2:0] a, b, c;
memory u_memory(.i_clk(), .address());

always_comb begin
    a    = 3;
    c    = sum(a + b);
end

endmodule
