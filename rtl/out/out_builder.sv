`default_nettype none
// out_builder: BBO/trace records, stats snapshots and config acks -> UDP payloads for
// fpga-eth-core udp_tx. Wire format: docs/protocol.md v1; design: ADR 0007, ADR 0008.
//
// Records in (s_*): one record per handshake, as its wire bytes, byte 0 in the top 8 bits
//   of s_rec_i. Length by the mode (mode_i as of the last ack): 0 = BBO (64 bytes),
//   1 = trace (40 bytes, the top 40 bytes of s_rec_i). Records enter a record FIFO
//   (2**REC_ADDR_W x 512 bits, ADR 0012) in one cycle; s_ready_o is low only while it is
//   full. A writer pops them and copies the bytes into a byte FIFO at one per cycle
//   (REC_LEN cycles per record, longer while the byte FIFO is full). Capacity before
//   s_ready_o drops with the output stalled: 2**REC_ADDR_W + the byte FIFO's complete
//   records + the one in the writer.
// Datagrams out (m_*): AXI-Stream, one byte per beat, tlast on a datagram's last byte.
//   When idle, the builder starts the highest-priority datagram that is due:
//     1. config ack (ack_req_i seen): flushes the FIFO and any half-written record, sets
//        pkt_seq to 0, sends ack_rec_i as the record, arms the builder.
//     2. stats (stats_req_i seen while armed): snapshots stats_i on the start cycle;
//        counter k (docs/protocol.md order) is stats_i[(N_STATS-k)*64-1 -: 64].
//        Requests that arrive while one is pending merge into one datagram (they come
//        1 s apart; a datagram takes < 3,000 cycles even at half the TX rate).
//     3. records (armed, >= 1 complete record in the FIFO): count = min(complete records,
//        MAX_COUNT), oldest first ("flush on idle", no timer).
//   Nothing is sent before the first ack. pkt_seq counts every datagram after the ack.
// Contract: ack_rec_i is stable from ack_req_i until flush_o. mode_i is latched when an
//   ack starts (records still queued from the previous run leave in the previous mode;
//   the ack flushes both FIFOs in the cycle it starts).
//   The caller holds the record source in reset until flush_o.
// Status (registered one-cycle pulses): dgram_o at each datagram start, with dgram_recs_o
//   = its BBO/trace record count (0 for acks and stats); flush_o when an ack starts.
// Latency: record accepted (idle, empty FIFOs) -> first header byte valid: REC_LEN + 4
//   cycles (measured by test_out.py latency_matches_header).
// Memory: one 2**FIFO_ADDR_W x 8 sync_fifo (one BRAM18 at the default) and one
//   2**REC_ADDR_W x 512 sync_fifo (about 7.5 BRAM36 at the default).
// Reset: synchronous, active-high.
module out_builder #(
    parameter int N_STATS     = 41,
    parameter int FIFO_ADDR_W = 11,
    parameter int REC_ADDR_W  = 9,     // record FIFO depth 2**N (ADR 0012)
    parameter int MAX_PAYLOAD = 1472  // udp_tx's limit
) (
    input  wire logic                    clk_i,
    input  wire logic                    rst_i,
    input  wire logic                    mode_i,
    // records in
    input  wire logic                    s_valid_i,
    output logic                         s_ready_o,
    input  wire logic [64*8-1:0]         s_rec_i,
    // stats and ack requests
    input  wire logic                    stats_req_i,
    input  wire logic [N_STATS*64-1:0]   stats_i,
    input  wire logic                    ack_req_i,
    input  wire logic [24*8-1:0]         ack_rec_i,
    // UDP payloads out
    output logic [7:0]                   m_tdata_o,
    output logic                         m_tvalid_o,
    input  wire logic                    m_tready_i,
    output logic                         m_tlast_o,
    // status
    output logic                         dgram_o,
    output logic [5:0]                   dgram_recs_o,
    output logic                         flush_o
);
    // docs/protocol.md: common header and packet types
    localparam logic [15:0] MAGIC     = 16'h4F42;
    localparam logic [7:0]  VERSION   = 8'd1;
    localparam logic [7:0]  T_BBO     = 8'h01;
    localparam logic [7:0]  T_TRACE   = 8'h02;
    localparam logic [7:0]  T_STATS   = 8'h03;
    localparam logic [7:0]  T_ACK     = 8'h11;
    localparam int          HDR_BYTES = 12;
    localparam int          BBO_LEN   = 64;
    localparam int          TRACE_LEN = 40;
    localparam int          STAT_LEN  = 8;
    localparam int          ACK_LEN   = 24;
    localparam int          MAX_BBO   = (MAX_PAYLOAD - HDR_BYTES) / BBO_LEN;    // 22
    localparam int          MAX_TRACE = (MAX_PAYLOAD - HDR_BYTES) / TRACE_LEN;  // 36
    localparam int          LEN_W     = $clog2(MAX_PAYLOAD + 1);
    localparam int          RECS_W    = FIFO_ADDR_W + 1;  // complete records in the FIFO
    localparam int          WLEFT_W   = $clog2(BBO_LEN + 1);
    // left_q's shift-add below hard-codes these lengths
    if (TRACE_LEN != 32 + 8 || BBO_LEN != 64) begin : g_len_check
        $error("out_builder: record lengths changed; update the left_q shift-add");
    end

    typedef enum logic [1:0] {S_IDLE, S_HDR, S_BODY} state_t;
    typedef enum logic [1:0] {SRC_REC, SRC_STATS, SRC_ACK} src_t;

    // ---- writer: record -> byte FIFO ----
    logic [64*8-1:0]    wsh_q;     // record bytes, next byte on top
    logic [WLEFT_W-1:0] wleft_q;   // bytes of the current record still to write
    logic               fifo_full, fifo_empty, fifo_wr, fifo_rd, flush;
    logic [7:0]         fifo_dout;
    logic               rec_done;  // the current record's last byte is written this cycle
    logic               mode_q;    // mode_i as of the last ack
    logic               rf_full, rf_empty, rf_rd;
    logic [64*8-1:0]    rf_dout;

    // ---- record FIFO: the book hands a record over in one cycle ----
    assign s_ready_o = !rf_full;
    assign rf_rd     = wleft_q == '0 && !rf_empty && !flush;

    sync_fifo #(.ADDR_W(REC_ADDR_W), .DATA_W(64*8)) u_rec_fifo (
        .clk_i(clk_i), .rst_i(rst_i), .flush_i(flush),
        .wr_en_i(s_valid_i && !rf_full), .wr_data_i(s_rec_i), .full_o(rf_full),
        .rd_en_i(rf_rd), .rd_data_o(rf_dout), .empty_o(rf_empty)
    );

    assign fifo_wr   = wleft_q != '0 && !fifo_full;
    assign rec_done  = fifo_wr && wleft_q == WLEFT_W'(1);

    always_ff @(posedge clk_i) begin
        if (rst_i || flush) begin
            wleft_q <= '0;
        end else if (rf_rd) begin
            wsh_q   <= rf_dout;
            wleft_q <= mode_q ? WLEFT_W'(TRACE_LEN) : WLEFT_W'(BBO_LEN);
        end else if (fifo_wr) begin
            wsh_q   <= wsh_q << 8;
            wleft_q <= wleft_q - WLEFT_W'(1);
        end
    end

    sync_fifo #(.ADDR_W(FIFO_ADDR_W), .DATA_W(8)) u_fifo (
        .clk_i(clk_i), .rst_i(rst_i), .flush_i(flush),
        .wr_en_i(fifo_wr), .wr_data_i(wsh_q[64*8-1 -: 8]), .full_o(fifo_full),
        .rd_en_i(fifo_rd), .rd_data_o(fifo_dout), .empty_o(fifo_empty)
    );

    // ---- reader: datagrams ----
    state_t                 state_q;
    src_t                   src_q;
    logic                   armed_q, ack_pend_q, stats_pend_q;
    logic [31:0]            pkt_seq_q;
    logic [RECS_W-1:0]      recs_q;
    logic [HDR_BYTES*8-1:0] hdr_q;
    logic [$clog2(HDR_BYTES)-1:0] hcnt_q;
    logic [LEN_W-1:0]       left_q;    // body bytes still to send
    logic [N_STATS*64-1:0]  stat_q;
    logic [ACK_LEN*8-1:0]   ack_q;

    logic                   start_ack, start_stats, start_rec, beat;
    logic [RECS_W-1:0]      max_cnt, cnt;
    logic [7:0]             body_byte;

    assign start_ack   = state_q == S_IDLE && ack_pend_q;
    assign start_stats = state_q == S_IDLE && !ack_pend_q && armed_q && stats_pend_q;
    assign start_rec   = state_q == S_IDLE && !ack_pend_q && armed_q && !stats_pend_q
                         && recs_q != '0;
    assign flush       = start_ack;
    assign max_cnt     = mode_q ? RECS_W'(MAX_TRACE) : RECS_W'(MAX_BBO);
    assign cnt         = recs_q < max_cnt ? recs_q : max_cnt;

    always_comb begin
        body_byte = fifo_dout;
        case (src_q)
            SRC_STATS: body_byte = stat_q[N_STATS*64-1 -: 8];
            SRC_ACK:   body_byte = ack_q[ACK_LEN*8-1 -: 8];
            default:   body_byte = fifo_dout;
        endcase
    end

    always_comb begin
        m_tdata_o  = '0;
        m_tvalid_o = 1'b0;
        m_tlast_o  = 1'b0;
        if (state_q == S_HDR) begin
            m_tdata_o  = hdr_q[HDR_BYTES*8-1 -: 8];
            m_tvalid_o = 1'b1;
        end else if (state_q == S_BODY) begin
            m_tdata_o  = body_byte;
            m_tvalid_o = src_q != SRC_REC || !fifo_empty;
            m_tlast_o  = left_q == LEN_W'(1);
        end
    end

    assign beat    = m_tvalid_o && m_tready_i;
    assign fifo_rd = state_q == S_BODY && src_q == SRC_REC && beat;

    always_ff @(posedge clk_i) begin
        dgram_o      <= 1'b0;
        dgram_recs_o <= '0;
        flush_o      <= 1'b0;
        if (rst_i) begin
            state_q      <= S_IDLE;
            armed_q      <= 1'b0;
            mode_q       <= 1'b0;
            ack_pend_q   <= 1'b0;
            stats_pend_q <= 1'b0;
            pkt_seq_q    <= '0;
            recs_q       <= '0;
        end else begin
            if (ack_req_i) ack_pend_q <= 1'b1;
            if (stats_req_i && armed_q) stats_pend_q <= 1'b1;
            recs_q <= recs_q + RECS_W'(rec_done) - (start_rec ? cnt : '0);

            case (state_q)
                S_IDLE: begin
                    hcnt_q <= '0;
                    if (start_ack) begin
                        // the ack request seen this cycle is the one being served
                        ack_pend_q   <= 1'b0;
                        stats_pend_q <= 1'b0;
                        armed_q      <= 1'b1;
                        mode_q       <= mode_i;
                        recs_q       <= '0;
                        pkt_seq_q    <= 32'd1;
                        hdr_q  <= {MAGIC, VERSION, T_ACK, 32'd0, 16'd1, 16'(ACK_LEN)};
                        ack_q  <= ack_rec_i;
                        src_q  <= SRC_ACK;
                        left_q <= LEN_W'(ACK_LEN);
                        state_q <= S_HDR;
                        dgram_o <= 1'b1;
                        flush_o <= 1'b1;
                    end else if (start_stats) begin
                        stats_pend_q <= stats_req_i;  // a request this cycle stays due
                        pkt_seq_q    <= pkt_seq_q + 32'd1;
                        hdr_q  <= {MAGIC, VERSION, T_STATS, pkt_seq_q, 16'(N_STATS),
                                   16'(STAT_LEN)};
                        stat_q <= stats_i;
                        src_q  <= SRC_STATS;
                        left_q <= LEN_W'(N_STATS * STAT_LEN);
                        state_q <= S_HDR;
                        dgram_o <= 1'b1;
                    end else if (start_rec) begin
                        pkt_seq_q <= pkt_seq_q + 32'd1;
                        hdr_q  <= {MAGIC, VERSION, mode_q ? T_TRACE : T_BBO, pkt_seq_q,
                                   16'(cnt), mode_q ? 16'(TRACE_LEN) : 16'(BBO_LEN)};
                        src_q  <= SRC_REC;
                        // cnt * 40 or cnt * 64 as shifts (a DSP multiply was the
                        // critical path)
                        left_q <= mode_q ? (LEN_W'(cnt) << 5) + (LEN_W'(cnt) << 3)
                                         : LEN_W'(cnt) << 6;
                        state_q <= S_HDR;
                        dgram_o      <= 1'b1;
                        dgram_recs_o <= 6'(cnt);
                    end
                end
                S_HDR: if (beat) begin
                    hdr_q  <= hdr_q << 8;
                    hcnt_q <= hcnt_q + 1'b1;
                    if (hcnt_q == $clog2(HDR_BYTES)'(HDR_BYTES - 1)) state_q <= S_BODY;
                end
                S_BODY: if (beat) begin
                    if (src_q == SRC_STATS) stat_q <= stat_q << 8;
                    if (src_q == SRC_ACK)   ack_q  <= ack_q << 8;
                    left_q <= left_q - LEN_W'(1);
                    if (left_q == LEN_W'(1)) state_q <= S_IDLE;
                end
                default: state_q <= S_IDLE;
            endcase
        end
    end

`ifndef SYNTHESIS
    always_ff @(posedge clk_i) begin
        if (!rst_i) begin
            // AXI-Stream: data and tlast hold while valid waits for ready.
            if ($past(m_tvalid_o && !m_tready_i && !rst_i)) begin
                assert (m_tvalid_o);
                assert (m_tdata_o == $past(m_tdata_o));
                assert (m_tlast_o == $past(m_tlast_o));
            end
            // A datagram's records are complete in the FIFO when it starts.
            assert (!(start_rec && cnt == '0));
        end
    end
`endif
endmodule
`default_nettype wire
