module port_align(i_clk, i_rst_n, i_data, i_valid, o_data, o_valid);

    input                                           i_clk  ;
    input                                           i_rst_n;
    input           data_t          [7:0]           i_data ;
    input           logic                           i_valid;
    output          data_t          [15:0]          o_data ;
    output          logic                           o_valid;

endmodule
