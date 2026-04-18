`include "params.svh"

`define WIDTH 32

module memory_top;
    logic [`WIDTH-1:0] data;
    logic [2:0] a, b, c;

    function logic [3:0] sum(input int a, input fifo_entry_t b, input int c);
        return a + b;
    endfunction

    memory u_memory(.i_clk(i_clk), .address(address), .data_in(data_in), .data_out(data_out), .read_write(read_write), .chip_en(chip_en));

    always_comb begin
        a    = 3;
        c    = sum(a + b);
    end

endmodule
