`include "params.svh"
`define WIDTH 32
`define print_bytes(ARR, STARTBYTE, NUMBYTES) \
    for (int ii=STARTBYTE; ii<STARTBYTE+NUMBYTES; ii++) begin \
        if ((ii != 0) && (ii % 16 == 0)) \
            $display("\n"); \
        $display("0x%x ", ARR[ii]); \
    end

typedef struct {
    logic                 [7:0]                         addr                            ;
    logic                                               valid                           ;
} packet_wo_data_t;

typedef struct {
    logic signed          [7:0]                         addr                            ;
    logic                 [31:0]                        data                            ;
    logic                                               valid                           ;
} packet_t;

function packet_t sum(input i_a, input i_b);
    return packet_t'(i_a + i_b);
endfunction

task add_numbers(input int a, input int b, output int result);
    result  = a + b;
endtask

module memory_top(i_clk, i_data, i_data2, i_data3, i_dd, i_dd22222, i_d33333, i_d44333, i_dd44321, i_d44334, VDD, VSS)      ;
input     packet_t                                             i_clk                                                        ;
input     logic signed                     [7:0]               i_data                        [7:0]                          ;
input     var byte                                             i_data2                                                      ;
input                                                          i_data3                                                      ;
input                                                          i_dd                                                         ;
input                                                          i_dd22222                                                    ;
input                                                          i_d33333                                                     ;
input                                                          i_d44333                                                    , i_dd44321;
input                                                          i_d44334                                                     ;
output    logic unsigned                   [0:0]               VDD                           [0:0] = 1'b1                  , VSS                           [0:0] = 1'b0;

logic                 [7:0]                         dout                          = 8'hFF           ;
logic unsigned        [0:0]                         VDD                           [0:0] = 1,     VSS                           [0:0] = 0       ;
logic                 [`WIDTH-1:0]                  data                          [0:0] = 1         ;
// a, b, c
logic                 [2:0]                         a                             ,              b                             /*asdfasdfsdf*/ ;
//dd
/* ehlo */  // a, b, c
//
//
//
//
logic                 [7:0]                         data_out                      ;
// logic                                               c                           ;

// b
/* ehlo */  // logic           [2:0]                   d                           ;
static int a;

assign d    = a + 1;

memory u_memory (
    .address                (addr                   ),
    .data_in                (data_in                ),
    .data_out               (dout                   ),
    .read_write             (read_write             ),
    .chip_en                (chip_en                )
);

`ifdef RTL_SIM
memory u_mem3();
`else
memory u_mem4();
`endif

always_comb begin
    // tte
    a       = 3;
    // tte
    c       = sum(.i_a(i_a), .i_b(i_b));
    // tte
    d       = {1'b0, a} ;
    tddd    = 3         ;
    ddddddd = 3         ;

    if (a == 3) begin
        tdddddddddddddddd   = 3 ;
        ddddddd             = 3 ;
    end
    else begin
        tddd    += 4;
    end

    for (int i  = 0; i < 32; i++) begin
    end
    while (i < 5) begin
        $display("i = %0d", i)  ;
        i++                     ;
    end
    for (int i  = 0; i < 32; i++) begin
        while (i < 5) begin
            $display("i = %0d", i)  ;
            i++                     ;
        end
    end
    do begin
        $display("i = %0d", i)  ;
        i++                     ;
    end while (i < 5);

    foreach (arr[i]) begin
        $display("arr[%0d]  = %0d", i, arr[i]);
    end

    repeat (3) begin
        $display("Hello");
    end

    forever begin
        #10                                 ;
        $display("Tick at time %0t", $time) ;
    end
end

initial begin
    forever #5 clk  = ~clk;
    // 10 time-unit period
end

endmodule
