module tb; reg clk = 0, rst = 1; reg signed [15:0] x; wire signed [15:0] y1, y2; integer i, s; integer seed = 7;
  tm_biquad dut(.clk(clk), .rst(rst), .x_in(x), .y1(y1), .y2(y2));
  always #5 clk = ~clk;
  initial begin x = 0; #12 rst = 0;
    for (i = 0; i < 64 * 16; i = i + 1) begin
      @(negedge clk); if (dut.slot == 4'd0) x = $random(seed) % 4000;
      if (dut.slot == 4'd0) $display("S %0d %0d %0d", x, y1, y2);
    end
    $finish; end
endmodule
