`default_nettype none
// feed_frontend: UDP payloads (MoldUDP64 packets) -> decoded book records for one symbol.
//   mold_deframer (ADR 0003) -> itch_decoder (ADR 0004)
// Golden model: model/moldudp64.py FeedDecoder.
//
// Ports are the union of the two blocks'; see their headers for meanings. Status pulses
// map to the model's counters as:
//   pkt_o heartbeat_o end_o gap_o(+gap_msgs_o) dup_o session_change_o malformed_pkt_o
//     -> SeqCounters (packets, heartbeats, end_of_session, gaps, gap_messages,
//        duplicates, session_changes, malformed_packets)
//   unknown_o other_locate_o dir_match_o locate_change_o -> FeedCounters
//   malformed_msg_o + empty_msg_o -> FeedCounters.malformed_messages
//   stale_o = SeqTracker.stale || any malformed message (FeedDecoder.stale)
// Latency: 1 cycle (deframer) + 1 cycle (decoder) from a message's last byte at the
// input to its record. Reset: synchronous, active-high.
module feed_frontend #(
    parameter int DATA_W = 8
) (
    input  wire logic              clk_i,
    input  wire logic              rst_i,
    input  wire logic [63:0]       cfg_symbol_i,
    input  wire logic              cfg_locate_valid_i,
    input  wire logic [15:0]       cfg_locate_i,
    // packets in
    input  wire logic [DATA_W-1:0] s_tdata_i,
    input  wire logic              s_tvalid_i,
    output logic                   s_tready_o,
    input  wire logic              s_tlast_i,
    input  wire logic              s_tuser_i,
    input  wire logic [63:0]       s_t0_i,     // T0 of the current packet's frame (ADR 0011)
    input  wire logic [63:0]       now_i,      // timestamp counter
    // records out
    output logic                   m_valid_o,
    input  wire logic              m_ready_i,
    output logic [7:0]             m_type_o,
    output logic [63:0]            m_seq_o,
    output logic [47:0]            m_ts_o,
    output logic [63:0]            m_ref_o,
    output logic [63:0]            m_new_ref_o,
    output logic                   m_sell_o,
    output logic [31:0]            m_shares_o,
    output logic [31:0]            m_price_o,
    output logic [63:0]            m_t0_o,
    output logic [63:0]            m_t1_o,     // T1: now_i at the message's last byte
    // status
    output logic                   t1_o,
    output logic                   pkt_o,
    output logic                   heartbeat_o,
    output logic                   end_o,
    output logic                   gap_o,
    output logic [63:0]            gap_msgs_o,
    output logic                   dup_o,
    output logic                   session_change_o,
    output logic                   malformed_pkt_o,
    output logic                   empty_msg_o,
    output logic                   unknown_o,
    output logic                   malformed_msg_o,
    output logic                   other_locate_o,
    output logic                   dir_match_o,
    output logic                   locate_change_o,
    output logic                   locate_valid_o,
    output logic [15:0]            locate_o,
    output logic                   stale_o
);
    logic [DATA_W-1:0] g_tdata;
    logic              g_tvalid, g_tready, g_tlast, g_tuser;
    logic [63:0]       g_seq, g_t0;
    logic              seq_stale, msg_bad_q;

    mold_deframer #(
        .DATA_W (DATA_W)
    ) u_deframer (
        .clk_i            (clk_i),
        .rst_i            (rst_i),
        .s_tdata_i        (s_tdata_i),
        .s_tvalid_i       (s_tvalid_i),
        .s_tready_o       (s_tready_o),
        .s_tlast_i        (s_tlast_i),
        .s_tuser_i        (s_tuser_i),
        .s_t0_i           (s_t0_i),
        .m_tdata_o        (g_tdata),
        .m_tvalid_o       (g_tvalid),
        .m_tready_i       (g_tready),
        .m_tlast_o        (g_tlast),
        .m_tuser_o        (g_tuser),
        .m_seq_o          (g_seq),
        .m_t0_o           (g_t0),
        .pkt_o            (pkt_o),
        .heartbeat_o      (heartbeat_o),
        .end_o            (end_o),
        .gap_o            (gap_o),
        .gap_msgs_o       (gap_msgs_o),
        .dup_o            (dup_o),
        .session_change_o (session_change_o),
        .malformed_o      (malformed_pkt_o),
        .empty_msg_o      (empty_msg_o),
        .stale_o          (seq_stale)
    );

    itch_decoder #(
        .DATA_W (DATA_W)
    ) u_decoder (
        .clk_i              (clk_i),
        .rst_i              (rst_i),
        .cfg_symbol_i       (cfg_symbol_i),
        .cfg_locate_valid_i (cfg_locate_valid_i),
        .cfg_locate_i       (cfg_locate_i),
        .s_tdata_i          (g_tdata),
        .s_tvalid_i         (g_tvalid),
        .s_tready_o         (g_tready),
        .s_tlast_i          (g_tlast),
        .s_tuser_i          (g_tuser),
        .s_seq_i            (g_seq),
        .s_t0_i             (g_t0),
        .now_i              (now_i),
        .m_valid_o          (m_valid_o),
        .m_ready_i          (m_ready_i),
        .m_type_o           (m_type_o),
        .m_seq_o            (m_seq_o),
        .m_ts_o             (m_ts_o),
        .m_ref_o            (m_ref_o),
        .m_new_ref_o        (m_new_ref_o),
        .m_sell_o           (m_sell_o),
        .m_shares_o         (m_shares_o),
        .m_price_o          (m_price_o),
        .m_t0_o             (m_t0_o),
        .m_t1_o             (m_t1_o),
        .t1_o               (t1_o),
        .unknown_o          (unknown_o),
        .malformed_o        (malformed_msg_o),
        .other_locate_o     (other_locate_o),
        .dir_match_o        (dir_match_o),
        .locate_change_o    (locate_change_o),
        .locate_valid_o     (locate_valid_o),
        .locate_o           (locate_o)
    );

    // A malformed or empty message also makes the book untrustworthy (sticky).
    always_ff @(posedge clk_i) begin
        if (rst_i)                              msg_bad_q <= 1'b0;
        else if (malformed_msg_o || empty_msg_o) msg_bad_q <= 1'b1;
    end

    assign stale_o = seq_stale || msg_bad_q;
endmodule
`default_nettype wire
