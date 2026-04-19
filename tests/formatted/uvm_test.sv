`include "uvm_macros.svh"

import uvm_pkg::*;

class my_driver extends uvm_driver #(my_seq_item);
    `uvm_component_utils(my_driver)

    function new (string name, uvm_component parent);
        super.new (name, parent);
    endfunction

    task run_phase(uvm_phase phase);
        my_seq_item req;
        forever begin
            seq_item_port.get_next_item(req);
            `uvm_info(get_type_name(), $sformatf("Driving: addr=%0h", req.addr), UVM_LOW);
            seq_item_port.item_done();
        end
    endtask
endclass

module tb_top;
    logic clk;
    logic rst_n;

    my_dut dut_inst (
                .clk            (clk            ),
                .rst_n          (rst_n          )
    );

    initial clk      = 0;
    always #5 clk    = ~clk;

    initial begin
        rst_n = 0;
        `uvm_info("TB_TOP", "Applying reset", UVM_NONE);
        #20 rst_n = 1;
        `uvm_info("TB_TOP", "Reset released", UVM_LOW);
        run_test();
    end
endmodule
