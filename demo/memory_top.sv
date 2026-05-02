`include "params.svh"
`define WIDTH 32
`define print_bytes(ARR, STARTBYTE, NUMBYTES) \
    for (int ii=STARTBYTE; ii<STARTBYTE+NUMBYTES; ii++) begin \
        if ((ii != 0) && (ii % 16 == 0)) \
            $display("\n"); \
        $display("0x%x ", ARR[ii]); \
    end

typedef struct {
    logic               [7:0]               addr                                ;
    logic                                   valid                               ;
} packet_wo_data_t;

typedef struct {
    logic signed        [7:0]               addr                                ;
    logic               [31:0]              data                                ;
    logic                                   valid                               ;
} packet_t;

function packet_t sum(input i_a, input i_b);
    return packet_t'({40'b0, i_a} + i_b);
endfunction

task add_numbers(input int a, input int b, output int result);
    result  = a + b;
endtask

parameter DEPTH = 8;

module memory_top(
    i_clk, i_data,
    i_data2,
    i_data3, i_dd,
    i_dd22222,
    dd22222,
    i_d33333,
    i_d44333,
    i_dd44321,
    i_d44334, VDD,
    VSS
);
input                                           i_clk                                   ;
input                                           i_rst_n                                 ;
input   logic signed                            i_data              [7:0]               ;
input   var byte                                i_data2                                 ;
input                                           i_data3                                 ;
input                                           i_dd                                    ;
input                                           i_dd22222                               ;
input                                           dd22222                                 ;
input                                           i_d33333                                ;
input                                           i_d44333                                , i_dd44321                               ;
input                                           i_d44334                                ;
output  logic unsigned      [0:0]               VDD                                     , VSS                                     ;

logic               [7:0]               dout                    = 8'hFF         ;
logic               [7:0]               douteeeeeeeeeeeeeeeeeee = 8'hFF         ;
logic               [`WIDTH-1:0]        data                                    ;

logic               [2:0]               a                                       , b                                   ;
//dd
/* ehlo */  // a, b, c
//
//
//
//
logic               [7:0]               data_out                                ;
logic                                   tt                                      ;
reg signed          [7:0]               kj                                      ;
// logic                                               c                           ;

// b
/* ehlo */  // logic           [2:0]                   d                           ;

static int                              a                                       ;
automatic int       [3:0]               b                                       ;

wire                [1:0]               addr                                    ;
logic                                   address                                 ;
logic               [7:0]               ddtt                                    ;
assign d    = a + 1;

memory u_memory (
    .address                (addr                   ),
    .data_in                (kj[2:0]                ),
    .data_out               (ddtt                   ),
    .read_write             (read_write             ),
    .chip_en                (tt                     ),
    .www3test               (                       )
);

memory u_mem1 (
    .address                (                   ),
    .data_in                (ddtt               ),
    .data_out               (                   ),
    .read_write             (                   ),
    .chip_en                (                   ),
    .www3test               (                   )
);

memory u_mem2 (
    .address                (               ),
    .data_in                (               ),
    .data_out               (               ),
    .read_write             (               ),
    .chip_en                (               )
);

memory u_mem5 (
    .address                (addr               ),
    .data_in                (                   ),
    .data_out               (kj                 ),
    .read_write             (                   ),
    .chip_en                (                   )
);

`ifdef RTL_SIM
memory u_mem3 (
    .address                (address                    ),
    .data_in                (kj[4:3]                    ),
    .data_out               (addr                       ),
    .read_write             (read_write                 ),
    .chip_en                (tt                         )
);
`else
memory u_mem4();
`endif

always_comb begin
    // tte
    a       = 3;
    // tte

    if (a == 3) begin
        a       += 1;
    end

    for (int i  = 0; i < 32; i++) begin
    end
    while (i < 5) begin
        $display("i = %0d", i);
        i++;
    end
    for (int i  = 0; i < 32; i++) begin
        while (i < 5) begin
            $display("i = %0d", i);
            i++;
        end
    end
    do begin
        $display("i = %0d", i);
        i++;
    end while (i < 5);

    foreach (arr[i]) begin
        $display("arr[%0d]  = %0d", i, arr[i]);
    end

    repeat (3) begin
        $display("Hello");
    end

    forever begin
        #10;
        $display("Tick at time %0t", $time);
    end
end

initial begin
    forever #5 clk  = ~clk;
    // 10 time-unit period
end

// Standard D-FF with synchronous active-low reset
always_ff @(posedge i_clk) begin
    if (data) data  <= 32'b0;
    else data       <= d;
end

endmodule
