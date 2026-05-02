// Test file for new lint features
module test_module;
  // Test naming patterns
  interface test_interface; endinterface
  struct { logic a; } test_struct;
  union logic [3:0] test_union;
  enum logic [1:0] { STATE_A, STATE_B } test_enum;
  parameter int TEST_PARAM = 1;
  localparam int LOCAL_PARAM = 2;

  // Test raw always (should trigger statement rule)
  always @(posedge clk) begin
    // Test incomplete if (latch detection)
    if (enable)
      q <= d;
    // missing else -> latch
  end

  // Test case missing default
  case (state)
    2'b00: out = 8'h01;
    2'b01: out = 8'h02;
    // missing default
  endcase

  // Test missing explicit begin
  if (condition)
    a = 1;
    b = 2;  // This line is not conditional but might be missed without begin/end

endmodule