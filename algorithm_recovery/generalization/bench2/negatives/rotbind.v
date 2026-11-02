// E2.4 N2: rotating binding (constructed negative case). One transaction = 3 cycles.
module rotbind(input clk, input rst, input start, input [15:0] a, input [15:0] b, output reg [15:0] y);
  reg [15:0] r0, r1;
  reg sel;
  reg [1:0] st;
  always @(posedge clk) begin
    if (rst) begin st <= 2'd0; sel <= 1'b0; end
    else case (st)
      2'd0: if (start) begin
              if (sel) begin r1 <= a + b; r0 <= a ^ b; end
              else     begin r0 <= a + b; r1 <= a ^ b; end
              st <= 2'd1;
            end
      2'd1: begin
              if (sel) r1 <= r1 + r0; else r0 <= r0 + r1;
              st <= 2'd2;
            end
      default: begin y <= sel ? r1 : r0; sel <= ~sel; st <= 2'd0; end
    endcase
  end
endmodule
