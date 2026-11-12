`default_nettype none
// out_mux: routes decoded records by mode and packs the wire records for out_builder.
//   mode 0 (BBO):   feed records -> book (f_* -> b_s_*); book BBOs -> BBO records.
//   mode 1 (trace): feed records -> trace records; the book gets nothing.
// Wire layouts: docs/protocol.md v1 (BBO record, trace record), byte 0 in the top 8 bits
// of o_rec_o; a trace record fills the top 40 bytes, the rest is 0.
// Golden model: model/protocol.py bbo_record (with stale = feed stale OR book stale, as
// in expected()) and trace_record. The decoder already zeroes the fields a type does
// not use (itch_decoder header), so trace fields are placed as they come.
// BBO flags: stale, crossed, locked (both sides non-empty: size != 0), out_of_window;
// t0/t1/t2 (ADR 0011) come from ob_core; cut_through = the build's CUT_THROUGH (ADR 0013).
// The record fields of the book's input are wired straight from the feed in the top
// level; only the handshake passes through here. Purely combinational (0 cycles).
// mode_i changes only while the pipeline is in reset (a config).
module out_mux #(
    parameter int LEVEL_W = 36,  // book_engine's size width (36 at ADR 0001's geometry), <= 48
    parameter bit CUT_THROUGH = 1'b0  // eth_rx build mode, reported in every BBO's flags
) (
    input  wire logic               mode_i,
    // feed records in
    input  wire logic               f_valid_i,
    output logic                    f_ready_o,
    input  wire logic [7:0]         f_type_i,
    input  wire logic [63:0]        f_seq_i,
    input  wire logic [47:0]        f_ts_i,
    input  wire logic [63:0]        f_ref_i,
    input  wire logic [63:0]        f_new_ref_i,
    input  wire logic               f_sell_i,
    input  wire logic [31:0]        f_shares_i,
    input  wire logic [31:0]        f_price_i,
    // book input handshake
    output logic                    b_s_valid_o,
    input  wire logic               b_s_ready_i,
    // book BBOs in
    input  wire logic               b_m_valid_i,
    output logic                    b_m_ready_o,
    input  wire logic [31:0]        b_bid_px_i,
    input  wire logic [LEVEL_W-1:0] b_bid_sz_i,
    input  wire logic [31:0]        b_ask_px_i,
    input  wire logic [LEVEL_W-1:0] b_ask_sz_i,
    input  wire logic               b_oow_i,
    input  wire logic [63:0]        b_seq_i,
    input  wire logic [47:0]        b_ts_i,
    input  wire logic               b_feed_stale_i,  // book_engine m_user_o
    input  wire logic               b_book_stale_i,  // book_engine m_stale_o
    input  wire logic [63:0]        b_t0_i,
    input  wire logic [63:0]        b_t1_i,
    input  wire logic [63:0]        b_t2_i,
    // records out (out_builder s_*)
    output logic                    o_valid_o,
    input  wire logic               o_ready_i,
    output logic [64*8-1:0]         o_rec_o
);
    // docs/protocol.md BBO flags
    localparam int F_STALE = 0, F_CROSSED = 1, F_LOCKED = 2, F_OOW = 3, F_CT = 4;

    logic        both;
    logic [15:0] flags;
    logic [64*8-1:0] bbo, trace;

    assign both = b_bid_sz_i != '0 && b_ask_sz_i != '0;
    always_comb begin
        flags = '0;
        flags[F_STALE]   = b_feed_stale_i | b_book_stale_i;
        flags[F_CROSSED] = both && b_bid_px_i > b_ask_px_i;
        flags[F_LOCKED]  = both && b_bid_px_i == b_ask_px_i;
        flags[F_OOW]     = b_oow_i;
        flags[F_CT]      = CUT_THROUGH;
    end

    // BBO record: symbol(2) flags(2) bid_px(4) ask_px(4) bid_sz(6) ask_sz(6) itch_ts(6)
    //   pad(2) mold_seq(8) t0(8) t1(8) t2(8)
    assign bbo = {16'd0, flags, b_bid_px_i, b_ask_px_i, 48'(b_bid_sz_i), 48'(b_ask_sz_i),
                  b_ts_i, 16'd0, b_seq_i, b_t0_i, b_t1_i, b_t2_i};
    // trace record: msg_type(1) flags(1) itch_ts(6) mold_seq(8) ref(8) new_ref(8)
    //   shares(4) price(4); then 24 bytes of 0
    assign trace = {f_type_i, 7'd0, f_sell_i, f_ts_i, f_seq_i, f_ref_i, f_new_ref_i,
                    f_shares_i, f_price_i, 192'd0};

    always_comb begin
        if (mode_i) begin
            o_valid_o   = f_valid_i;
            f_ready_o   = o_ready_i;
            o_rec_o     = trace;
            b_s_valid_o = 1'b0;
            b_m_ready_o = 1'b0;
        end else begin
            o_valid_o   = b_m_valid_i;
            b_m_ready_o = o_ready_i;
            o_rec_o     = bbo;
            b_s_valid_o = f_valid_i;
            f_ready_o   = b_s_ready_i;
        end
    end

    if (LEVEL_W > 48) begin : g_level_w_check
        $error("out_mux: LEVEL_W > 48 does not fit the 6-byte size field");
    end
endmodule
`default_nettype wire
