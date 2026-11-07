`default_nettype none
// book_engine: decoded book records -> BBO updates. order_table (ADR 0001) feeding
// book_levels (ADR 0006) through its level-delta stream.
// Golden model: model/bounded.py BoundedBook.apply, one BBO per message whose BBO changed.
//
// Input / output and status: see order_table.sv (records, counters, stale) and
// book_levels.sv (BBO updates, bbo_oow). cfg_lo_price_i is the price window's low edge in
// ITCH price units, (base - 2^(WIN_BITS-1)) * 100 (ADR 0002).
// Latency: order_table's plus book_levels'; measured by test_book.py latency_matches_header.
// Reset: synchronous, active-high; both blocks clear their RAMs before taking input.
module book_engine #(
    parameter int SET_BITS = 13,
    parameter int WAYS     = 4,
    parameter int SHARES_W = 20,
    parameter int WIN_BITS = 12,
    parameter int USER_W   = 1,  // per-message sideband, carried to the BBO (see order_table)
    // ADR 0006: a level sums at most 2 * 2^SET_BITS * WAYS orders of < 2^SHARES_W shares.
    localparam int ENTRIES_BITS = SET_BITS + 1 + $clog2(WAYS),
    localparam int LEVEL_W      = SHARES_W + ENTRIES_BITS
) (
    input  wire logic          clk_i,
    input  wire logic          rst_i,
    input  wire logic [31:0]   cfg_lo_price_i,
    // records in (itch_decoder output)
    input  wire logic          s_valid_i,
    output logic               s_ready_o,
    input  wire logic [7:0]    s_type_i,
    input  wire logic [63:0]   s_seq_i,
    input  wire logic [47:0]   s_ts_i,
    input  wire logic [USER_W-1:0] s_user_i,
    input  wire logic [63:0]   s_ref_i,
    input  wire logic [63:0]   s_new_ref_i,
    input  wire logic          s_sell_i,
    input  wire logic [31:0]   s_shares_i,
    input  wire logic [31:0]   s_price_i,
    // BBO updates out
    output logic               m_valid_o,
    input  wire logic          m_ready_i,
    output logic [31:0]        m_bid_px_o,
    output logic [LEVEL_W-1:0] m_bid_sz_o,
    output logic [31:0]        m_ask_px_o,
    output logic [LEVEL_W-1:0] m_ask_sz_o,
    output logic               m_oow_o,
    output logic [63:0]        m_seq_o,
    output logic [47:0]        m_ts_o,
    output logic [USER_W-1:0]  m_user_o,
    output logic               m_stale_o,  // book stale after this update's cause message
    // status
    output logic               ref_overflow_o,
    output logic               duplicate_ref_o,
    output logic               shares_overflow_o,
    output logic               table_overflow_o,
    output logic               unknown_ref_o,
    output logic               over_reduce_o,
    output logic               price_oow_o,
    output logic               price_subpenny_o,
    output logic               bbo_oow_o,
    output logic               stale_o
);
    localparam int CNT_W = ENTRIES_BITS + 1;  // counts up to every entry

    logic                d_valid, d_ready, d_sell, d_better, d_dec, d_last;
    logic [1:0]          d_kind;
    logic [WIN_BITS-1:0] d_idx;
    logic [SHARES_W-1:0] d_shares;
    logic [63:0]         d_seq;
    logic [47:0]         d_ts;
    logic [USER_W-1:0]   d_user;
    logic                d_stale;
    logic                table_busy, levels_busy;

    order_table #(
        .SET_BITS(SET_BITS), .WAYS(WAYS), .SHARES_W(SHARES_W), .WIN_BITS(WIN_BITS),
        .USER_W(USER_W)
    ) u_table (
        .clk_i(clk_i), .rst_i(rst_i), .cfg_lo_price_i(cfg_lo_price_i),
        .s_valid_i(s_valid_i), .s_ready_o(s_ready_o), .s_type_i(s_type_i),
        .s_seq_i(s_seq_i), .s_ts_i(s_ts_i), .s_user_i(s_user_i), .s_ref_i(s_ref_i), .s_new_ref_i(s_new_ref_i),
        .s_sell_i(s_sell_i), .s_shares_i(s_shares_i), .s_price_i(s_price_i),
        .m_valid_o(d_valid), .m_ready_i(d_ready), .m_kind_o(d_kind), .m_sell_o(d_sell),
        .m_idx_o(d_idx), .m_better_o(d_better), .m_dec_o(d_dec), .m_shares_o(d_shares),
        .m_last_o(d_last), .m_seq_o(d_seq), .m_ts_o(d_ts), .m_user_o(d_user), .m_stale_o(d_stale),
        .ref_overflow_o(ref_overflow_o), .duplicate_ref_o(duplicate_ref_o),
        .shares_overflow_o(shares_overflow_o), .table_overflow_o(table_overflow_o),
        .unknown_ref_o(unknown_ref_o), .over_reduce_o(over_reduce_o),
        .price_oow_o(price_oow_o), .price_subpenny_o(price_subpenny_o),
        .stale_o(stale_o), .busy_o(table_busy)
    );

    book_levels #(
        .WIN_BITS(WIN_BITS), .SHARES_W(SHARES_W), .LEVEL_W(LEVEL_W), .CNT_W(CNT_W),
        .USER_W(USER_W + 1)
    ) u_levels (
        .clk_i(clk_i), .rst_i(rst_i), .cfg_lo_price_i(cfg_lo_price_i),
        .s_valid_i(d_valid), .s_ready_o(d_ready), .s_kind_i(d_kind), .s_sell_i(d_sell),
        .s_idx_i(d_idx), .s_better_i(d_better), .s_dec_i(d_dec), .s_shares_i(d_shares),
        .s_last_i(d_last), .s_seq_i(d_seq), .s_ts_i(d_ts), .s_user_i({d_user, d_stale}),
        .m_valid_o(m_valid_o), .m_ready_i(m_ready_i), .m_bid_px_o(m_bid_px_o),
        .m_bid_sz_o(m_bid_sz_o), .m_ask_px_o(m_ask_px_o), .m_ask_sz_o(m_ask_sz_o),
        .m_oow_o(m_oow_o), .m_seq_o(m_seq_o), .m_ts_o(m_ts_o), .m_user_o({m_user_o, m_stale_o}),
        .bbo_oow_o(bbo_oow_o), .busy_o(levels_busy)
    );

    // busy outputs are for debug probes only
    logic unused_busy;
    assign unused_busy = table_busy ^ levels_busy;
endmodule
`default_nettype wire
