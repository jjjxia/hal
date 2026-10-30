// Folded MAC whose accumulator is cleared at the start of every sample (slot 0) by a synchronous clear.
// P = 4: acc <- 0 (slot 0), acc <- acc + c[phi]*x (slots 1..3), y <- acc in slot 0.
module top_module(input clk, input rst, input signed [7:0] x, output reg signed [15:0] y);
    reg [1:0] phi; reg signed [15:0] acc; reg signed [7:0] c;
    always @* case (phi) 2'd1: c = 8'sd3; 2'd2: c = 8'sd5; default: c = 8'sd7; endcase
    always @(posedge clk) begin
        if (rst) phi <= 0; else phi <= phi + 1;
        if (phi == 2'd0) acc <= 16'sd0; else acc <= acc + c * x;
        if (phi == 2'd0) y <= acc;
    end
endmodule
