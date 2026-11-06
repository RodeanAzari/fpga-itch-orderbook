`default_nettype none
// book_levels: level deltas -> best bid/offer updates (ADR 0006, ADR 0002).
// Golden model: model/bounded.py BoundedBook (levels, out-of-window flag, BBO stream).
//
// Input:  order_table's delta beats, valid/ready (kinds K_NONE / K_LEVEL / K_OOW, see
//         order_table.sv). The BBO is evaluated once per message, after its m_last beat.
// Output: one BBO update per message whose BBO changed, valid/ready: best bid price and
//         size, best ask price and size (price 0 and size 0 for an empty side), the
//         out-of-window flag, and the message's seq and ts. Prices are ITCH price units.
//
// Per side (0 = bid, 1 = ask):
//   - level RAM: 2^WIN_BITS x LEVEL_W aggregate shares, indexed by the window index
//     (ascending price on both sides). LEVEL_W = SHARES_W + log2(table entries) makes
//     overflow impossible (ADR 0006); an assertion checks every update.
//   - occupancy: L1, 2^HI_BITS bits in registers (group has a non-empty level); L2,
//     2^HI_BITS words of 2^LO_BITS bits in distributed RAM (one bit per level).
//   - best index / size / valid, and the out-of-window counters per edge (ADR 0002).
// A delta's level is read, updated and written back; its occupancy bit is set on
// 0 -> non-zero and cleared on -> 0 (and its L1 bit when the L2 word empties). A level
// that improves on the best becomes the best; a best that empties is replaced by a
// search: priority-encode L1 (highest group for bids, lowest for asks), then the L2 word
// of that group, then read the new best's size.
// Out-of-window flag (ADR 0002): a side has a live order past its better edge, or has no
// in-window level and a live order past its worse edge.
//
// Latency (cycle 0 = the delta's handshake cycle), one delta at a time. A last delta
// presents its BBO update in cycle 8 for K_LEVEL (read, output register, modify, bitmap,
// done, form, compare, present), 14 when the best empties (+ L1 encode, L2 read, L2
// encode, size read, output register, size set), 4 for K_NONE and 5 for K_OOW. A non-last delta frees the
// input after 6 (K_LEVEL). Measured end to end by test_book.py latency_matches_header.
// After reset the level RAMs and L2 are cleared, one index per cycle (2^WIN_BITS cycles).
// Reset: synchronous, active-high.
module book_levels #(
    parameter int WIN_BITS = 12,
    parameter int SHARES_W = 20,
    parameter int LEVEL_W  = 36,
    parameter int CNT_W    = 17
) (
    input  wire logic                clk_i,
    input  wire logic                rst_i,
    // configuration
    input  wire logic [31:0]         cfg_lo_price_i,
    // level deltas in
    input  wire logic                s_valid_i,
    output logic                     s_ready_o,
    input  wire logic [1:0]          s_kind_i,
    input  wire logic                s_sell_i,
    input  wire logic [WIN_BITS-1:0] s_idx_i,
    input  wire logic                s_better_i,
    input  wire logic                s_dec_i,
    input  wire logic [SHARES_W-1:0] s_shares_i,
    input  wire logic                s_last_i,
    input  wire logic [63:0]         s_seq_i,
    input  wire logic [47:0]         s_ts_i,
    // BBO updates out
    output logic                     m_valid_o,
    input  wire logic                m_ready_i,
    output logic [31:0]              m_bid_px_o,
    output logic [LEVEL_W-1:0]       m_bid_sz_o,
    output logic [31:0]              m_ask_px_o,
    output logic [LEVEL_W-1:0]       m_ask_sz_o,
    output logic                     m_oow_o,
    output logic [63:0]              m_seq_o,
    output logic [47:0]              m_ts_o,
    // status
    output logic                     bbo_oow_o,
    output logic                     busy_o
);
    localparam logic [1:0] K_NONE = 2'd0, K_LEVEL = 2'd1, K_OOW = 2'd2;
    localparam int LO_BITS = WIN_BITS / 2;       // level within a group (L2 word bit)
    localparam int HI_BITS = WIN_BITS - LO_BITS; // group (L1 bit, L2 word address)
    localparam int GROUPS  = 2 ** HI_BITS;
    localparam int GW      = 2 ** LO_BITS;
    localparam int TICK    = 100;                // ITCH price units per tick (1 cent)

    typedef enum logic [4:0] {
        S_CLEAR, S_IDLE, S_OOW, S_RD, S_RDREG, S_MOD, S_BIT, S_FIND1, S_FIND2,
        S_FIND3, S_SZ, S_SZREG, S_SZSET, S_DONE, S_BBO1, S_BBO2, S_EMIT
    } state_t;
    state_t state_q;
    logic [WIN_BITS-1:0] clear_q;

    // Delta registers (datapath, not reset)
    logic                sell_q, better_q, dec_q, last_q;
    logic [WIN_BITS-1:0] idx_q;
    logic [SHARES_W-1:0] n_q;
    logic [63:0]         seq_q;
    logic [47:0]         ts_q;

    // Priority encoders: highest set bit for bids (sell = 0), lowest for asks.
    function automatic logic [HI_BITS-1:0] pe_group(input logic [GROUPS-1:0] v, input logic sell);
        logic [HI_BITS-1:0] r;
        r = '0;
        for (int i = 0; i < GROUPS; i++) begin
            if (sell) begin
                if (v[GROUPS-1-i]) r = HI_BITS'(GROUPS - 1 - i);  // last write: lowest
            end else begin
                if (v[i]) r = HI_BITS'(i);                        // last write: highest
            end
        end
        return r;
    endfunction
    function automatic logic [LO_BITS-1:0] pe_level(input logic [GW-1:0] v, input logic sell);
        logic [LO_BITS-1:0] r;
        r = '0;
        for (int i = 0; i < GW; i++) begin
            if (sell) begin
                if (v[GW-1-i]) r = LO_BITS'(GW - 1 - i);
            end else begin
                if (v[i]) r = LO_BITS'(i);
            end
        end
        return r;
    endfunction

    // ---------------------------------------------------------------- level RAMs
    logic [WIN_BITS-1:0] rd_addr_q;             // the one read address (delta, or new best)
    logic [1:0]          lwe_q;                 // per side
    logic [WIN_BITS-1:0] lwaddr_q;
    logic [LEVEL_W-1:0]  lwdata_q;
    logic [LEVEL_W-1:0]  lrd [2];
    logic [LEVEL_W-1:0]  lrd_sel;
    for (genvar h = 0; h < 2; h++) begin : g_side
        sdp_ram #(.ADDR_W(WIN_BITS), .DATA_W(LEVEL_W), .OUT_REG(1'b1)) u_level (
            .clk_i(clk_i), .we_i(lwe_q[h]), .waddr_i(lwaddr_q), .wdata_i(lwdata_q),
            .raddr_i(rd_addr_q), .rdata_o(lrd[h])
        );
    end
    assign lrd_sel = lrd[sell_q];

    // ---------------------------------------------------------------- occupancy
    logic [GW-1:0]          l2 [2 * GROUPS];    // distributed RAM, address {side, group}
    logic                   l2_we;
    logic [HI_BITS:0]       l2_wa;
    logic [HI_BITS:0]       l2_ra_q;            // read address register: the delta's group,
                                                // then (S_FIND1) the group found
    logic [GW-1:0]          l2_wd, l2_rd;
    logic [1:0][GROUPS-1:0] l1_q;
    logic [GW-1:0]          l2w_q;              // the delta's group word, read in S_MOD
    logic [HI_BITS-1:0]     g_q;                // group found in S_FIND1
    logic [GW-1:0]          gw_q;               // its L2 word, read in S_FIND2

    always_ff @(posedge clk_i) if (l2_we) l2[l2_wa] <= l2_wd;
    assign l2_rd = l2[l2_ra_q];

    // ---------------------------------------------------------------- best, counters
    logic [1:0]                    bv_q;        // side has an in-window level
    logic [1:0][WIN_BITS-1:0]      best_q;
    logic [1:0][LEVEL_W-1:0]       bsz_q;
    logic [1:0][1:0][CNT_W-1:0]    cnt_q;       // [side][better] live out-of-window orders

    // Next BBO (S_BBO1) and the last one published (reset: empty, unflagged)
    logic [31:0]        nbpx_q, napx_q, lbpx_q, lapx_q;
    logic [LEVEL_W-1:0] nbsz_q, nasz_q, lbsz_q, lasz_q;
    logic               nflag_q, lflag_q;

    // ---------------------------------------------------------------- update datapath
    logic [LEVEL_W:0]   sum;                    // one spare bit for the overflow check
    logic [LEVEL_W-1:0] new_q;
    logic               new_nz;                 // = new_q != 0, registered with it in S_MOD
    logic [GW-1:0]      bit_mask, word_upd;
    logic               improves;

    always_comb begin
        sum = dec_q ? {1'b0, lrd_sel} - (LEVEL_W + 1)'(n_q)
                    : {1'b0, lrd_sel} + (LEVEL_W + 1)'(n_q);
        bit_mask = GW'(1) << idx_q[LO_BITS-1:0];
        word_upd = new_nz ? (l2w_q | bit_mask) : (l2w_q & ~bit_mask);
        improves = !bv_q[sell_q] || (sell_q ? idx_q < best_q[sell_q] : idx_q > best_q[sell_q])
                   || idx_q == best_q[sell_q];
        // occupancy RAM ports
        l2_we = 1'b0;
        l2_wa = {sell_q, idx_q[WIN_BITS-1 -: HI_BITS]};
        l2_wd = word_upd;
        if (state_q == S_CLEAR) begin
            l2_we = 1'b1;
            l2_wa = clear_q[HI_BITS:0];
            l2_wd = '0;
        end else if (state_q == S_BIT) begin
            l2_we = 1'b1;
        end
    end

    always_ff @(posedge clk_i) begin
        lwe_q     <= 2'b00;
        bbo_oow_o <= 1'b0;

        unique case (state_q)
            S_CLEAR: begin
                lwe_q    <= 2'b11;
                lwaddr_q <= clear_q;
                lwdata_q <= '0;
                clear_q  <= clear_q + 1'b1;
                if (&clear_q) state_q <= S_IDLE;
            end
            S_IDLE: begin
                if (s_valid_i) begin
                    sell_q <= s_sell_i; better_q <= s_better_i; dec_q <= s_dec_i;
                    last_q <= s_last_i; idx_q <= s_idx_i; n_q <= s_shares_i;
                    seq_q  <= s_seq_i;  ts_q <= s_ts_i;
                    rd_addr_q <= s_idx_i;
                    l2_ra_q   <= {s_sell_i, s_idx_i[WIN_BITS-1 -: HI_BITS]};
                    unique case (s_kind_i)
                        K_LEVEL: state_q <= S_RD;
                        K_OOW:   state_q <= S_OOW;
                        K_NONE:  state_q <= S_DONE;
                        default: state_q <= S_DONE;      // never (asserted)
                    endcase
                end
            end
            S_OOW: begin
                cnt_q[sell_q][better_q] <= dec_q ? cnt_q[sell_q][better_q] - 1'b1
                                                 : cnt_q[sell_q][better_q] + 1'b1;
                state_q <= S_DONE;
            end
            S_RD:    state_q <= S_RDREG;                 // level RAM read of idx_q
            S_RDREG: state_q <= S_MOD;                   // its output register
            S_MOD: begin
                new_q    <= sum[LEVEL_W-1:0];
                new_nz   <= sum[LEVEL_W-1:0] != '0;
                lwe_q[sell_q] <= 1'b1;
                lwaddr_q <= idx_q;
                lwdata_q <= sum[LEVEL_W-1:0];
                l2w_q    <= l2_rd;
                state_q  <= S_BIT;
            end
            S_BIT: begin                                 // L2 written (comb port)
                l1_q[sell_q][idx_q[WIN_BITS-1 -: HI_BITS]] <= |word_upd;
                state_q <= S_DONE;
                if (new_nz) begin
                    if (improves) begin
                        bv_q[sell_q]   <= 1'b1;
                        best_q[sell_q] <= idx_q;
                        bsz_q[sell_q]  <= new_q;
                    end
                end else if (bv_q[sell_q] && idx_q == best_q[sell_q]) begin
                    state_q <= S_FIND1;                  // the best emptied
                end
            end
            S_FIND1: begin
                if (l1_q[sell_q] == '0) begin
                    bv_q[sell_q]  <= 1'b0;               // side has no in-window level
                    bsz_q[sell_q] <= '0;
                    state_q <= S_DONE;
                end else begin
                    g_q     <= pe_group(l1_q[sell_q], sell_q);
                    l2_ra_q <= {sell_q, pe_group(l1_q[sell_q], sell_q)};
                    state_q <= S_FIND2;
                end
            end
            S_FIND2: begin                               // L2 read, registered
                gw_q    <= l2_rd;
                state_q <= S_FIND3;
            end
            S_FIND3: begin                               // priority encode the word
                best_q[sell_q] <= {g_q, pe_level(gw_q, sell_q)};
                rd_addr_q      <= {g_q, pe_level(gw_q, sell_q)};
                state_q        <= S_SZ;
            end
            S_SZ:    state_q <= S_SZREG;
            S_SZREG: state_q <= S_SZSET;
            S_SZSET: begin
                bsz_q[sell_q] <= lrd_sel;
                state_q <= S_DONE;
            end
            S_DONE: state_q <= last_q ? S_BBO1 : S_IDLE;
            S_BBO1: begin
                nbpx_q  <= bv_q[0] ? cfg_lo_price_i + 32'(best_q[0]) * 32'(TICK) : '0;
                napx_q  <= bv_q[1] ? cfg_lo_price_i + 32'(best_q[1]) * 32'(TICK) : '0;
                nbsz_q  <= bv_q[0] ? bsz_q[0] : '0;
                nasz_q  <= bv_q[1] ? bsz_q[1] : '0;
                nflag_q <= cnt_q[0][1] != '0 || (!bv_q[0] && cnt_q[0][0] != '0) ||
                           cnt_q[1][1] != '0 || (!bv_q[1] && cnt_q[1][0] != '0);
                state_q <= S_BBO2;
            end
            S_BBO2: begin
                if ({nbpx_q, nbsz_q, napx_q, nasz_q, nflag_q} !=
                    {lbpx_q, lbsz_q, lapx_q, lasz_q, lflag_q}) begin
                    lbpx_q <= nbpx_q; lbsz_q <= nbsz_q; lapx_q <= napx_q; lasz_q <= nasz_q;
                    lflag_q <= nflag_q;
                    m_bid_px_o <= nbpx_q; m_bid_sz_o <= nbsz_q;
                    m_ask_px_o <= napx_q; m_ask_sz_o <= nasz_q;
                    m_oow_o <= nflag_q; m_seq_o <= seq_q; m_ts_o <= ts_q;
                    bbo_oow_o <= nflag_q;
                    state_q <= S_EMIT;
                end else begin
                    state_q <= S_IDLE;
                end
            end
            S_EMIT: if (m_ready_i) state_q <= S_IDLE;
            default: state_q <= S_IDLE;
        endcase

        if (rst_i) begin
            state_q <= S_CLEAR;
            clear_q <= '0;
            l1_q    <= '0;
            bv_q    <= '0;
            cnt_q   <= '0;
            lbpx_q  <= '0; lbsz_q <= '0; lapx_q <= '0; lasz_q <= '0; lflag_q <= 1'b0;
        end
    end

    assign s_ready_o = state_q == S_IDLE;
    assign m_valid_o = state_q == S_EMIT;
    assign busy_o    = state_q != S_IDLE;

`ifndef SYNTHESIS
    always_ff @(posedge clk_i) begin
        if (!rst_i) begin
            // ADR 0006: a level never underflows and never reaches 2^LEVEL_W.
            if (state_q == S_MOD) assert (!sum[LEVEL_W]);
            // Out-of-window counters never wrap.
            if (state_q == S_OOW)
                assert (dec_q ? cnt_q[sell_q][better_q] != '0 : ~cnt_q[sell_q][better_q] != '0);
            // L1 and L2 agree: a group marked non-empty has a set bit.
            if (state_q == S_FIND3) assert (gw_q != '0);
            // Deltas carry only the three kinds.
            if (state_q == S_IDLE && s_valid_i) assert (s_kind_i != 2'd3);
        end
    end
`endif
endmodule
`default_nettype wire
