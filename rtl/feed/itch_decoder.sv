`default_nettype none
// itch_decoder: ITCH 5.0 messages -> decoded book records for one symbol (ADR 0004).
// Golden model: model/moldudp64.py FeedDecoder (records and counters).
//
// Input:  AXI-Stream, one ITCH message per tlast, DATA_W/8 byte lanes, message byte 0 in
//         lane 0 of the first beat (DATA_W 8: mold_deframer output; DATA_W 64:
//         msg_aligner output, ADR 0015): every beat full except the last, whose tkeep is
//         contiguous from lane 0. s_seq_i is the message's MoldUDP64 sequence number.
//         s_tuser_i on tlast = discard the message (already counted upstream).
// Output: one record per accepted book message (A F E C X D U for the configured
//         stock), valid/ready. Field meaning per type (ITCH 5.0, docs/buildplan.md §5.3):
//           A/F  ref, side, shares, price      E/C/X  ref, shares
//           D    ref                            U      ref (original), new_ref, shares, price
//         Unused fields are 0. C's execution price is not decoded: the book uses the
//         resting price. m_stale_o (ADR 0016): s_stale_i of the message, or a malformed
//         message decided before it (model Record.stale, in stream order).
//
// Checks, in the model's order:
//   1. a type outside §5.3                     -> unknown_o (skipped by length)
//   2. length differs from the type's spec     -> malformed_o
//   3. R: stock == cfg_symbol_i -> learn its locate (dir_match_o; locate_change_o if it
//      replaces a different one). S: nothing.
//   4. book type, locate != learned (or none)  -> other_locate_o
//   5. A/F with a side other than B/S          -> malformed_o
//   6. otherwise                               -> record
// A zero-length message never reaches here (mold_deframer counts it).
//
// Structure: every field offset in §5.3 is fixed from the start of the message, so each
// byte is written to a fixed place in a capture register as it streams in, selected by
// its byte index alone (no shifting, no length-dependent muxing): byte k arrives in beat
// k / LANES, lane k % LANES, so each capture byte has one fixed source lane. Five capture
// registers cover every field of every subset type:
//   loc   bytes 1..2      ts   bytes 5..10      w11  bytes 11..18 (ref; R: stock)
//   w19   bytes 19..26 (A/F side 19, shares 20..23; E/C/X shares 19..22; U new ref)
//   w27   bytes 27..35 (U shares 27..30, price 31..34; A/F price 32..35)
// The type and length check are registered on the last byte (stage 1, the T1 event);
// the locate/side checks and the record are formed one cycle later (stage 2), from
// registers only. The next message's first beat may be accepted during stage 2 (a beat
// is accepted only when stage 2 may run, so stage 2 is never later than that): its
// writes land at the end of the cycle in which stage 2 reads the old values.
//
// Latency: record valid 2 cycles after the message's last beat is accepted (t1_o pulses
// 1 cycle after it). Throughput: 1 beat/cycle; the input stalls only while a record
// waits for m_ready_i.
// Configuration: cfg_symbol_i (8 ASCII bytes, space padded, as in the R message);
// cfg_locate_valid_i / cfg_locate_i preload the locate at reset (host fallback).
// Reset: synchronous, active-high.
module itch_decoder #(
    parameter int DATA_W = 8
) (
    input  wire logic              clk_i,
    input  wire logic              rst_i,
    // configuration
    input  wire logic [63:0]       cfg_symbol_i,
    input  wire logic              cfg_locate_valid_i,
    input  wire logic [15:0]       cfg_locate_i,
    // messages in
    input  wire logic [DATA_W-1:0] s_tdata_i,
    input  wire logic [DATA_W/8-1:0] s_tkeep_i,
    input  wire logic              s_tvalid_i,
    output logic                   s_tready_o,
    input  wire logic              s_tlast_i,
    input  wire logic              s_tuser_i,
    input  wire logic [63:0]       s_seq_i,
    input  wire logic [63:0]       s_t0_i,    // T0 of the message's frame (ADR 0011)
    input  wire logic              s_stale_i, // in-band stale, constant in a message (0016)
    input  wire logic [63:0]       now_i,     // timestamp counter
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
    // status
    output logic [63:0]            m_t0_o,
    output logic [63:0]            m_t1_o,    // now_i when the last byte was accepted
    output logic                   m_stale_o, // s_stale_i, or a malformed message before
    output logic                   t1_o,
    output logic                   unknown_o,
    output logic                   malformed_o,
    output logic                   other_locate_o,
    output logic                   dir_match_o,
    output logic                   locate_change_o,
    output logic                   locate_valid_o,
    output logic [15:0]            locate_o
);
    // ITCH 5.0 (docs/buildplan.md §5.3): message types and lengths
    localparam logic [7:0] T_S = 8'h53, T_R = 8'h52, T_A = 8'h41, T_F = 8'h46, T_E = 8'h45,
                           T_C = 8'h43, T_X = 8'h58, T_D = 8'h44, T_U = 8'h55;
    localparam int L_S = 12, L_R = 39, L_A = 36, L_F = 40, L_E = 31, L_C = 36, L_X = 23,
                   L_D = 19, L_U = 35;
    // Capture-register start offsets (ITCH 5.0 §1.2 header, §1.3+ bodies)
    localparam int OFF_LOC = 1;   // stock locate, 2 bytes
    localparam int OFF_TS  = 5;   // timestamp, 6 bytes
    localparam int OFF_W11 = 11;  // order ref / R stock, 8 bytes
    localparam int OFF_W19 = 19;  // 8 bytes
    localparam int OFF_W27 = 27;  // 9 bytes
    localparam logic [7:0] SIDE_B = 8'h42, SIDE_S = 8'h53;
    localparam int IDX_W = 6;     // byte index into the capture registers (< 64)
    localparam int LANES = DATA_W / 8;
    localparam int SH    = $clog2(LANES);   // byte index -> beat index

    if (DATA_W != 8 && DATA_W != 64) begin : g_bad_width
        $error("itch_decoder: DATA_W must be 8 or 64 (ADR 0005, 0015)");
    end

    // Capture byte at message offset o: its beat index and its lane.
    function automatic logic [IDX_W-1:0] beat_of(input int o);
        return IDX_W'(o >> SH);
    endfunction
    function automatic logic [7:0] byte_of(input logic [DATA_W-1:0] d, input int o);
        return d[8 * (o % LANES) +: 8];
    endfunction

    function automatic logic [15:0] spec_len(input logic [7:0] t);
        unique case (t)
            T_S:     spec_len = 16'(L_S);
            T_R:     spec_len = 16'(L_R);
            T_A:     spec_len = 16'(L_A);
            T_F:     spec_len = 16'(L_F);
            T_E:     spec_len = 16'(L_E);
            T_C:     spec_len = 16'(L_C);
            T_X:     spec_len = 16'(L_X);
            T_D:     spec_len = 16'(L_D);
            T_U:     spec_len = 16'(L_U);
            default: spec_len = '0;
        endcase
    endfunction

    // ---------------------------------------------------------------- capture
    logic [15:0] len_q;      // bytes of the current message accepted so far (saturating;
                             // a multiple of LANES until the last beat)
    logic [7:0]  type_q;
    // Capture registers (datapath, not reset). Byte k of a field lives at [(N-1-k)*8 +: 8].
    logic [15:0] loc_q;
    logic [47:0] ts_q;
    logic [63:0] w11_q, w19_q;
    logic [71:0] w27_q;

    logic             fire, done;
    logic [7:0]       kind;
    logic [IDX_W-1:0] idx;   // beat index in the message
    logic             idx_ok;
    logic [15:0]      nb, len_sum;

    always_comb begin
        s_tready_o = !m_valid_o || m_ready_i;
        fire       = s_tvalid_i && s_tready_o;
        done       = fire && s_tlast_i && !s_tuser_i;
        kind       = (len_q == '0) ? s_tdata_i[7:0] : type_q;
        idx        = IDX_W'(len_q >> SH);
        idx_ok     = len_q < 16'(1 << IDX_W);
        nb         = '0;
        for (int i = 0; i < LANES; i++) nb += 16'(s_tkeep_i[i]);
        len_sum    = len_q + nb;   // bytes including this beat
    end

    always_ff @(posedge clk_i) begin
        if (fire) begin
            // Bytes past a short last beat's tkeep may be captured; they only matter in a
            // message whose length check fails, and then no field is used.
            if (len_q == '0) type_q <= s_tdata_i[7:0];
            for (int k = 0; k < 2; k++)
                if (idx_ok && idx == beat_of(OFF_LOC + k))
                    loc_q[(1 - k) * 8 +: 8] <= byte_of(s_tdata_i, OFF_LOC + k);
            for (int k = 0; k < 6; k++)
                if (idx_ok && idx == beat_of(OFF_TS + k))
                    ts_q[(5 - k) * 8 +: 8] <= byte_of(s_tdata_i, OFF_TS + k);
            for (int k = 0; k < 8; k++) begin
                if (idx_ok && idx == beat_of(OFF_W11 + k))
                    w11_q[(7 - k) * 8 +: 8] <= byte_of(s_tdata_i, OFF_W11 + k);
                if (idx_ok && idx == beat_of(OFF_W19 + k))
                    w19_q[(7 - k) * 8 +: 8] <= byte_of(s_tdata_i, OFF_W19 + k);
            end
            for (int k = 0; k < 9; k++)
                if (idx_ok && idx == beat_of(OFF_W27 + k))
                    w27_q[(8 - k) * 8 +: 8] <= byte_of(s_tdata_i, OFF_W27 + k);
        end
    end

    // ---------------------------------------------------- stage 1: type, length
    logic        pend_q;     // a finished message awaits stage 2
    logic [7:0]  d_kind_q;
    logic        d_known_q, d_len_ok_q;
    logic [63:0] d_seq_q, d_t0_q, d_t1_q;
    logic        d_stale_q;
    logic        bad_q;  // a malformed message has been decided (sticky until reset)

    always_ff @(posedge clk_i) begin
        if (done) begin
            d_kind_q   <= kind;
            d_known_q  <= spec_len(kind) != '0;
            d_len_ok_q <= len_sum == spec_len(kind);
            d_seq_q    <= s_seq_i;
            d_t0_q     <= s_t0_i;   // constant within a message (one packet)
            d_stale_q  <= s_stale_i;
            d_t1_q     <= now_i;    // T1: the message's last byte accepted
        end
    end

    // ------------------------------------------- stage 2: locate, side, record
    logic        go;         // stage 2 runs this cycle
    logic        is_add, is_red, is_u, is_book, loc_ok, side_ok, sym_ok;
    logic [7:0]  side;

    always_comb begin
        go      = pend_q && (!m_valid_o || m_ready_i);
        is_add  = d_kind_q == T_A || d_kind_q == T_F;
        is_red  = d_kind_q == T_E || d_kind_q == T_C || d_kind_q == T_X;
        is_u    = d_kind_q == T_U;
        is_book = is_add || is_red || is_u || d_kind_q == T_D;
        loc_ok  = locate_valid_o && loc_q == locate_o;
        side    = w19_q[63:56];                      // byte 19
        side_ok = !is_add || side == SIDE_B || side == SIDE_S;
        sym_ok  = w11_q == cfg_symbol_i;             // R: stock, bytes 11..18
    end

    always_ff @(posedge clk_i) begin
        if (go && d_known_q && d_len_ok_q && is_book && loc_ok && side_ok) begin
            m_type_o    <= d_kind_q;
            m_seq_o     <= d_seq_q;
            m_t0_o      <= d_t0_q;
            m_t1_o      <= d_t1_q;
            // stage 2 runs in message order. malformed_o: a malformed message's stage 2
            // on the previous cycle (unreachable with ITCH's >= 19-byte book messages;
            // kept so this does not rest on message lengths)
            m_stale_o   <= d_stale_q || bad_q || malformed_o;
            m_ts_o      <= ts_q;
            m_ref_o     <= w11_q;
            m_new_ref_o <= is_u ? w19_q : '0;
            m_sell_o    <= is_add && side == SIDE_S;
            m_shares_o  <= is_add ? w19_q[55:24]     // bytes 20..23
                         : is_red ? w19_q[63:32]     // bytes 19..22
                         : is_u   ? w27_q[71:40]     // bytes 27..30
                         : '0;
            m_price_o   <= is_add ? w27_q[31:0]      // bytes 32..35
                         : is_u   ? w27_q[39:8]      // bytes 31..34
                         : '0;
        end
    end

    // ---------------------------------------------------------------- control
    always_ff @(posedge clk_i) begin
        if (rst_i) begin
            len_q           <= '0;
            pend_q          <= 1'b0;
            m_valid_o       <= 1'b0;
            t1_o            <= 1'b0;
            unknown_o       <= 1'b0;
            malformed_o     <= 1'b0;
            bad_q           <= 1'b0;
            other_locate_o  <= 1'b0;
            dir_match_o     <= 1'b0;
            locate_change_o <= 1'b0;
            locate_valid_o  <= cfg_locate_valid_i;
            locate_o        <= cfg_locate_i;
        end else begin
            t1_o            <= done;
            unknown_o       <= 1'b0;
            malformed_o     <= 1'b0;
            other_locate_o  <= 1'b0;
            dir_match_o     <= 1'b0;
            locate_change_o <= 1'b0;
            if (m_ready_i) m_valid_o <= 1'b0;
            if (malformed_o) bad_q <= 1'b1;

            if (fire) len_q <= s_tlast_i ? '0 : (len_sum < len_q ? '1 : len_sum);

            if (go) begin
                pend_q <= 1'b0;
                if (!d_known_q) begin
                    unknown_o <= 1'b1;
                end else if (!d_len_ok_q) begin
                    malformed_o <= 1'b1;
                end else if (d_kind_q == T_R) begin
                    if (sym_ok) begin
                        dir_match_o     <= 1'b1;
                        locate_change_o <= locate_valid_o && loc_q != locate_o;
                        locate_valid_o  <= 1'b1;
                        locate_o        <= loc_q;
                    end
                end else if (is_book) begin
                    if (!loc_ok)       other_locate_o <= 1'b1;
                    else if (!side_ok) malformed_o    <= 1'b1;
                    else               m_valid_o      <= 1'b1;
                end
            end
            if (done) pend_q <= 1'b1;  // after go: a 1-byte message may finish during go
        end
    end

`ifndef SYNTHESIS
    always_ff @(posedge clk_i) begin
        if (!rst_i) begin
            assert ($countones({unknown_o, malformed_o, other_locate_o, dir_match_o}) <= 1);
            // stage 2 must have run before another message can finish
            assert (!(done && pend_q && !go));
            // input shape: tkeep contiguous from lane 0, full unless tlast
            if (s_tvalid_i) begin
                assert (s_tkeep_i[0] && (s_tkeep_i & (s_tkeep_i + 1'b1)) == '0);
                assert (s_tlast_i || &s_tkeep_i);
            end
        end
    end
`endif
endmodule
`default_nettype wire
