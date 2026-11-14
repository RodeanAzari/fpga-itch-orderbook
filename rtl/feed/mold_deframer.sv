`default_nettype none
// mold_deframer: MoldUDP64 packets (UDP payloads) -> ITCH messages, with the sequence
// semantics of ADR 0003. Golden model: model/moldudp64.py SeqTracker.
//
// Selects the implementation by DATA_W (ADR 0005, 0015); behaviour, status pulses and
// sequence rules are the same at both widths and documented in each:
//   DATA_W = 8:  mold_deframer_w8  (one byte per beat; tkeep is always 1)
//   DATA_W = 64: mold_deframer_w64 (8 byte lanes; messages start at any lane)
// Any other width stops elaboration.
//
// tkeep: lane i = byte i of the beat (lane 0 first on the wire). Input beats are full
// except a packet's last, whose tkeep is contiguous from lane 0. Output beats hold bytes
// of one message, contiguous lanes; only a message's first beat may start above lane 0
// and only its last may end below the top lane.
// Latency: see the selected implementation.
module mold_deframer #(
    parameter int DATA_W = 8
) (
    input  wire logic                clk_i,
    input  wire logic                rst_i,
    // packets in
    input  wire logic [DATA_W-1:0]   s_tdata_i,
    input  wire logic [DATA_W/8-1:0] s_tkeep_i,
    input  wire logic                s_tvalid_i,
    output logic                     s_tready_o,
    input  wire logic                s_tlast_i,
    input  wire logic                s_tuser_i,
    input  wire logic [63:0]         s_t0_i,
    input  wire logic                s_stale_i,  // in-band stale (ADR 0016)
    // messages out
    output logic [DATA_W-1:0]        m_tdata_o,
    output logic [DATA_W/8-1:0]      m_tkeep_o,
    output logic                     m_tvalid_o,
    input  wire logic                m_tready_i,
    output logic                     m_tlast_o,
    output logic                     m_tuser_o,
    output logic [63:0]              m_seq_o,
    output logic [63:0]              m_t0_o,
    output logic                     m_stale_o,
    // status
    output logic                     pkt_o,
    output logic                     heartbeat_o,
    output logic                     end_o,
    output logic                     gap_o,
    output logic [63:0]              gap_msgs_o,
    output logic                     dup_o,
    output logic                     session_change_o,
    output logic                     malformed_o,
    output logic                     empty_msg_o,
    output logic                     stale_o
);
    if (DATA_W == 8) begin : g_w8
        // One lane: every accepted beat holds its byte.
        logic unused_keep;
        assign unused_keep = &{1'b0, s_tkeep_i};
        assign m_tkeep_o   = '1;

        mold_deframer_w8 #(
            .DATA_W (DATA_W)
        ) u_impl (
            .clk_i            (clk_i),
            .rst_i            (rst_i),
            .s_tdata_i        (s_tdata_i),
            .s_tvalid_i       (s_tvalid_i),
            .s_tready_o       (s_tready_o),
            .s_tlast_i        (s_tlast_i),
            .s_tuser_i        (s_tuser_i),
            .s_t0_i           (s_t0_i),
            .s_stale_i        (s_stale_i),
            .m_tdata_o        (m_tdata_o),
            .m_tvalid_o       (m_tvalid_o),
            .m_tready_i       (m_tready_i),
            .m_tlast_o        (m_tlast_o),
            .m_tuser_o        (m_tuser_o),
            .m_seq_o          (m_seq_o),
            .m_t0_o           (m_t0_o),
            .m_stale_o        (m_stale_o),
            .pkt_o            (pkt_o),
            .heartbeat_o      (heartbeat_o),
            .end_o            (end_o),
            .gap_o            (gap_o),
            .gap_msgs_o       (gap_msgs_o),
            .dup_o            (dup_o),
            .session_change_o (session_change_o),
            .malformed_o      (malformed_o),
            .empty_msg_o      (empty_msg_o),
            .stale_o          (stale_o)
        );
    end else if (DATA_W == 64) begin : g_w64
        mold_deframer_w64 u_impl (
            .clk_i            (clk_i),
            .rst_i            (rst_i),
            .s_tdata_i        (s_tdata_i),
            .s_tkeep_i        (s_tkeep_i),
            .s_tvalid_i       (s_tvalid_i),
            .s_tready_o       (s_tready_o),
            .s_tlast_i        (s_tlast_i),
            .s_tuser_i        (s_tuser_i),
            .s_t0_i           (s_t0_i),
            .s_stale_i        (s_stale_i),
            .m_tdata_o        (m_tdata_o),
            .m_tkeep_o        (m_tkeep_o),
            .m_tvalid_o       (m_tvalid_o),
            .m_tready_i       (m_tready_i),
            .m_tlast_o        (m_tlast_o),
            .m_tuser_o        (m_tuser_o),
            .m_seq_o          (m_seq_o),
            .m_t0_o           (m_t0_o),
            .m_stale_o        (m_stale_o),
            .pkt_o            (pkt_o),
            .heartbeat_o      (heartbeat_o),
            .end_o            (end_o),
            .gap_o            (gap_o),
            .gap_msgs_o       (gap_msgs_o),
            .dup_o            (dup_o),
            .session_change_o (session_change_o),
            .malformed_o      (malformed_o),
            .empty_msg_o      (empty_msg_o),
            .stale_o          (stale_o)
        );
    end else begin : g_bad_width
        $error("mold_deframer: DATA_W must be 8 or 64 (ADR 0005, 0015)");
    end
endmodule
`default_nettype wire
