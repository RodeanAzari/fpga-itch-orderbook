`default_nettype none
// pkt_gate: passes or drops whole AXI-Stream packets while a consumer is held in reset.
//
// block_i is the consumer's reset. While it is high, every input beat is accepted and
// dropped (the source is drained, never stalled: the feed shares eth_rx with the control
// port). A packet passes only if block_i was low on every cycle from its first beat to
// its last, so the consumer never sees the tail of a packet it did not see start. A packet
// that block_i interrupts is dropped to its end, even after block_i falls; the consumer's
// reset discards what it had received of it.
// Latency: 0 cycles (combinational pass-through). Reset: synchronous, active-high.
module pkt_gate #(
    parameter int DATA_W = 8
) (
    input  wire logic              clk_i,
    input  wire logic              rst_i,
    input  wire logic              block_i,
    input  wire logic [DATA_W-1:0] s_tdata_i,
    input  wire logic              s_tvalid_i,
    output logic                   s_tready_o,
    input  wire logic              s_tlast_i,
    input  wire logic              s_tuser_i,
    output logic [DATA_W-1:0]      m_tdata_o,
    output logic                   m_tvalid_o,
    input  wire logic              m_tready_i,
    output logic                   m_tlast_o,
    output logic                   m_tuser_o
);
    logic mid_q, pass_q, pass;

    // a new packet passes if not blocked; one in flight passes if it always did
    assign pass       = !block_i && (mid_q ? pass_q : 1'b1);
    assign s_tready_o = pass ? m_tready_i : 1'b1;
    assign m_tvalid_o = s_tvalid_i && pass;
    assign m_tdata_o  = s_tdata_i;
    assign m_tlast_o  = s_tlast_i;
    assign m_tuser_o  = s_tuser_i;

    always_ff @(posedge clk_i) begin
        if (s_tvalid_i && s_tready_o) begin
            mid_q  <= !s_tlast_i;
            pass_q <= pass;
        end else if (block_i) begin
            pass_q <= 1'b0;
        end
        if (rst_i) begin
            mid_q  <= 1'b0;
            pass_q <= 1'b0;
        end
    end
endmodule
`default_nettype wire
