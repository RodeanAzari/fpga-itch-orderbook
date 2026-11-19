`default_nettype none
// mold_deframer_w8: mold_deframer at DATA_W = 8 (selected by mold_deframer).
// MoldUDP64 packets (UDP payloads) -> ITCH messages, with the sequence
// semantics of ADR 0003. Golden model: model/moldudp64.py SeqTracker.
//
// Input:  AXI-Stream, one byte per beat, one MoldUDP64 packet per tlast (udp_rx output).
//         s_tuser_i on tlast = the datagram was truncated: the packet is malformed.
// Output: AXI-Stream, one byte per beat, one ITCH message per tlast (length prefix
//         removed). m_seq_o is the message's MoldUDP64 sequence number, valid with every
//         beat of it. m_tuser_o on tlast = the packet ended inside this message: discard
//         it (the packet is already counted as malformed).
//
// Packet format (docs/buildplan.md §5.2, big-endian): session (10 bytes), sequence of the first
// message (8), count (2; 0 = heartbeat, 0xFFFF = end of session), then per message a
// 2-byte length and the message.
//
// Sequence rules (decided on the last header byte):
//   - A packet whose session differs from the stored one starts tracking at 1; it counts
//     as a session change (and marks stale) unless it is the first session seen.
//   - seq < expected: duplicate or late; the whole packet is dropped (dup_o).
//   - seq > expected: gap of (seq - expected) messages (gap_o, gap_msgs_o); stale.
//   - Then expected = seq + count (heartbeat / end of session: expected = seq).
// Malformed packets (malformed_o, once per packet, at its tlast; stale): shorter than the
// header, a message or length prefix running past the end, bytes after the last message,
// or a heartbeat / end-of-session with a body. Messages before the break are delivered.
// A zero-length message is framed correctly but carries nothing: empty_msg_o.
//
// Status pulses (one cycle): pkt_o (every packet, at tlast), heartbeat_o, end_o, gap_o
// (with gap_msgs_o), dup_o, session_change_o, malformed_o, empty_msg_o. stale_o is a
// level, sticky until reset.
// In-band stale (ADR 0016): m_stale_o, valid with every beat, is the stale state as the
// message is delivered: stale_o so far, or an empty message earlier, or s_stale_i
// sampled at the packet's first byte (an external reason, e.g. a bad cut-through frame
// before this packet). A packet's malformed tail is found after its messages, so it
// flags only later ones (as model SeqTracker.msg_stale).
//
// Latency: 1 cycle from a message byte in to its output beat. Throughput 1 byte/cycle.
// Timing: the header verdict's wide compares are precomputed at bytes 10, 11 and 18, and
// expected = seq + count is added into a register on the cycle after the header and
// loaded into expected one cycle later, so no 64-bit add follows the backpressure
// chain (the next header is at least 20 bytes away).
// Backpressure: s_tready_o = !m_tvalid_o || m_tready_i, also while parsing headers.
// Reset: synchronous, active-high; clears the session, expected and stale.
module mold_deframer_w8 #(
    parameter int DATA_W = 8
) (
    input  wire logic              clk_i,
    input  wire logic              rst_i,
    // packets in
    input  wire logic [DATA_W-1:0] s_tdata_i,
    input  wire logic              s_tvalid_i,
    output logic                   s_tready_o,
    input  wire logic              s_tlast_i,
    input  wire logic              s_tuser_i,
    input  wire logic [63:0]       s_t0_i,    // T0 of this packet's frame (ADR 0011)
    input  wire logic              s_stale_i, // sampled at the packet's first byte (0016)
    // messages out
    output logic [DATA_W-1:0]      m_tdata_o,
    output logic                   m_tvalid_o,
    input  wire logic              m_tready_i,
    output logic                   m_tlast_o,
    output logic                   m_tuser_o,
    output logic [63:0]            m_seq_o,
    output logic [63:0]            m_t0_o,    // s_t0_i at the packet's first byte
    output logic                   m_stale_o, // in-band stale (ADR 0016)
    // status
    output logic                   pkt_o,
    output logic                   heartbeat_o,
    output logic                   end_o,
    output logic                   gap_o,
    output logic [63:0]            gap_msgs_o,
    output logic                   dup_o,
    output logic                   session_change_o,
    output logic                   malformed_o,
    output logic                   empty_msg_o,
    output logic                   stale_o
);
    // MoldUDP64 header, docs/buildplan.md §5.2
    localparam int          HDR_LEN     = 20;
    localparam int          SEQ_OFF     = 10;   // sequence number, bytes 10..17
    localparam int          CNT_OFF     = 18;   // message count, bytes 18..19
    localparam logic [15:0] CNT_HB      = 16'h0000;
    localparam logic [15:0] CNT_END     = 16'hFFFF;
    localparam logic [63:0] FIRST_SEQ   = 64'd1;

    if (DATA_W != 8) begin : g_bad_width
        $error("mold_deframer_w8: DATA_W must be 8");
    end

    typedef enum logic [2:0] {S_HDR, S_LEN, S_MSG, S_TRAIL, S_DROP} state_t;

    state_t      state_q;
    logic [4:0]  hcnt_q;        // header byte index
    logic        len_hi_q;      // the second length byte is next

    // Tracking state (control, reset)
    logic        have_session_q;
    logic [63:0] expected_q;

    // Datapath (not reset)
    logic [79:0] session_q, sess_in_q;
    logic [63:0] seq_in_q, msg_seq_q;
    logic [7:0]  cnt_hi_q, len_b0_q;
    logic [15:0] msgs_left_q, remain_q, count_q;
    // Header verdict, precomputed while the header streams in (timing: no 64/80-bit
    // compare or add on the last header byte). Valid from the byte noted.
    logic        new_sess_q;    // byte 10: session differs from the stored one (or none)
    logic [63:0] exp_q;         // byte 11: expected sequence for this packet
    logic        seq_lt_q;      // byte 18: seq < expected (duplicate / late)
    logic        seq_gt_q;      // byte 18: seq > expected (gap)
    logic [63:0] gap_q;         // byte 18: seq - expected
    logic        upd_exp_q;     // the cycle after the header: exp_sum_q is being formed
    logic        upd_exp2_q;    // two cycles after the header: expected <= exp_sum_q
    logic [63:0] exp_sum_q;     // seq + count, from registers stable since the header

    logic [63:0] pkt_t0_q;
    logic        pkt_stale_q, empty_seen_q;
    logic        fire, emit, hdr_last, last_msg, is_dup, end_ok, drop_pkt;
    logic [15:0] count, len;

    always_comb begin
        s_tready_o  = !m_tvalid_o || m_tready_i;
        fire        = s_tvalid_i && s_tready_o;
        emit        = fire && state_q == S_MSG;
        hdr_last    = state_q == S_HDR && hcnt_q == 5'(HDR_LEN - 1);
        count       = {cnt_hi_q, s_tdata_i};
        len         = {len_b0_q, s_tdata_i};
        last_msg    = msgs_left_q == 16'd1;
        is_dup      = hdr_last && seq_lt_q;
        // A packet is well formed only if its tlast is its final byte: the last header
        // byte of a heartbeat / end of session, the last byte of the last message, or
        // the zero-length prefix of the last message. Dropped duplicates are never judged.
        end_ok      = (hdr_last && (count == CNT_HB || count == CNT_END))
                    || (state_q == S_LEN && len_hi_q && len == '0 && last_msg)
                    || (state_q == S_MSG && remain_q == 16'd1 && last_msg);
        drop_pkt    = is_dup || state_q == S_DROP;
    end

    // Header fields and message bookkeeping (datapath).
    always_ff @(posedge clk_i) begin
        if (fire) begin
            if (state_q == S_HDR) begin
                if (hcnt_q < 5'(SEQ_OFF)) sess_in_q <= {sess_in_q[71:0], s_tdata_i};
                else if (hcnt_q < 5'(CNT_OFF)) seq_in_q <= {seq_in_q[55:0], s_tdata_i};
                else cnt_hi_q <= s_tdata_i;
                // The session is complete before byte 10, the sequence before byte 18.
                if (hcnt_q == 5'(SEQ_OFF))
                    new_sess_q <= !have_session_q || sess_in_q != session_q;
                if (hcnt_q == 5'(SEQ_OFF + 1))
                    exp_q <= new_sess_q ? FIRST_SEQ : expected_q;
                if (hcnt_q == 5'(CNT_OFF)) begin
                    seq_lt_q <= seq_in_q < exp_q;
                    seq_gt_q <= seq_in_q > exp_q;
                    gap_q    <= seq_in_q - exp_q;
                end
            end
            if (hdr_last) begin
                msg_seq_q   <= seq_in_q;
                msgs_left_q <= count;
                count_q     <= count;
            end
            if (state_q == S_LEN && !len_hi_q) len_b0_q <= s_tdata_i;
            if (state_q == S_LEN && len_hi_q) remain_q <= len;
            if (state_q == S_MSG) remain_q <= remain_q - 16'd1;
            // A message ends: on its last byte, or on its zero-length prefix.
            if ((state_q == S_MSG && remain_q == 16'd1)
                || (state_q == S_LEN && len_hi_q && len == '0)) begin
                msg_seq_q   <= msg_seq_q + 64'd1;
                msgs_left_q <= msgs_left_q - 16'd1;
            end
            if (hdr_last && new_sess_q) session_q <= sess_in_q;
        end
        // T0 is sampled at the packet's first byte, when it is the frame's (ADR 0011).
        if (fire && state_q == S_HDR && hcnt_q == '0) begin
            pkt_t0_q    <= s_t0_i;
            pkt_stale_q <= s_stale_i;
        end
        if (emit) begin
            m_tdata_o <= s_tdata_i;
            m_seq_o   <= msg_seq_q;
            m_t0_o    <= pkt_t0_q;
            m_stale_o <= stale_o || empty_msg_o || empty_seen_q || pkt_stale_q;
        end
        gap_msgs_o <= gap_q;
        exp_sum_q  <= seq_in_q + 64'(count_q);
    end

    // Control.
    always_ff @(posedge clk_i) begin
        if (rst_i) begin
            state_q          <= S_HDR;
            hcnt_q           <= '0;
            len_hi_q         <= 1'b0;
            have_session_q   <= 1'b0;
            expected_q       <= FIRST_SEQ;
            upd_exp_q        <= 1'b0;
            upd_exp2_q       <= 1'b0;
            stale_o          <= 1'b0;
            empty_seen_q     <= 1'b0;
            m_tvalid_o       <= 1'b0;
            m_tlast_o        <= 1'b0;
            m_tuser_o        <= 1'b0;
            pkt_o            <= 1'b0;
            heartbeat_o      <= 1'b0;
            end_o            <= 1'b0;
            gap_o            <= 1'b0;
            dup_o            <= 1'b0;
            session_change_o <= 1'b0;
            malformed_o      <= 1'b0;
            empty_msg_o      <= 1'b0;
        end else begin
            pkt_o            <= 1'b0;
            heartbeat_o      <= 1'b0;
            end_o            <= 1'b0;
            gap_o            <= 1'b0;
            dup_o            <= 1'b0;
            session_change_o <= 1'b0;
            malformed_o      <= 1'b0;
            empty_msg_o      <= 1'b0;
            upd_exp_q        <= 1'b0;
            upd_exp2_q       <= upd_exp_q;
            if (empty_msg_o) empty_seen_q <= 1'b1;
            // seq_in_q and count_q are stable for >= 10 cycles after the header.
            if (upd_exp2_q) expected_q <= exp_sum_q;

            if (emit) begin
                m_tvalid_o <= 1'b1;
                m_tlast_o  <= remain_q == 16'd1 || s_tlast_i;
                m_tuser_o  <= s_tlast_i && remain_q != 16'd1;  // packet ended early
            end else if (m_tready_i) begin
                m_tvalid_o <= 1'b0;
            end

            if (fire) begin
                unique case (state_q)
                    S_HDR: begin
                        hcnt_q <= hcnt_q + 5'd1;
                        if (hdr_last) begin
                            have_session_q <= 1'b1;
                            if (new_sess_q && have_session_q) begin
                                session_change_o <= 1'b1;
                                stale_o          <= 1'b1;
                            end
                            if (is_dup) begin
                                dup_o      <= 1'b1;
                                expected_q <= exp_q;
                                state_q    <= S_DROP;
                            end else begin
                                if (seq_gt_q) begin
                                    gap_o   <= 1'b1;
                                    stale_o <= 1'b1;
                                end
                                if (count == CNT_HB || count == CNT_END) begin
                                    heartbeat_o <= count == CNT_HB;
                                    end_o       <= count == CNT_END;
                                    expected_q  <= seq_in_q;
                                    state_q     <= S_TRAIL;
                                end else begin
                                    upd_exp_q  <= 1'b1;
                                    state_q    <= S_LEN;
                                    len_hi_q   <= 1'b0;
                                end
                            end
                        end
                    end
                    S_LEN: begin
                        len_hi_q <= !len_hi_q;
                        if (len_hi_q) begin
                            if (len != '0)    state_q <= S_MSG;
                            else begin
                                empty_msg_o <= 1'b1;
                                if (last_msg) state_q <= S_TRAIL;
                            end
                        end
                    end
                    S_MSG: if (remain_q == 16'd1 && last_msg) state_q <= S_TRAIL;
                    else if (remain_q == 16'd1) state_q <= S_LEN;
                    default: ;  // S_TRAIL (bytes after the last message), S_DROP: to tlast
                endcase

                if (s_tlast_i) begin
                    state_q  <= S_HDR;
                    hcnt_q   <= '0;
                    len_hi_q <= 1'b0;
                    pkt_o    <= 1'b1;
                    if (!drop_pkt && (!end_ok || s_tuser_i)) begin
                        malformed_o <= 1'b1;
                        stale_o     <= 1'b1;
                    end
                end
            end
        end
    end

`ifndef SYNTHESIS
    always_ff @(posedge clk_i) begin
        if (!rst_i) begin
            assert (!(m_tuser_o && !m_tlast_o) || !m_tvalid_o);
            assert (!(gap_o && dup_o));
        end
    end
`endif

`ifdef FORMAL
    // ---------------------------------------------------------------- formal (§8.3)
    // tb/formal/mold_deframer.sby. f_* is a shadow of ADR 0003 (model SeqTracker.feed):
    // the header parsed from the input bytes alone and judged on its last byte with no
    // precompute or pipelining. The DUT's verdict pulses, expected sequence, stale level,
    // duplicate drop and the malformed cases decided by the header alone must match it.
    // The f_inv assertions tie the DUT's precomputed header registers to the shadow so
    // k-induction closes; they are what the timing comment above promises.
    logic        f_past = 1'b0;
    logic [4:0]  f_pos;               // byte index in the packet, saturating at HDR_LEN
    logic [79:0] f_sess, f_session;
    logic [63:0] f_seq, f_expected, f_gapn_q;
    logic [7:0]  f_cnt_hi;
    logic        f_have, f_stale, f_mal_seen, f_drop, f_hbpkt;
    logic        f_gap_q, f_dup_q, f_sc_q, f_hb_q, f_end_q, f_pkt_q, f_must_q, f_never_q;
    logic [15:0] f_count;
    logic        f_hdr, f_new, f_dup, f_gap, f_hbish, f_this_dup;
    logic [63:0] f_exp;

    always_comb begin
        f_hdr      = fire && f_pos == 5'(HDR_LEN - 1);
        f_count    = {f_cnt_hi, s_tdata_i};
        f_new      = !f_have || f_sess != f_session;
        f_exp      = f_new ? FIRST_SEQ : f_expected;
        f_dup      = f_seq < f_exp;
        f_gap      = f_seq > f_exp;
        f_hbish    = f_count == CNT_HB || f_count == CNT_END;
        f_this_dup = f_drop || (f_hdr && f_dup);
    end

    always_ff @(posedge clk_i) begin
        f_past <= 1'b1;
        if (fire && f_pos < 5'(SEQ_OFF)) f_sess <= {f_sess[71:0], s_tdata_i};
        if (fire && f_pos >= 5'(SEQ_OFF) && f_pos < 5'(CNT_OFF))
            f_seq <= {f_seq[55:0], s_tdata_i};
        if (fire && f_pos == 5'(CNT_OFF)) f_cnt_hi <= s_tdata_i;
        f_gapn_q <= f_seq - f_exp;
        if (rst_i) begin
            f_pos      <= '0;
            f_have     <= 1'b0;
            f_expected <= FIRST_SEQ;
            f_stale    <= 1'b0;
            f_mal_seen <= 1'b0;
            f_drop     <= 1'b0;
            f_hbpkt    <= 1'b0;
            {f_gap_q, f_dup_q, f_sc_q, f_hb_q, f_end_q, f_pkt_q, f_must_q, f_never_q} <= '0;
        end else begin
            f_gap_q   <= f_hdr && !f_dup && f_gap;
            f_dup_q   <= f_hdr && f_dup;
            f_sc_q    <= f_hdr && f_new && f_have;
            f_hb_q    <= f_hdr && !f_dup && f_count == CNT_HB;
            f_end_q   <= f_hdr && !f_dup && f_count == CNT_END;
            f_pkt_q   <= fire && s_tlast_i;
            // Malformed by the header alone: shorter than the header, a heartbeat / end
            // of session with a body, or truncated (tuser). Never for a duplicate, never
            // for a heartbeat / end of session that ends on its header.
            f_must_q  <= fire && s_tlast_i && !f_this_dup
                         && (f_pos < 5'(HDR_LEN - 1) || f_hbpkt || s_tuser_i);
            f_never_q <= fire && s_tlast_i
                         && (f_this_dup || (f_hdr && f_hbish && !s_tuser_i));
            if (malformed_o) f_mal_seen <= 1'b1;
            if (fire) f_pos <= s_tlast_i ? '0
                             : f_pos == 5'(HDR_LEN) ? f_pos : f_pos + 5'd1;
            if (f_hdr) begin
                f_have <= 1'b1;
                if (f_new) f_session <= f_sess;
                if ((f_new && f_have) || (!f_dup && f_gap)) f_stale <= 1'b1;
                f_expected <= f_dup ? f_exp : f_hbish ? f_seq : f_seq + 64'(f_count);
                f_drop     <= f_dup && !s_tlast_i;
                f_hbpkt    <= !f_dup && f_hbish && !s_tlast_i;
            end else if (fire && s_tlast_i) begin
                f_drop  <= 1'b0;
                f_hbpkt <= 1'b0;
            end
        end
    end

    always_comb begin
        if (f_past) begin
            // Spec: the verdict pulses one cycle after the header's last byte.
            assert (gap_o == f_gap_q);
            assert (dup_o == f_dup_q);
            assert (session_change_o == f_sc_q);
            assert (heartbeat_o == f_hb_q);
            assert (end_o == f_end_q);
            assert (pkt_o == f_pkt_q);
            if (gap_o) assert (gap_msgs_o == f_gapn_q);
            if (f_must_q) assert (malformed_o);
            if (f_never_q || !f_pkt_q) assert (!malformed_o);
            assert (stale_o == (f_stale || f_mal_seen || malformed_o));
            // A duplicate packet delivers nothing.
            if (f_drop) assert (!emit);
            if (f_hdr && f_dup) assert (!emit);

            // f_inv: the DUT's header registers agree with the shadow.
            assert (state_q == S_HDR || state_q == S_LEN || state_q == S_MSG
                    || state_q == S_TRAIL || state_q == S_DROP);
            assert (f_pos <= 5'(HDR_LEN));
            assert ((state_q == S_HDR) == (f_pos < 5'(HDR_LEN)));
            if (state_q == S_HDR) assert (hcnt_q == f_pos);
            assert (f_drop == (state_q == S_DROP));
            assert (have_session_q == f_have);
            if (f_have) assert (session_q == f_session);
            assert (!(upd_exp_q && upd_exp2_q));
            if (!upd_exp_q && !upd_exp2_q) assert (expected_q == f_expected);
            if (upd_exp_q) assert (seq_in_q + 64'(count_q) == f_expected
                                   && (state_q != S_HDR || hcnt_q == '0));
            if (upd_exp2_q) assert (exp_sum_q == f_expected
                                    && (state_q != S_HDR || hcnt_q <= 5'd1));
            if (upd_exp_q || upd_exp2_q) assert (!f_hbpkt && !f_drop);
            if (state_q == S_HDR && hcnt_q >= 5'(SEQ_OFF)) assert (sess_in_q == f_sess);
            if (state_q == S_HDR && hcnt_q > 5'(SEQ_OFF))
                assert (new_sess_q == (!have_session_q || sess_in_q != session_q));
            if (state_q == S_HDR && hcnt_q > 5'(SEQ_OFF + 1))
                assert (exp_q == (new_sess_q ? FIRST_SEQ : expected_q));
            if ((state_q == S_HDR && hcnt_q >= 5'(CNT_OFF)) || upd_exp_q)
                assert (seq_in_q == f_seq);
            // Part-way through a field, the bytes shifted in so far agree.
            if (state_q == S_HDR && hcnt_q < 5'(SEQ_OFF))
                assert (((sess_in_q ^ f_sess) & ((80'd1 << (8 * hcnt_q)) - 80'd1)) == '0);
            if (state_q == S_HDR && hcnt_q >= 5'(SEQ_OFF) && hcnt_q < 5'(CNT_OFF))
                assert (((seq_in_q ^ f_seq)
                         & ((64'd1 << (8 * (hcnt_q - 5'(SEQ_OFF)))) - 64'd1)) == '0);
            if (state_q == S_HDR && hcnt_q > 5'(CNT_OFF)) begin
                assert (cnt_hi_q == f_cnt_hi);
                assert (seq_lt_q == (seq_in_q < exp_q));
                assert (seq_gt_q == (seq_in_q > exp_q));
                assert (gap_q == seq_in_q - exp_q);
            end
            if (f_hbpkt) assert (state_q == S_TRAIL);
        end
    end
`endif
endmodule
`default_nettype wire
