module test;
interface my_interface;
endinterface
struct {
    logic               a                                   ;
} my_struct;
union logic [3:0] my_union;
enum logic [1:0]{
    A, B} my_enum;
parameter int MY_PARAM      = 5;
localparam int LOCAL_VAR    = 10;
endmodule
