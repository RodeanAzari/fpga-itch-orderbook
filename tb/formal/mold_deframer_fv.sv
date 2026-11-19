`default_nettype none
// Formal harness for mold_deframer_w8 (mold_deframer at DATA_W 8) (buildplan §8.3, MoldUDP64 sequence and
// gap logic; ADR 0003). The sequence properties sit in mold_deframer_w8 under FORMAL
// (a shadow of model SeqTracker.feed). This harness gives every input freely (bytes,
// tlast, tuser, valid, ready, mid-run resets), starts in reset, and checks the output
// AXI-Stream handshake.
module mold_deframer_fv (
    input wire logic        clk_i,
    input wire logic        rst_i,
    input wire logic [7:0]  s_tdata_i,
    input wire logic        s_tvalid_i,
    input wire logic        s_tlast_i,
    input wire logic        s_tuser_i,
    input wire logic [63:0] s_t0_i,
    input wire logic        s_stale_i,
    input wire logic        m_tready_i
);
    logic        past = 1'b0;
    always_ff @(posedge clk_i) past <= 1'b1;
    always_comb if (!past) assume (rst_i);

    logic        s_tready, m_tvalid, m_tlast, m_tuser, m_stale;
    logic [7:0]  m_tdata;
    logic [63:0] m_seq, m_t0, gap_msgs;
    logic        pkt, hb, eos, gap, dup, sc, mal, empty, stale;

    mold_deframer_w8 #(.DATA_W(8)) dut (
        .clk_i, .rst_i,
        .s_tdata_i, .s_tvalid_i, .s_tready_o(s_tready), .s_tlast_i,
        .s_tuser_i, .s_t0_i, .s_stale_i,
        .m_tdata_o(m_tdata), .m_tvalid_o(m_tvalid), .m_tready_i,
        .m_tlast_o(m_tlast), .m_tuser_o(m_tuser), .m_seq_o(m_seq), .m_t0_o(m_t0),
        .m_stale_o(m_stale),
        .pkt_o(pkt), .heartbeat_o(hb), .end_o(eos), .gap_o(gap), .gap_msgs_o(gap_msgs),
        .dup_o(dup), .session_change_o(sc), .malformed_o(mal), .empty_msg_o(empty),
        .stale_o(stale)
    );

    axis_hold #(.W(8 + 1 + 1 + 64 + 64 + 1)) u_m_hold (
        .clk_i, .rst_i, .valid_i(m_tvalid), .ready_i(m_tready_i),
        .payload_i({m_tdata, m_tlast, m_tuser, m_seq, m_t0, m_stale})
    );
    always_comb if (past && !rst_i && m_tuser) assert (!m_tvalid || m_tlast);

    // Not vacuous: each verdict, and a message after a gap.
    always_comb if (past && !rst_i) begin
        cover (gap && gap_msgs > 64'd1);
        cover (dup);
        cover (sc);
        cover (hb);
        cover (eos);
        cover (mal);
        cover (m_tvalid && m_tlast && stale);
    end
endmodule
`default_nettype wire
