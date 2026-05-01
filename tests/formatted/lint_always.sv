module always_test(input logic clk, input logic rst_n, input logic sel, output logic q, output logic y);
always_ff @(posedge clk) begin
    q       <= 1'b0;
end

always_comb begin
    if (sel) y  = 1'b1;
end
endmodule
