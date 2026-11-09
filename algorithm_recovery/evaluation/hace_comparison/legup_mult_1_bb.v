(* blackbox *)
module legup_mult_1(clock, aclr, sum, clken, dataa, datab, result);
  parameter widtha = 32; parameter widthb = 32; parameter widthp = 64; parameter representation = "UNSIGNED";
  input clock, aclr, sum, clken; input [widtha-1:0] dataa; input [widthb-1:0] datab; output [widthp-1:0] result;
endmodule
