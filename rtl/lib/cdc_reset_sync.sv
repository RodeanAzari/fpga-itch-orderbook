`default_nettype none
// cdc_reset_sync: brings a reset request (any domain, or a pin) into clk_i's domain.
// Assertion and release both pass through STAGES flops, so rst_o is a clean synchronous,
// active-high reset. Listed CDC (docs/design.md): level into an ASYNC_REG chain, false
// path to stage 0 (constr/arty_timing.xdc).
// rst_o only changes while clk_i runs: a domain whose clock is absent (the PHY's clocks
// while the PHY is in reset) leaves reset STAGES edges after its clock starts.
// Latency: STAGES cycles each way.
module cdc_reset_sync #(
    parameter int STAGES = 3
) (
    input  wire logic clk_i,
    input  wire logic rst_req_i,
    output logic      rst_o
);
    // Power-up value (FPGA configuration init) instead of a reset: this block makes the
    // reset, so it has none to use. Starts asserted.
    /* verilator lint_off PROCASSINIT */
    (* ASYNC_REG = "TRUE" *) logic [STAGES-1:0] sync_q = '1;
    /* verilator lint_on PROCASSINIT */

    always_ff @(posedge clk_i) sync_q <= {sync_q[STAGES-2:0], rst_req_i};

    assign rst_o = sync_q[STAGES-1];
endmodule
`default_nettype wire
