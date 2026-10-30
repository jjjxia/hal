// E1 design: two cascaded direct-form-II biquads folded onto one multiplier, one adder and one accumulator, with
// the filter state in ONE shared 5-register chain (S1 <= ACC, Si <= S(i-1), shifted only in slots 2 and 7).
// Schedule (phi = slot, P = 10):
//   0: ACC <= x        + a11*S2     (S2 = v[n-1])        5: ACC <= ACC + a21*S2 (s[n-1])
//   1: ACC <= ACC      + a12*S4     (v[n-2]) -> v[n]     6: ACC <= ACC + a22*S4 (s[n-2]) -> s[n]
//   2: ACC <=            b10*ACC    shift chain (v[n])   7: ACC <=       b20*ACC  shift chain (s[n])
//   3: ACC <= ACC      + b11*S3     (v[n-1])             8: ACC <= ACC + b21*S3 (s[n-1])
//   4: ACC <= ACC      + b12*S5     (v[n-2]) -> u[n]     9: ACC <= ACC + b22*S5 (s[n-2]) -> y[n]
// Y <= ACC[19:4] in slot 0. Products are (c * operand) >>> 10, coefficients Q1.10.
module biquad2_folded (input wire clk, input wire rst, input wire signed [15:0] x, output reg [15:0] y);
    reg [3:0] phi;
    reg signed [23:0] acc;
    reg signed [23:0] s1, s2, s3, s4, s5;
    reg signed [11:0] coef;
    reg signed [23:0] opnd, base;
    reg we;
    always @(*) begin
        coef = 12'sd0; opnd = acc; base = acc; we = 1'b0;
        case (phi)
            4'd0: begin coef =  12'sd1280; opnd = s2;  base = {{8{x[15]}}, x}; end
            4'd1: begin coef = -12'sd576;  opnd = s4;  end
            4'd2: begin coef =  12'sd320;  opnd = acc; base = 24'sd0; we = 1'b1; end
            4'd3: begin coef = -12'sd704;  opnd = s3;  end
            4'd4: begin coef =  12'sd448;  opnd = s5;  end
            4'd5: begin coef = -12'sd384;  opnd = s2;  end
            4'd6: begin coef = -12'sd256;  opnd = s4;  end
            4'd7: begin coef =  12'sd896;  opnd = acc; base = 24'sd0; we = 1'b1; end
            4'd8: begin coef =  12'sd192;  opnd = s3;  end
            4'd9: begin coef = -12'sd512;  opnd = s5;  end
            default: ;
        endcase
    end
    wire signed [35:0] p = coef * opnd;
    wire signed [23:0] prod = p[33:10];
    always @(posedge clk) begin
        if (rst) begin
            phi <= 4'd0; acc <= 24'sd0; y <= 16'd0;
            s1 <= 24'sd0; s2 <= 24'sd0; s3 <= 24'sd0; s4 <= 24'sd0; s5 <= 24'sd0;
        end else begin
            phi <= (phi == 4'd9) ? 4'd0 : phi + 4'd1;
            acc <= base + prod;
            if (we) begin s1 <= acc; s2 <= s1; s3 <= s2; s4 <= s3; s5 <= s4; end
            if (phi == 4'd0) y <= acc[19:4];
        end
    end
endmodule
