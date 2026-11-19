`default_nettype none
// AXI-Stream handshake rule (buildplan §8.3), shared by the formal harnesses: once a
// source raises valid it holds valid and its payload until ready (AXI4-Stream §2.2.1).
// ASSUME = 1 constrains an input to the DUT; 0 asserts a DUT output.
module axis_hold #(
    parameter int W      = 8,
    parameter bit ASSUME = 0
) (
    input wire logic         clk_i,
    input wire logic         rst_i,
    input wire logic         valid_i,
    input wire logic         ready_i,
    input wire logic [W-1:0] payload_i
);
    logic         past = 1'b0, stalled_q;
    logic [W-1:0] payload_q;
    always_ff @(posedge clk_i) begin
        past      <= 1'b1;
        stalled_q <= !rst_i && valid_i && !ready_i;
        payload_q <= payload_i;
    end
    always_comb if (past && stalled_q && !rst_i) begin
        if (ASSUME) assume (valid_i && payload_i == payload_q);
        else        assert (valid_i && payload_i == payload_q);
    end
endmodule
`default_nettype wire
