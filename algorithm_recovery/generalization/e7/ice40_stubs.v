(* blackbox *) module SB_LUT4 (output O, input I0, I1, I2, I3); parameter [15:0] LUT_INIT = 0; endmodule
(* blackbox *) module SB_CARRY (output CO, input I0, I1, CI); endmodule
(* blackbox *) module SB_DFFE (output Q, input C, E, D); endmodule
(* blackbox *) module SB_DFFER (output Q, input C, E, R, D); endmodule
(* blackbox *) module SB_DFFES (output Q, input C, E, S, D); endmodule
(* blackbox *) module SB_DFFR (output Q, input C, R, D); endmodule
(* blackbox *) module SB_GB (input USER_SIGNAL_TO_GLOBAL_BUFFER, output GLOBAL_BUFFER_OUTPUT); endmodule
(* blackbox *) module SB_IO (inout PACKAGE_PIN, input LATCH_INPUT_VALUE, CLOCK_ENABLE, INPUT_CLK, OUTPUT_CLK, OUTPUT_ENABLE, D_OUT_0, D_OUT_1, output D_IN_0, D_IN_1);
  parameter [5:0] PIN_TYPE = 0; parameter [0:0] PULLUP = 0; parameter [0:0] NEG_TRIGGER = 0; parameter IO_STANDARD = "SB_LVCMOS"; endmodule
(* blackbox *) module SB_GB_IO (inout PACKAGE_PIN, output GLOBAL_BUFFER_OUTPUT, input LATCH_INPUT_VALUE, CLOCK_ENABLE, INPUT_CLK, OUTPUT_CLK, OUTPUT_ENABLE, D_OUT_0, D_OUT_1, output D_IN_0, D_IN_1);
  parameter [5:0] PIN_TYPE = 0; parameter [0:0] PULLUP = 0; parameter [0:0] NEG_TRIGGER = 0; parameter IO_STANDARD = "SB_LVCMOS"; endmodule
