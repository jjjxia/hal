// E1: two direct-form-II biquads in cascade, time-multiplexed on ONE multiply-accumulate unit.
// Shared across both filters: the accumulator acc, the temporary w, the multiplier, the coefficient ROM.
// State: st[0..3] = w1[n-1], w1[n-2], w2[n-1], w2[n-2]. One input sample every 16 cycles (slot 0).
// Fixed point: Q2.14 coefficients, 16-bit samples, 32-bit accumulator; F = 14.
module tm_biquad (input clk, input rst, input signed [15:0] x_in, output reg signed [15:0] y1, output reg signed [15:0] y2);
  reg [3:0] slot;
  reg signed [31:0] acc;
  reg signed [15:0] w;
  reg signed [15:0] st0, st1, st2, st3;
  // coefficients (biquad 1: b10 b11 b12 / a11 a12 ; biquad 2: b20 b21 b22 / a21 a22), Q2.14
  localparam signed [15:0] B10 = 16'sd2048, B11 = 16'sd4096, B12 = 16'sd2048, A11 = -16'sd18000, A12 = 16'sd6000;
  localparam signed [15:0] B20 = 16'sd4096, B21 = -16'sd8192, B22 = 16'sd4096, A21 = -16'sd25000, A22 = 16'sd10000;
  reg signed [15:0] c, opnd;
  always @* begin
    case (slot)
      4'd0:  begin c = 16'sd16384; opnd = x_in; end   // acc = x
      4'd1:  begin c = -A11;      opnd = st0;  end   // acc += -a1 * w1[n-1]
      4'd2:  begin c = -A12;      opnd = st1;  end   // acc += -a2 * w1[n-2]
      4'd4:  begin c = B10;       opnd = w;    end   // acc = b0 * w1[n]
      4'd5:  begin c = B11;       opnd = st0;  end
      4'd6:  begin c = B12;       opnd = st1;  end
      4'd8:  begin c = 16'sd16384; opnd = y1;  end   // biquad 2 input = y1
      4'd9:  begin c = -A21;      opnd = st2;  end
      4'd10: begin c = -A22;      opnd = st3;  end
      4'd12: begin c = B20;       opnd = w;    end
      4'd13: begin c = B21;       opnd = st2;  end
      4'd14: begin c = B22;       opnd = st3;  end
      default: begin c = 16'sd0;  opnd = 16'sd0; end
    endcase
  end
  wire signed [31:0] prod = c * opnd;
  always @(posedge clk) begin
    if (rst) begin slot <= 0; acc <= 0; w <= 0; st0 <= 0; st1 <= 0; st2 <= 0; st3 <= 0; y1 <= 0; y2 <= 0; end
    else begin
      slot <= slot + 4'd1;
      case (slot)
        4'd0, 4'd4, 4'd8, 4'd12: acc <= prod;            // start a sum
        4'd1, 4'd2, 4'd5, 4'd6, 4'd9, 4'd10, 4'd13, 4'd14: acc <= acc + prod;
        default: ;
      endcase
      case (slot)
        4'd3:  w <= acc >>> 14;                          // w1[n]
        4'd7:  begin y1 <= acc >>> 14; st1 <= st0; st0 <= w; end
        4'd11: w <= acc >>> 14;                          // w2[n]
        4'd15: begin y2 <= acc >>> 14; st3 <= st2; st2 <= w; end
        default: ;
      endcase
    end
  end
endmodule
