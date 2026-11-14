`default_nettype none
// mold_deframer_w64: mold_deframer at DATA_W = 64 (selected by mold_deframer; ADR 0015).
// MoldUDP64 packets (UDP payloads) -> ITCH messages, with the sequence semantics of
// ADR 0003. Golden model: model/moldudp64.py SeqTracker. Sequence rules, malformed-packet
// rules and status pulses are those of mold_deframer_w8, decided at the same points.
//
// Input:  8 byte lanes per beat (lane 0 = first byte on the wire), one MoldUDP64 packet
//         per tlast. Beats are full except a packet's last, whose tkeep is contiguous
//         from lane 0 (never 0). s_tuser_i on tlast = the datagram was truncated.
// Output: one ITCH message per tlast (length prefix removed). Each beat holds bytes of one
//         message in contiguous lanes, at the lanes they had at the input: a message's
//         first beat may start at any lane, its middle beats are full, its last ends at
//         any lane. m_seq_o, m_t0_o are valid with every beat. m_tuser_o on tlast = the
//         packet ended inside this message: discard it.
//
// How: the input beat is held in a register with a lane pointer. Each cycle (a "step")
// consumes one element from the pointer: a header beat, the verdict (no bytes), a length
// prefix together with the message bytes after it in the beat, the rest of a message up
// to its end or the beat's end, a zero-length message, or the bytes after the last
// message. A beat with k message ends takes up to k + 1 steps. The header always
// occupies beats 0, 1 and lanes 0..3 of beat 2 (packets start at lane 0).
//
// Latency: 2 cycles from a beat's input handshake to its first output beat (holding
// register, output register). Throughput: one step per cycle, so cycles per packet <=
// beats + messages + 1 (the verdict).
// Timing: the 80-bit session compare is registered on header beat 1, the expected
// sequence on beat 2, and the verdict compares/subtraction run on their own step from
// registers. expected = seq + count is added into a register on the cycle after the
// verdict and loaded one cycle later (the next verdict is >= 4 cycles away).
// Backpressure: a step runs only when the output register is free; s_tready_o = holding
// register empty, or its beat finishes this cycle.
// In-band stale (ADR 0016): m_stale_o as in mold_deframer_w8 (s_stale_i sampled at the
// packet's first beat). The verdict and an empty message are steps of their own, so a
// later step's beat sees their stale.
// Reset: synchronous, active-high; clears the session, expected and stale.
module mold_deframer_w64 (
    input  wire logic        clk_i,
    input  wire logic        rst_i,
    // packets in
    input  wire logic [63:0] s_tdata_i,
    input  wire logic [7:0]  s_tkeep_i,
    input  wire logic        s_tvalid_i,
    output logic             s_tready_o,
    input  wire logic        s_tlast_i,
    input  wire logic        s_tuser_i,
    input  wire logic [63:0] s_t0_i,    // T0 of this packet's frame (ADR 0011)
    input  wire logic        s_stale_i, // sampled at the packet's first beat (ADR 0016)
    // messages out
    output logic [63:0]      m_tdata_o,
    output logic [7:0]       m_tkeep_o,
    output logic             m_tvalid_o,
    input  wire logic        m_tready_i,
    output logic             m_tlast_o,
    output logic             m_tuser_o,
    output logic [63:0]      m_seq_o,
    output logic [63:0]      m_t0_o,    // s_t0_i at the packet's first beat
    output logic             m_stale_o, // in-band stale (ADR 0016)
    // status (as mold_deframer_w8)
    output logic             pkt_o,
    output logic             heartbeat_o,
    output logic             end_o,
    output logic             gap_o,
    output logic [63:0]      gap_msgs_o,
    output logic             dup_o,
    output logic             session_change_o,
    output logic             malformed_o,
    output logic             empty_msg_o,
    output logic             stale_o
);
    localparam int          LANES     = 8;
    // MoldUDP64 header, docs/buildplan.md §5.2: session bytes 0..9, sequence 10..17,
    // count 18..19. At 8 lanes: beat 0 = session 0..7; beat 1 = session 8..9 (lanes 0,1),
    // sequence 0..5 (lanes 2..7); beat 2 = sequence 6..7 (lanes 0,1), count (lanes 2,3).
    localparam logic [3:0]  HDR_TAIL  = 4'd4;   // header bytes in beat 2
    localparam logic [15:0] CNT_HB    = 16'h0000;
    localparam logic [15:0] CNT_END   = 16'hFFFF;
    localparam logic [63:0] FIRST_SEQ = 64'd1;

    typedef enum logic [2:0] {S_HDR, S_VERD, S_LEN, S_MSG, S_TRAIL, S_DROP} state_t;

    function automatic logic [7:0] lane(input logic [63:0] d, input logic [2:0] i);
        return d[8*i +: 8];
    endfunction

    // Bytes in a beat from its tkeep (contiguous from lane 0).
    function automatic logic [3:0] keep_bytes(input logic [7:0] k);
        logic [3:0] n;
        n = '0;
        for (int i = 0; i < LANES; i++) if (k[i]) n = 4'(i + 1);
        return n;
    endfunction

    // Input holding register
    logic        in_v_q;                      // control
    logic [63:0] in_data_q, in_t0_q;          // datapath
    logic [3:0]  in_nb_q;                     // bytes in the beat, 1..8
    logic        in_last_q, in_user_q, in_stale_q;
    logic        pkt_stale_q, empty_seen_q;

    // Parser state (control, reset)
    state_t      state_q;
    logic [1:0]  hbeat_q;                     // header beat index
    logic [3:0]  ptr_q;                       // next lane to consume
    logic        len_hi_q;                    // the second length byte is next
    logic        have_session_q;
    logic [63:0] expected_q;
    logic        upd_exp_q, upd_exp2_q;

    // Datapath (not reset)
    logic [63:0] sess_hi_q;                   // session bytes 0..7
    logic [79:0] sess_full_q, session_q;
    logic [47:0] seq_hi_q;                    // sequence bytes 0..5
    logic [63:0] seq_full_q, exp_q, msg_seq_q, exp_sum_q, pkt_t0_q;
    logic [15:0] count_q, msgs_left_q, remain_q;
    logic [7:0]  len_b0_q;
    logic        new_sess_q;
    // Length-prefix verdicts, computed as each beat is accepted (timing: the step only
    // selects one by lane). Slot i < 7: a prefix in lanes i, i+1; slot 7: a prefix whose
    // first byte was the previous beat's lane 7 and second is lane 0. For each: the
    // length is 0 (pf_z), the message ends in this beat (pf_le), it ends exactly at the
    // beat's last byte (pf_eq).
    logic [7:0]  pf_z_q, pf_le_q, pf_eq_q;

    // Length prefix and room after it for slot i of the beat being accepted (see pf_*).
    function automatic logic [15:0] pf_len(input int i);
        if (i == LANES - 1) return {in_data_q[63:56], s_tdata_i[7:0]};
        return {s_tdata_i[8*i +: 8], s_tdata_i[8*(i+1) +: 8]};
    endfunction
    function automatic logic [3:0] pf_room(input int i);
        return keep_bytes(s_tkeep_i) - (i == LANES - 1 ? 4'd1 : 4'(i + 2));
    endfunction

    // Step
    logic        run, finished, pkt_end, to_verd, seq_lt, seq_gt, is_cnt_end;
    logic        emit, msg_end, empty, extra, take_b0, last_msg, len_hi_d, drop_d;
    logic        pz, ple, peq, straddle;
    logic [2:0]  pf_sel;
    state_t      state_d;
    logic [3:0]  avail, p2, room, n, ptr_d, lo;
    logic [15:0] len, remain_d;
    logic [7:0]  keep;
    logic [79:0] sess_in;

    always_comb begin
        run        = in_v_q && (!m_tvalid_o || m_tready_i);
        avail      = in_nb_q - ptr_q;
        last_msg   = msgs_left_q == 16'd1;
        seq_lt     = seq_full_q < exp_q;
        seq_gt     = seq_full_q > exp_q;
        is_cnt_end = count_q == CNT_HB || count_q == CNT_END;
        sess_in    = {sess_hi_q, lane(in_data_q, 3'd0), lane(in_data_q, 3'd1)};
        len        = len_hi_q ? {len_b0_q, lane(in_data_q, ptr_q[2:0])}
                              : {lane(in_data_q, ptr_q[2:0]), lane(in_data_q, ptr_q[2:0] + 3'd1)};
        p2         = ptr_q + (len_hi_q ? 4'd1 : 4'd2);
        room       = in_nb_q - p2;
        straddle   = !len_hi_q && avail == 4'd1;  // a prefix's first byte is the beat's last
        pf_sel     = len_hi_q ? 3'd7 : ptr_q[2:0];
        pz         = pf_z_q[pf_sel];
        ple        = pf_le_q[pf_sel];
        peq        = pf_eq_q[pf_sel];
        state_d    = state_q;
        ptr_d      = ptr_q;
        len_hi_d   = len_hi_q;
        remain_d   = remain_q;
        lo         = ptr_q;
        n          = '0;
        emit       = 1'b0;
        msg_end    = 1'b0;
        empty      = 1'b0;
        extra      = 1'b0;
        take_b0    = 1'b0;
        to_verd    = 1'b0;

        unique case (state_q)
            S_HDR: begin
                // A packet too short for the header ends here (malformed: not S_TRAIL).
                if (hbeat_q == 2'd2 && in_nb_q >= HDR_TAIL) begin
                    ptr_d   = HDR_TAIL;
                    state_d = S_VERD;
                    to_verd = 1'b1;
                end else begin
                    ptr_d   = in_nb_q;
                end
            end
            S_VERD: begin
                if (seq_lt)          state_d = S_DROP;
                else if (is_cnt_end) state_d = S_TRAIL;
                else                 state_d = S_LEN;
            end
            S_LEN: begin
                if (straddle) begin
                    take_b0  = 1'b1;
                    len_hi_d = 1'b1;
                    ptr_d    = in_nb_q;
                end else begin
                    len_hi_d = 1'b0;
                    if (pz) begin
                        empty = 1'b1;
                        ptr_d = p2;
                    end else begin
                        lo      = p2;
                        state_d = S_MSG;
                        if (ple) begin
                            n       = len[3:0];
                            msg_end = 1'b1;
                        end else begin
                            n       = room;
                        end
                        emit     = n != '0;
                        ptr_d    = p2 + n;
                        remain_d = len - 16'(n);
                    end
                end
            end
            S_MSG: begin
                if (remain_q <= 16'(avail)) begin
                    n       = remain_q[3:0];
                    msg_end = 1'b1;
                end else begin
                    n       = avail;
                end
                emit     = 1'b1;
                ptr_d    = ptr_q + n;
                remain_d = remain_q - 16'(n);
            end
            S_TRAIL: begin
                extra = 1'b1;
                ptr_d = in_nb_q;
            end
            default: ptr_d = in_nb_q;  // S_DROP: to tlast
        endcase
        if (msg_end || empty) state_d = last_msg ? S_TRAIL : S_LEN;

        // finished = ptr_d == in_nb_q && !to_verd (asserted below), from short paths only.
        unique case (state_q)
            S_HDR:   finished = !(hbeat_q == 2'd2 && in_nb_q >= HDR_TAIL);
            S_VERD:  finished = in_nb_q == HDR_TAIL;
            S_LEN:   finished = straddle || !ple || peq;
            S_MSG:   finished = remain_q >= 16'(avail);
            default: finished = 1'b1;
        endcase
        pkt_end    = run && finished && in_last_q;
        drop_d     = state_d == S_DROP;
        s_tready_o = !in_v_q || (run && finished);
        for (int i = 0; i < LANES; i++) keep[i] = 4'(i) >= lo && 4'(i) < ptr_d;
    end

    // Datapath.
    always_ff @(posedge clk_i) begin
        if (s_tvalid_i && s_tready_o) begin
            in_data_q <= s_tdata_i;
            in_nb_q   <= keep_bytes(s_tkeep_i);
            in_last_q <= s_tlast_i;
            in_user_q <= s_tuser_i;
            in_t0_q   <= s_t0_i;
            in_stale_q <= s_stale_i;
            // in_data_q still holds the previous beat here (slot 7 needs its lane 7).
            for (int i = 0; i < LANES; i++) begin
                pf_z_q[i]  <= pf_len(i) == '0;
                pf_le_q[i] <= pf_len(i) <= 16'(pf_room(i));
                pf_eq_q[i] <= pf_len(i) == 16'(pf_room(i));
            end
        end
        if (run) begin
            if (state_q == S_HDR) begin
                unique case (hbeat_q)
                    2'd0: begin
                        for (int i = 0; i < LANES; i++)
                            sess_hi_q[63-8*i -: 8] <= lane(in_data_q, 3'(i));
                        pkt_t0_q    <= in_t0_q;  // T0 at the packet's first beat (ADR 0011)
                        pkt_stale_q <= in_stale_q;
                    end
                    2'd1: begin
                        sess_full_q <= sess_in;
                        new_sess_q  <= !have_session_q || sess_in != session_q;
                        for (int i = 2; i < LANES; i++)
                            seq_hi_q[47-8*(i-2) -: 8] <= lane(in_data_q, 3'(i));
                    end
                    default: begin
                        seq_full_q <= {seq_hi_q, lane(in_data_q, 3'd0), lane(in_data_q, 3'd1)};
                        count_q    <= {lane(in_data_q, 3'd2), lane(in_data_q, 3'd3)};
                        exp_q      <= new_sess_q ? FIRST_SEQ : expected_q;
                    end
                endcase
            end
            if (state_q == S_VERD) begin
                msg_seq_q   <= seq_full_q;
                msgs_left_q <= count_q;
                gap_msgs_o  <= seq_full_q - exp_q;
                if (new_sess_q) session_q <= sess_full_q;
            end
            if (take_b0) len_b0_q <= lane(in_data_q, ptr_q[2:0]);
            remain_q <= remain_d;
            if (msg_end || empty) begin
                msg_seq_q   <= msg_seq_q + 64'd1;
                msgs_left_q <= msgs_left_q - 16'd1;
            end
            if (emit) begin
                m_tdata_o <= in_data_q;
                m_tkeep_o <= keep;
                m_seq_o   <= msg_seq_q;
                m_t0_o    <= pkt_t0_q;
                m_stale_o <= stale_o || empty_msg_o || empty_seen_q || pkt_stale_q;
            end
        end
        exp_sum_q <= seq_full_q + 64'(count_q);
    end

    // Control.
    always_ff @(posedge clk_i) begin
        if (rst_i) begin
            in_v_q           <= 1'b0;
            state_q          <= S_HDR;
            hbeat_q          <= '0;
            ptr_q            <= '0;
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
            // seq_full_q and count_q are stable until the next packet's header beat 2.
            if (upd_exp2_q) expected_q <= exp_sum_q;

            if (s_tvalid_i && s_tready_o) in_v_q <= 1'b1;
            else if (run && finished)     in_v_q <= 1'b0;

            if (run && emit) begin
                m_tvalid_o <= 1'b1;
                m_tlast_o  <= msg_end || pkt_end;
                m_tuser_o  <= pkt_end && !msg_end;  // packet ended inside the message
            end else if (m_tready_i) begin
                m_tvalid_o <= 1'b0;
            end

            if (run) begin
                state_q     <= state_d;
                len_hi_q    <= len_hi_d;
                ptr_q       <= finished ? '0 : ptr_d;
                empty_msg_o <= empty;
                if (state_q == S_HDR) hbeat_q <= hbeat_q + 2'd1;
                if (state_q == S_VERD) begin
                    have_session_q <= 1'b1;
                    if (new_sess_q && have_session_q) begin
                        session_change_o <= 1'b1;
                        stale_o          <= 1'b1;
                    end
                    if (seq_lt) begin
                        dup_o      <= 1'b1;
                        expected_q <= exp_q;
                    end else begin
                        if (seq_gt) begin
                            gap_o   <= 1'b1;
                            stale_o <= 1'b1;
                        end
                        if (is_cnt_end) begin
                            heartbeat_o <= count_q == CNT_HB;
                            end_o       <= count_q == CNT_END;
                            expected_q  <= seq_full_q;
                        end else begin
                            upd_exp_q   <= 1'b1;
                        end
                    end
                end
                if (pkt_end) begin
                    state_q  <= S_HDR;
                    hbeat_q  <= '0;
                    len_hi_q <= 1'b0;
                    pkt_o    <= 1'b1;
                    // Well formed only if the parse ended exactly after the last message
                    // (or a body-less heartbeat / end of session). Bytes after the last
                    // message keep the parse in S_TRAIL, so the final step sees them as
                    // extra. Duplicates are not judged.
                    if (!drop_d && (in_user_q || state_d != S_TRAIL || extra)) begin
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
            // input contract: tkeep contiguous from lane 0, full unless tlast
            if (s_tvalid_i) begin
                assert (s_tkeep_i != '0 && (s_tkeep_i & (s_tkeep_i + 8'd1)) == '0);
                assert (s_tlast_i || s_tkeep_i == '1);
            end
            if (in_v_q) assert (ptr_q < in_nb_q || state_q == S_VERD);
            if (in_v_q) assert (finished == (ptr_d == in_nb_q && !to_verd));
            if (in_v_q && state_q == S_LEN && !straddle)
                assert (pz == (len == '0) && ple == (len <= 16'(room))
                        && peq == (len == 16'(room)));
            if (m_tvalid_o) begin
                assert (m_tkeep_o != '0);
                assert (!m_tuser_o || m_tlast_o);
            end
            assert (!(gap_o && dup_o));
        end
    end
`endif
endmodule
`default_nettype wire
