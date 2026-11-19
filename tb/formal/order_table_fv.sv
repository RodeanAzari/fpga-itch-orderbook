`default_nettype none
// Formal harness for order_table (buildplan §8.3: the order table never returns a stale
// entry after a delete). The property and its invariants sit in order_table under
// FORMAL. Small geometry (4 sets x 2 ways per bank, 4-bit shares, 16-level window) so
// buckets fill and overflow within reach; the table logic is the same at any size.
// Every input is free (any record, any type, any ready, mid-run resets); the output
// handshake is checked.
module order_table_fv (
    input wire logic        clk_i,
    input wire logic        rst_i,
    input wire logic [31:0] cfg_lo_price_i,
    input wire logic        s_valid_i,
    input wire logic [7:0]  s_type_i,
    input wire logic [63:0] s_seq_i,
    input wire logic [47:0] s_ts_i,
    input wire logic [63:0] s_ref_i,
    input wire logic [63:0] s_new_ref_i,
    input wire logic        s_sell_i,
    input wire logic [31:0] s_shares_i,
    input wire logic [31:0] s_price_i,
    input wire logic        m_ready_i
);
    localparam int SET_BITS = 2, WAYS = 2, SHARES_W = 4, WIN_BITS = 4;

    logic past = 1'b0;
    always_ff @(posedge clk_i) past <= 1'b1;
    always_comb if (!past) assume (rst_i);

    logic                s_ready, m_valid, m_better, m_dec, m_last, m_sell, m_stale;
    logic [1:0]          m_kind;
    logic [WIN_BITS-1:0] m_idx;
    logic [SHARES_W-1:0] m_shares;
    logic [63:0]         m_seq;
    logic [47:0]         m_ts;
    logic                m_user;

    order_table #(.SET_BITS(SET_BITS), .WAYS(WAYS), .SHARES_W(SHARES_W),
                  .WIN_BITS(WIN_BITS), .USER_W(1),
                  .HASH_M0(32'd1), .HASH_M1(32'd5)) dut (
        .clk_i, .rst_i, .cfg_lo_price_i,
        .s_valid_i, .s_ready_o(s_ready), .s_type_i, .s_seq_i, .s_ts_i, .s_user_i(1'b0),
        .s_ref_i, .s_new_ref_i, .s_sell_i, .s_shares_i, .s_price_i,
        .m_valid_o(m_valid), .m_ready_i, .m_kind_o(m_kind), .m_sell_o(m_sell),
        .m_idx_o(m_idx), .m_better_o(m_better), .m_dec_o(m_dec), .m_shares_o(m_shares),
        .m_last_o(m_last), .m_seq_o(m_seq), .m_ts_o(m_ts), .m_user_o(m_user),
        .m_stale_o(m_stale),
        .ref_overflow_o(), .duplicate_ref_o(), .shares_overflow_o(), .table_overflow_o(),
        .unknown_ref_o(), .over_reduce_o(), .price_oow_o(), .price_subpenny_o(),
        .stale_o(), .busy_o()
    );

    axis_hold #(.W(2 + 1 + WIN_BITS + 1 + 1 + SHARES_W + 1 + 64 + 48 + 1)) u_m_hold (
        .clk_i, .rst_i, .valid_i(m_valid), .ready_i(m_ready_i),
        .payload_i({m_kind, m_sell, m_idx, m_better, m_dec, m_shares, m_last, m_seq, m_ts,
                    m_user})
    );

    // Covers (R added, deleted, looked up again; a full table) are in order_table.
endmodule
`default_nettype wire
