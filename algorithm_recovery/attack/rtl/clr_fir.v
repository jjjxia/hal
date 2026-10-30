// Folded 3-tap FIR, P = 4, accumulator cleared in slot 0 by a synchronous clear (yosys maps it to FDRE.R, CE = 1).
module top_module(input clk, input rst, input signed [7:0] x, output reg signed [15:0] y);
    reg [1:0] phi; reg signed [15:0] acc; reg signed [7:0] c, d0, d1, d2, op;
    always @* case (phi) 2'd1: begin c = 8'sd3; op = d0; end 2'd2: begin c = 8'sd5; op = d1; end default: begin c = 8'sd7; op = d2; end endcase
    always @(posedge clk) begin
        if (rst) phi <= 0; else phi <= phi + 1;
        if (phi == 2'd0) begin d0 <= x; d1 <= d0; d2 <= d1; end
        if (phi == 2'd0) acc <= 16'sd0; else acc <= acc + c * op;
        if (phi == 2'd0) y <= acc;
    end
endmodule
