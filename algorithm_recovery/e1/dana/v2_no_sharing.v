// V2: the same two biquads and the same 16-slot schedule, but each filter has its own operand multiplexer,
// multiplier, accumulator and temporary (no shared datapath).
module tm_biquad (input clk, input rst, input signed [15:0] x_in, output reg signed [15:0] y1, output reg signed [15:0] y2);
  reg [3:0] slot;
  reg signed [31:0] acc1, acc2;
  reg signed [15:0] w1, w2, st0, st1, st2, st3;
  localparam signed [15:0] B10 = 16'sd2048, B11 = 16'sd4096, B12 = 16'sd2048, A11 = -16'sd18000, A12 = 16'sd6000;
  localparam signed [15:0] B20 = 16'sd4096, B21 = -16'sd8192, B22 = 16'sd4096, A21 = -16'sd25000, A22 = 16'sd10000;
  reg signed [15:0] c1, o1, c2, o2;
  always @* begin
    case (slot)
      4'd0: begin c1 = 16'sd16384; o1 = x_in; end  4'd1: begin c1 = -A11; o1 = st0; end  4'd2: begin c1 = -A12; o1 = st1; end
      4'd4: begin c1 = B10; o1 = w1; end           4'd5: begin c1 = B11; o1 = st0; end   4'd6: begin c1 = B12; o1 = st1; end
      default: begin c1 = 16'sd0; o1 = 16'sd0; end
    endcase
    case (slot)
      4'd8: begin c2 = 16'sd16384; o2 = y1; end    4'd9: begin c2 = -A21; o2 = st2; end  4'd10: begin c2 = -A22; o2 = st3; end
      4'd12: begin c2 = B20; o2 = w2; end          4'd13: begin c2 = B21; o2 = st2; end  4'd14: begin c2 = B22; o2 = st3; end
      default: begin c2 = 16'sd0; o2 = 16'sd0; end
    endcase
  end
  wire signed [31:0] p1 = c1 * o1, p2 = c2 * o2;
  always @(posedge clk) begin
    if (rst) begin slot <= 0; acc1 <= 0; acc2 <= 0; w1 <= 0; w2 <= 0; st0 <= 0; st1 <= 0; st2 <= 0; st3 <= 0; y1 <= 0; y2 <= 0; end
    else begin
      slot <= slot + 4'd1;
      case (slot) 4'd0, 4'd4: acc1 <= p1; 4'd1, 4'd2, 4'd5, 4'd6: acc1 <= acc1 + p1; default: ; endcase
      case (slot) 4'd8, 4'd12: acc2 <= p2; 4'd9, 4'd10, 4'd13, 4'd14: acc2 <= acc2 + p2; default: ; endcase
      case (slot)
        4'd3: w1 <= acc1 >>> 14;
        4'd7: begin y1 <= acc1 >>> 14; st1 <= st0; st0 <= w1; end
        4'd11: w2 <= acc2 >>> 14;
        4'd15: begin y2 <= acc2 >>> 14; st3 <= st2; st2 <= w2; end
        default: ;
      endcase
    end
  end
endmodule
