// Testbench: clock edges at 5000 + 10000k ps (the convention of the HAL guided-SSE scripts); rst for 4 cycles;
// x changes every cycle (pseudo-random, seed from +seed), so only the slot-0 samples matter.
`timescale 1ps/1ps
module tb;
    reg clk = 0, rst = 1; reg signed [15:0] x = 0; wire [15:0] y;
    integer k, seed, ncyc, f;
    biquad2_folded dut(.clk(clk), .rst(rst), .x(x), .y(y));
    always #5000 clk = ~clk;
    initial begin
        if (!$value$plusargs("seed=%d", seed)) seed = 1;
        if (!$value$plusargs("ncyc=%d", ncyc)) ncyc = 400;
        $dumpfile("sim.vcd"); $dumpvars(0, tb);
        f = $fopen("io.txt", "w");
        for (k = 0; k < ncyc; k = k + 1) begin
            #1000; if (k == 4) rst = 0;
            x = $random(seed) >>> 18;   // small amplitude keeps the cascade mostly out of wraparound
            #9000; $fwrite(f, "%0d %0d %0d\n", k, x, y);
        end
        $fclose(f); $finish;
    end
endmodule
