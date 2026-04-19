// Port declaration alignment fixture
// Tests 4-column alignment: direction / type / dimension / name

module port_align_basic(input i_clk, input data_t[7:0] i_data_array, input logic [7:0] i_data_valid, input i_valid, output data_t[15:0] o_data_array);
endmodule

module port_align_mixed(input logic clk, input logic rst_n, input logic [7:0] i_byte, input logic [31:0] i_word, output logic o_ready, output logic [7:0] o_result);
endmodule

module port_align_no_type(input i_a, input i_b, output o_c);
endmodule

module port_align_inout(input logic i_clk, inout logic [7:0] io_bus, output logic o_valid);
endmodule
