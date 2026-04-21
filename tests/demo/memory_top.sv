`include "params.svh"

`define WIDTH 32

function logic [3:0] sum(input i_a, input i_b);
    return i_a + i_b;
endfunction

task add_numbers(input int a, input int b, output int result);
    result = a + b;
endtask

module memory_top(i_clk);
    input i_clk;
    logic           [`WIDTH-1:0]            data                    ;
    // a, b, c
    logic           [2:0]                   a                       , b /* eho */             , c                       ;
    //dd
    /* ehlo */  // a, b, c
    //
    //
    //
    //
    logic           [7:0]                   data_out                ;
    // b
    /* ehlo */
    logic           [2:0]                   d                       ;

    assign d = a;

    memory u_memory (
        .i_clk                  (i_clk                  ),
        .address                (address                ),
        .data_in                (data_in                ),
        .data_out               (data_out               ),
        .read_write             (read_write             ),
        .chip_en                (chip_en                )
    );
    memory u_pll1();
    memory u_pll2();
    memory u_pll3();

    always_comb begin
        a    = 3;
        c    = sum(.i_a(3), .i_b(i_b));
        c    = add_numbers(a, b, result);
    end

endmodule
