// E2.4 N3: hidden pipeline (constructed negative case). One transaction = 3 cycles.
// The output of transaction t uses the sum computed in transaction t-1.
module hidpipe(input clk, input rst, input start, input [15:0] a, input [15:0] b, output reg [15:0] y);
  reg [15:0] p, q;
  reg [1:0] st;
  always @(posedge clk) begin
    if (rst) begin st <= 2'd0; p <= 16'd0; end
    else case (st)
      2'd0: if (start) begin q <= a ^ b; st <= 2'd1; end
      2'd1: begin y <= p + q; p <= a + b; st <= 2'd2; end
      default: st <= 2'd0;
    endcase
  end
endmodule
