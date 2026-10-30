// Held-out testbench: x from stim.txt (one value per cycle, applied before clock edge k at 5000 + 10000k ps),
// rst high for cycles 0..3; writes the y value present during each cycle k (after edge k-1) to y.txt.
`timescale 1ps/1ps
module tb;
    reg clk = 0, rst = 1; reg signed [15:0] x = 0; wire [15:0] y;
    integer k, ncyc, f, g, r, v;
    biquad2_folded dut(.clk(clk), .rst(rst), .x(x), .y(y));
    always #5000 clk = ~clk;
    initial begin
        if (!$value$plusargs("ncyc=%d", ncyc)) ncyc = 100;
        f = $fopen("stim.txt", "r"); g = $fopen("y.txt", "w");
        for (k = 0; k < ncyc; k = k + 1) begin
            r = $fscanf(f, "%d\n", v); x = v; rst = (k < 4);
            #4000; $fwrite(g, "%0d\n", y);   // value during cycle k
            #6000;
        end
        $fclose(g); $finish;
    end
endmodule
