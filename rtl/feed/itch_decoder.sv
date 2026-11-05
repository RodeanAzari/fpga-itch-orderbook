`default_nettype none
// itch_decoder: ITCH 5.0 messages -> decoded book records for one symbol (ADR 0004).
// Golden model: model/moldudp64.py FeedDecoder (records and counters).
//
// Input:  AXI-Stream, one byte per beat, one ITCH message per tlast (mold_deframer
//         output). s_seq_i is the message's MoldUDP64 sequence number. s_tuser_i on
//         tlast = discard the message (already counted upstream).
// Output: one record per accepted book message (A F E C X D U for the configured
//         stock), valid/ready. Field meaning per type (ITCH 5.0, CLAUDE.md §5.3):
//           A/F  ref, side, shares, price      E/C/X  ref, shares
//           D    ref                            U      ref (original), new_ref, shares, price
//         Unused fields are 0. C's execution price is not decoded: the book uses the
//         resting price.
//
// Checks, in the model's order, on the message's last byte:
//   1. zero length or a type outside §5.3      -> unknown_o (skipped by length)
//   2. length differs from the type's spec     -> malformed_o
//   3. R: stock == cfg_symbol_i -> learn its locate (dir_match_o; locate_change_o if it
//      replaces a different one). S: nothing.
//   4. book type, locate != learned (or none)  -> other_locate_o
//   5. A/F with a side other than B/S          -> malformed_o
//   6. otherwise                               -> record
// A zero-length message never reaches here (mold_deframer counts it).
//
// Fields are taken from a shift register holding the last MAX_LEN bytes: once the length
// equals the spec, every field sits at a fixed distance from the message's last byte.
//
// Latency: record valid 1 cycle after the message's last byte is accepted (that byte is
// the T1 event, t1_o). Throughput: 1 byte/cycle; the input stalls only while a record
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
    input  wire logic              s_tvalid_i,
    output logic                   s_tready_o,
    input  wire logic              s_tlast_i,
    input  wire logic              s_tuser_i,
    input  wire logic [63:0]       s_seq_i,
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
    output logic                   t1_o,
    output logic                   unknown_o,
    output logic                   malformed_o,
    output logic                   other_locate_o,
    output logic                   dir_match_o,
    output logic                   locate_change_o,
    output logic                   locate_valid_o,
    output logic [15:0]            locate_o
);
    // ITCH 5.0 (CLAUDE.md §5.3): message types and lengths
    localparam logic [7:0] T_S = 8'h53, T_R = 8'h52, T_A = 8'h41, T_F = 8'h46, T_E = 8'h45,
                           T_C = 8'h43, T_X = 8'h58, T_D = 8'h44, T_U = 8'h55;
    localparam int L_S = 12, L_R = 39, L_A = 36, L_F = 40, L_E = 31, L_C = 36, L_X = 23,
                   L_D = 19, L_U = 35;
    localparam int MAX_LEN = 40;          // longest type in the subset (F)
    // Common header: type 0, locate 1, tracking 3, timestamp 5 (6 bytes)
    localparam int OFF_LOCATE = 1;
    localparam int OFF_TS     = 5;
    // Body offsets (ITCH 5.0 §1.3+): Add 'A' ref 11, side 19, shares 20, stock 24,
    // price 32 ('F' the same, plus MPID); E/C/X ref 11, shares 19; D ref 11;
    // U orig ref 11, new ref 19, shares 27, price 31; R stock 11.
    localparam int OFF_REF        = 11;
    localparam int OFF_ADD_SIDE   = 19;
    localparam int OFF_ADD_SHARES = 20;
    localparam int OFF_ADD_PRICE  = 32;
    localparam int OFF_RED_SHARES = 19;
    localparam int OFF_U_NEWREF   = 19;
    localparam int OFF_U_SHARES   = 27;
    localparam int OFF_U_PRICE    = 31;
    localparam int OFF_R_STOCK    = 11;
    localparam logic [7:0] SIDE_B = 8'h42, SIDE_S = 8'h53;

    if (DATA_W != 8) begin : g_bad_width
        $error("itch_decoder: only DATA_W = 8 is implemented (ADR 0005)");
    end

    // Field at byte offset OFF, N bytes, of a message of length L held right-aligned in
    // win (last byte in win[7:0]).
    function automatic logic [63:0] field(input logic [MAX_LEN*8-1:0] win, input int l,
                                          input int off, input int n);
        logic [63:0] v;
        v = '0;
        for (int i = 0; i < n; i++) v = {v[55:0], win[(l - 1 - off - i) * 8 +: 8]};
        return v;
    endfunction

    logic [(MAX_LEN-1)*8-1:0] win_q;  // previous MAX_LEN-1 bytes, the latest at [7:0]
    logic [15:0]          len_q;    // bytes seen so far (saturating)
    logic [7:0]           type_q;

    logic                 fire, done;
    logic [MAX_LEN*8-1:0] win;      // including this cycle's byte
    logic [15:0]          len;
    logic [7:0]           kind;
    logic                 known, len_ok, is_book, is_add, is_red, is_u, loc_ok, side_ok;
    logic [15:0]          loc;
    logic [7:0]           side;
    int                   spec;

    always_comb begin
        s_tready_o = !m_valid_o || m_ready_i;
        fire       = s_tvalid_i && s_tready_o;
        done       = fire && s_tlast_i && !s_tuser_i;
        win        = {win_q, s_tdata_i};
        len        = len_q + 16'd1;
        kind       = (len_q == '0) ? s_tdata_i : type_q;

        unique case (kind)
            T_S:     spec = L_S;
            T_R:     spec = L_R;
            T_A:     spec = L_A;
            T_F:     spec = L_F;
            T_E:     spec = L_E;
            T_C:     spec = L_C;
            T_X:     spec = L_X;
            T_D:     spec = L_D;
            T_U:     spec = L_U;
            default: spec = 0;
        endcase
        known   = spec != 0;
        len_ok  = known && len == 16'(spec);
        is_add  = kind == T_A || kind == T_F;
        is_red  = kind == T_E || kind == T_C || kind == T_X;
        is_u    = kind == T_U;
        is_book = is_add || is_red || is_u || kind == T_D;
        loc     = 16'(field(win, spec, OFF_LOCATE, 2));
        loc_ok  = locate_valid_o && loc == locate_o;
        side    = 8'(field(win, spec, OFF_ADD_SIDE, 1));
        side_ok = !is_add || side == SIDE_B || side == SIDE_S;
    end

    // Datapath (not reset): window and record fields.
    always_ff @(posedge clk_i) begin
        if (fire) begin
            win_q <= win[(MAX_LEN-1)*8-1:0];
            if (len_q == '0) type_q <= s_tdata_i;
        end
        if (done && len_ok && is_book && loc_ok && side_ok) begin
            m_type_o    <= kind;
            m_seq_o     <= s_seq_i;
            m_ts_o      <= 48'(field(win, spec, OFF_TS, 6));
            m_ref_o     <= field(win, spec, OFF_REF, 8);
            m_new_ref_o <= is_u ? field(win, spec, OFF_U_NEWREF, 8) : '0;
            m_sell_o    <= is_add && side == SIDE_S;
            m_shares_o  <= is_add ? 32'(field(win, spec, OFF_ADD_SHARES, 4))
                         : is_red ? 32'(field(win, spec, OFF_RED_SHARES, 4))
                         : is_u   ? 32'(field(win, spec, OFF_U_SHARES, 4)) : '0;
            m_price_o   <= is_add ? 32'(field(win, spec, OFF_ADD_PRICE, 4))
                         : is_u   ? 32'(field(win, spec, OFF_U_PRICE, 4)) : '0;
        end
    end

    // Control.
    always_ff @(posedge clk_i) begin
        if (rst_i) begin
            len_q           <= '0;
            m_valid_o       <= 1'b0;
            t1_o            <= 1'b0;
            unknown_o       <= 1'b0;
            malformed_o     <= 1'b0;
            other_locate_o  <= 1'b0;
            dir_match_o     <= 1'b0;
            locate_change_o <= 1'b0;
            locate_valid_o  <= cfg_locate_valid_i;
            locate_o        <= cfg_locate_i;
        end else begin
            t1_o            <= 1'b0;
            unknown_o       <= 1'b0;
            malformed_o     <= 1'b0;
            other_locate_o  <= 1'b0;
            dir_match_o     <= 1'b0;
            locate_change_o <= 1'b0;
            if (m_ready_i) m_valid_o <= 1'b0;

            if (fire) len_q <= s_tlast_i ? '0 : (len_q == '1 ? len_q : len);

            if (done) begin
                t1_o <= 1'b1;
                if (!known) begin
                    unknown_o <= 1'b1;
                end else if (!len_ok) begin
                    malformed_o <= 1'b1;
                end else if (kind == T_R) begin
                    if (field(win, L_R, OFF_R_STOCK, 8) == cfg_symbol_i) begin
                        dir_match_o     <= 1'b1;
                        locate_change_o <= locate_valid_o && loc != locate_o;
                        locate_valid_o  <= 1'b1;
                        locate_o        <= loc;
                    end
                end else if (is_book) begin
                    if (!loc_ok)       other_locate_o <= 1'b1;
                    else if (!side_ok) malformed_o    <= 1'b1;
                    else               m_valid_o      <= 1'b1;
                end
            end
        end
    end

`ifndef SYNTHESIS
    always_ff @(posedge clk_i) begin
        if (!rst_i) assert ($countones({unknown_o, malformed_o, other_locate_o,
                                        dir_match_o}) <= 1);
    end
`endif
endmodule
`default_nettype wire
