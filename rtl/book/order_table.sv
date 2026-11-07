`default_nettype none
// order_table: decoded book records -> price-level deltas (ADR 0001, 0002, 0006).
// Golden model: model/bounded.py BoundedBook (placement, limits, counters, stale).
//
// Input:  one record per accepted book message (itch_decoder output), valid/ready.
//         s_type_i is the ITCH type: A/F add, E/C/X reduce, D delete, U replace.
// Output: level deltas, valid/ready. Every message produces one or more beats and its
//         last beat has m_last_o set (a U produces two: the removal, then the insert),
//         so the level stage evaluates the BBO exactly once per message. Beat kinds:
//           K_NONE   no level effect (a counted error, a sub-penny order, or the partial
//                    reduce of an out-of-window order)
//           K_LEVEL  add (m_dec_o = 0) or subtract (m_dec_o = 1) m_shares_o at level
//                    m_idx_o of side m_sell_o
//           K_OOW    one out-of-window order of side m_sell_o past edge m_better_o
//                    appears (m_dec_o = 0) or goes (m_dec_o = 1); ADR 0002's counters
//         m_seq_o / m_ts_o / m_user_o are the message's, on every beat. s_user_i is an
//         opaque per-message sideband (the feed's stale bit; T1 in Phase E).
//
// Table: 2 banks x 2^SET_BITS sets x WAYS ways (ADR 0001). Bank b looks at set
// prod_b[31 -: SET_BITS] and tag prod_b[TAG_W-1:0], prod_b = ref[31:0] * M_b mod 2^32.
// M_b is odd, so (set, tag) is a bijection of the ref within a bank: no aliasing. Insert
// goes to the bank with fewer valid ways, ties to bank 0, lowest free way.
// Entry (ENTRY_W = 54 at the defaults), MSB first: valid, sell, shares, nolevel, idx, tag.
// For a nolevel entry the idx field is reused: idx[WIN_BITS-1] = past the better edge,
// idx[WIN_BITS-2] = sub-penny inside the window (not out of window).
//
// Price -> level (ADR 0006): cfg_lo_price_i is the window's low edge in ITCH price units
// ((base - 2^(WIN_BITS-1)) * 100). off = price - lo; off >= 2^WIN_BITS * 100 or price < lo
// is out of window; else idx = off / 100 by reciprocal multiply (exact over the window,
// checked by assertion), and a non-zero remainder is a sub-penny price.
//
// Checks, in the model's order. Add (A/F, and U's insert): ref >= 2^32 -> ref_overflow;
// ref already live -> duplicate_ref; shares >= 2^SHARES_W -> shares_overflow; both
// buckets full -> table_overflow; then insert, counting price_oow or price_subpenny.
// E/C/X/D, and U's removal: ref >= 2^32 or not found -> unknown_ref (a U then stops);
// reduce by >= the order's shares removes it (over_reduce if more). U's insert takes the
// original order's side.
//
// Status pulses (one cycle): the eight counters above. stale_o: sticky level, set by every
// pulse except price_oow (ADR 0006).
//
// Latency (cycle 0 = the record's handshake cycle): the first delta beat is valid in cycle
// 7 (hash, address, BRAM read, BRAM output register, match, act, beat); a U's second beat
// 7 cycles after its first is taken; s_ready_o is high again the cycle after the last beat
// is taken. Measured by test latency_matches_header. One message at a time: s_ready_o is
// high only in the idle state. After reset the table is cleared, one set per cycle
// (2^SET_BITS cycles), with s_ready_o low.
// Reset: synchronous, active-high.
module order_table #(
    parameter int SET_BITS = 13,
    parameter int WAYS     = 4,
    parameter int SHARES_W = 20,
    parameter int WIN_BITS = 12,
    parameter int USER_W   = 1
) (
    input  wire logic                clk_i,
    input  wire logic                rst_i,
    // configuration
    input  wire logic [31:0]         cfg_lo_price_i,
    // records in
    input  wire logic                s_valid_i,
    output logic                     s_ready_o,
    input  wire logic [7:0]          s_type_i,
    input  wire logic [63:0]         s_seq_i,
    input  wire logic [47:0]         s_ts_i,
    input  wire logic [USER_W-1:0]   s_user_i,
    input  wire logic [63:0]         s_ref_i,
    input  wire logic [63:0]         s_new_ref_i,
    input  wire logic                s_sell_i,
    input  wire logic [31:0]         s_shares_i,
    input  wire logic [31:0]         s_price_i,
    // level deltas out
    output logic                     m_valid_o,
    input  wire logic                m_ready_i,
    output logic [1:0]               m_kind_o,
    output logic                     m_sell_o,
    output logic [WIN_BITS-1:0]      m_idx_o,
    output logic                     m_better_o,
    output logic                     m_dec_o,
    output logic [SHARES_W-1:0]      m_shares_o,
    output logic                     m_last_o,
    output logic [63:0]              m_seq_o,
    output logic [47:0]              m_ts_o,
    output logic [USER_W-1:0]        m_user_o,
    // status
    output logic                     ref_overflow_o,
    output logic                     duplicate_ref_o,
    output logic                     shares_overflow_o,
    output logic                     table_overflow_o,
    output logic                     unknown_ref_o,
    output logic                     over_reduce_o,
    output logic                     price_oow_o,
    output logic                     price_subpenny_o,
    output logic                     stale_o,
    output logic                     busy_o
);
    // ITCH 5.0 message types (docs/buildplan.md §5.3)
    localparam logic [7:0] T_A = 8'h41, T_F = 8'h46, T_E = 8'h45, T_C = 8'h43, T_X = 8'h58,
                           T_D = 8'h44, T_U = 8'h55;
    localparam logic [1:0] K_NONE = 2'd0, K_LEVEL = 2'd1, K_OOW = 2'd2;
    localparam int REF_W   = 32;                    // ADR 0001: hash and tag cover 32 bits
    localparam int TAG_W   = REF_W - SET_BITS;
    localparam int ENTRY_W = 1 + 1 + SHARES_W + 1 + WIN_BITS + TAG_W;
    localparam int SET_W   = WAYS * ENTRY_W;
    localparam int FILL_W  = $clog2(WAYS + 1);
    localparam int WAY_W   = (WAYS > 1) ? $clog2(WAYS) : 1;
    localparam logic [31:0] M0 = 32'h9E37_79B1;     // ADR 0001 multipliers (odd)
    localparam logic [31:0] M1 = 32'h85EB_CA6B;
    // Price window in ITCH price units (4 implied decimals; 1 tick = 1 cent = 100 units)
    localparam int TICK    = 100;
    localparam int WIN     = 2 ** WIN_BITS;
    localparam int OFF_W   = $clog2(WIN * TICK);    // in-window offsets
    // idx = (off * RECIP) >> RSHIFT is floor(off / TICK) for every in-window off when
    // 2^RSHIFT > TICK * WIN * TICK (the reciprocal's error stays under 1/TICK).
    localparam int RSHIFT  = $clog2(TICK * WIN * TICK) + 1;
    localparam longint RECIP = (64'd1 << RSHIFT) / 64'(TICK) + 64'd1;
    localparam int RECIP_W = RSHIFT - $clog2(TICK) + 2;
    localparam int PROD_W  = OFF_W + RECIP_W;

    typedef struct packed {
        logic                valid;
        logic                sell;
        logic [SHARES_W-1:0] shares;
        logic                nolevel;
        logic [WIN_BITS-1:0] idx;
        logic [TAG_W-1:0]    tag;
    } entry_t;

    typedef enum logic [3:0] {
        S_CLEAR, S_IDLE, S_HASH, S_ADDR, S_READ, S_RDREG, S_EVAL, S_ACT, S_EMIT
    } state_t;
    typedef enum logic [1:0] { OP_ADD, OP_REDUCE, OP_DELETE, OP_REPLACE } op_t;

    state_t state_q;
    op_t    op_q;
    logic   ins_q;                 // this lookup is an insert (A/F, or U's second half)
    logic [SET_BITS-1:0] clear_q;

    // Message registers (datapath, not reset)
    logic [63:0] new_ref_q, seq_q;
    logic [47:0] ts_q;
    logic [USER_W-1:0] user_q;
    logic        sell_q;
    logic [31:0] shares_q, price_q;

    // ---------------------------------------------------------------- price class
    // One stage per lookup state, while the table read is in flight: S_HASH offset and
    // window compare; S_ADDR and S_READ the reciprocal multiply (two register stages, so
    // it can use the DSP's pipeline registers); S_RDREG the quotient; S_EVAL remainder and
    // class. Used in S_ACT.
    logic [32:0]         off_full;
    logic [OFF_W-1:0]    off_q;
    logic                below_q, above_q;
    // Only qprod[RSHIFT +: WIN_BITS] is the quotient: the low bits are the fraction and the
    // top bits are always 0 for in-window offsets.
    /* verilator lint_off UNUSEDSIGNAL */
    logic [PROD_W-1:0]   qprod_q, qprod2_q;
    /* verilator lint_on UNUSEDSIGNAL */
    logic [WIN_BITS-1:0] pidx_q;
    logic [OFF_W-1:0]    prem;
    logic                p_oow_q, p_better_q, p_sub_q;
    logic [WIN_BITS-1:0] p_idx_q;

    always_comb begin
        off_full = {1'b0, price_q} - {1'b0, cfg_lo_price_i};
        prem     = off_q - OFF_W'(pidx_q) * OFF_W'(TICK);
    end

    // ---------------------------------------------------------------- hash and banks
    logic [63:0] key_q;            // ref being looked up: the record's ref, then (U insert)
                                   // its new_ref; a register, so the hash starts from FFs
    logic [31:0] prod0_q, prod1_q; // S_HASH: the hash products
    logic        key_wide_q;       // key >= 2^32: never in the table

    // S_ADDR: set and tag registered from the products, so each bank's read address
    // comes straight from flip-flops (it fans out to every BRAM of the bank; replicable).
    (* max_fanout = 16 *) logic [SET_BITS-1:0] set0_q, set1_q;
    logic [TAG_W-1:0]    tag0_q, tag1_q;

    (* max_fanout = 16 *) logic                we0, we1;
    (* max_fanout = 16 *) logic [SET_BITS-1:0] waddr0, waddr1;
    logic [SET_W-1:0]    wdata0, wdata1, rdata0, rdata1;

    // Each bank is two half-depth RAMs selected by the set's top bit: 2^(SET_BITS-1) x
    // SET_W maps to 4K x 9 BRAM tiles (24 per half at the defaults, 48 per bank), where
    // one 8K-deep RAM was mapped as 8K x 4 tiles (54 per bank). ADR 0001's budget is 96.
    localparam int HALF_W = SET_BITS - 1;
    logic [SET_W-1:0] rd0 [2], rd1 [2];
    for (genvar h = 0; h < 2; h++) begin : g_half
        sdp_ram #(.ADDR_W(HALF_W), .DATA_W(SET_W), .OUT_REG(1'b1)) u_bank0 (
            .clk_i(clk_i), .we_i(we0 && waddr0[HALF_W] == 1'(h)),
            .waddr_i(waddr0[HALF_W-1:0]), .wdata_i(wdata0),
            .raddr_i(set0_q[HALF_W-1:0]), .rdata_o(rd0[h])
        );
        sdp_ram #(.ADDR_W(HALF_W), .DATA_W(SET_W), .OUT_REG(1'b1)) u_bank1 (
            .clk_i(clk_i), .we_i(we1 && waddr1[HALF_W] == 1'(h)),
            .waddr_i(waddr1[HALF_W-1:0]), .wdata_i(wdata1),
            .raddr_i(set1_q[HALF_W-1:0]), .rdata_o(rd1[h])
        );
    end
    assign rdata0 = rd0[set0_q[HALF_W]];  // set*_q is held from S_READ through S_ACT
    assign rdata1 = rd1[set1_q[HALF_W]];

    // ---------------------------------------------------------------- match (S_EVAL)
    entry_t [WAYS-1:0] row0, row1;
    assign row0 = rdata0;
    assign row1 = rdata1;

    logic              hit0, hit1;
    logic [WAY_W-1:0]  hway0, hway1, fway0, fway1;
    logic [FILL_W-1:0] fill0, fill1;
    // Reduce arithmetic for every way, in parallel with the tag match, so S_ACT only selects
    // the matched way's result: ge = the reduce removes the order, gt = over-reduce.
    logic [WAYS-1:0]                ge0, ge1, gt0, gt1;
    logic [WAYS-1:0][SHARES_W-1:0]  left0, left1;

    always_comb begin
        hit0 = 1'b0; hit1 = 1'b0;
        hway0 = '0;  hway1 = '0;
        fway0 = '0;  fway1 = '0;
        fill0 = '0;  fill1 = '0;
        for (int w = WAYS - 1; w >= 0; w--) begin  // descending: lowest index wins
            if (row0[w].valid && row0[w].tag == tag0_q) begin hit0 = 1'b1; hway0 = WAY_W'(w); end
            if (row1[w].valid && row1[w].tag == tag1_q) begin hit1 = 1'b1; hway1 = WAY_W'(w); end
            if (!row0[w].valid) fway0 = WAY_W'(w);
            if (!row1[w].valid) fway1 = WAY_W'(w);
        end
        for (int w = 0; w < WAYS; w++) begin
            fill0 = fill0 + FILL_W'(row0[w].valid);
            fill1 = fill1 + FILL_W'(row1[w].valid);
            ge0[w] = shares_q >= 32'(row0[w].shares);
            ge1[w] = shares_q >= 32'(row1[w].shares);
            gt0[w] = shares_q > 32'(row0[w].shares);
            gt1[w] = shares_q > 32'(row1[w].shares);
            left0[w] = row0[w].shares - shares_q[SHARES_W-1:0];
            left1[w] = row1[w].shares - shares_q[SHARES_W-1:0];
        end
    end

    // Registered match result (S_EVAL -> S_ACT)
    entry_t [WAYS-1:0] row0_q, row1_q;
    logic              hit_q, hbank_q, full_q, ibank_q;
    logic [WAY_W-1:0]  hway_q, iway_q;
    logic [WAYS-1:0]               ge0_q, ge1_q, gt0_q, gt1_q;
    logic [WAYS-1:0][SHARES_W-1:0] left0_q, left1_q;

    // ---------------------------------------------------------------- act (S_ACT)
    entry_t              found;    // the matched entry
    entry_t              ins;      // the entry an insert writes
    entry_t              upd;      // the matched entry after a reduce or removal
    entry_t [WAYS-1:0]   wrow0, wrow1;  // the set rows S_ACT writes back
    logic                removes;  // the matched entry is removed
    always_comb begin
        found = hbank_q ? row1_q[hway_q] : row0_q[hway_q];
        removes = op_q == OP_DELETE || op_q == OP_REPLACE ||
                  (op_q == OP_REDUCE && (hbank_q ? ge1_q[hway_q] : ge0_q[hway_q]));
        ins.valid   = 1'b1;
        ins.sell    = sell_q;
        ins.shares  = shares_q[SHARES_W-1:0];
        ins.nolevel = p_oow_q || p_sub_q;
        ins.idx     = p_oow_q ? {p_better_q, {(WIN_BITS-1){1'b0}}}
                    : p_sub_q ? {1'b0, 1'b1, {(WIN_BITS-2){1'b0}}} : p_idx_q;
        ins.tag     = ibank_q ? tag1_q : tag0_q;
        upd = found;
        if (removes) upd.valid = 1'b0;
        else upd.shares = hbank_q ? left1_q[hway_q] : left0_q[hway_q];
        wrow0 = row0_q;
        wrow1 = row1_q;
        if (ins_q) begin
            wrow0[iway_q] = ins;
            wrow1[iway_q] = ins;
        end else begin
            wrow0[hway_q] = upd;
            wrow1[hway_q] = upd;
        end
    end

    // Delta kind when an entry is removed: sub-penny entries have no level and no edge.
    function automatic logic [1:0] gone_kind(input logic nolevel, input logic sub);
        if (!nolevel) return K_LEVEL;
        return sub ? K_NONE : K_OOW;
    endfunction

    // Status pulse registers
    logic ref_ovf_q, dup_q, shares_ovf_q, table_ovf_q, unknown_q, over_q, poow_q, psub_q;

    always_ff @(posedge clk_i) begin
        // pulses default low
        ref_ovf_q <= 1'b0; dup_q <= 1'b0; shares_ovf_q <= 1'b0; table_ovf_q <= 1'b0;
        unknown_q <= 1'b0; over_q <= 1'b0; poow_q <= 1'b0; psub_q <= 1'b0;
        we0 <= 1'b0; we1 <= 1'b0;

        unique case (state_q)
            S_CLEAR: begin
                we0 <= 1'b1; we1 <= 1'b1;
                waddr0 <= clear_q; waddr1 <= clear_q;
                wdata0 <= '0; wdata1 <= '0;
                clear_q <= clear_q + 1'b1;
                if (&clear_q) state_q <= S_IDLE;
            end
            S_IDLE: begin
                if (s_valid_i) begin
                    key_q <= s_ref_i; new_ref_q <= s_new_ref_i; seq_q <= s_seq_i;
                    ts_q <= s_ts_i; user_q <= s_user_i; sell_q <= s_sell_i; shares_q <= s_shares_i;
                    price_q <= s_price_i;
                    unique case (s_type_i)
                        T_A, T_F:      begin op_q <= OP_ADD;     ins_q <= 1'b1; end
                        T_E, T_C, T_X: begin op_q <= OP_REDUCE;  ins_q <= 1'b0; end
                        T_D:           begin op_q <= OP_DELETE;  ins_q <= 1'b0; end
                        default:       begin op_q <= OP_REPLACE; ins_q <= 1'b0; end  // T_U
                    endcase
                    state_q <= S_HASH;
                end
            end
            S_HASH: begin
                prod0_q    <= key_q[31:0] * M0;
                prod1_q    <= key_q[31:0] * M1;
                key_wide_q <= |key_q[63:32];
                below_q    <= off_full[32];                               // price < lo
                above_q    <= !off_full[32] && off_full[31:0] >= 32'(WIN * TICK);
                off_q      <= off_full[OFF_W-1:0];
                state_q    <= S_ADDR;
            end
            S_ADDR: begin
                set0_q  <= prod0_q[31 -: SET_BITS];
                set1_q  <= prod1_q[31 -: SET_BITS];
                tag0_q  <= prod0_q[TAG_W-1:0];
                tag1_q  <= prod1_q[TAG_W-1:0];
                qprod_q <= PROD_W'(off_q) * PROD_W'(RECIP);
                state_q <= S_READ;
            end
            S_READ: begin                       // BRAM read of set0_q / set1_q
                qprod2_q <= qprod_q;
                state_q  <= S_RDREG;
            end
            S_RDREG: begin                      // BRAM output register
                pidx_q  <= qprod2_q[RSHIFT +: WIN_BITS];
                state_q <= S_EVAL;
            end
            S_EVAL: begin
                p_oow_q    <= below_q || above_q;
                p_better_q <= above_q == !sell_q;  // bids: above is better; asks: below is
                p_sub_q    <= !below_q && !above_q && prem != '0;
                p_idx_q    <= pidx_q;
                row0_q  <= row0; row1_q <= row1;
                ge0_q   <= ge0;  ge1_q  <= ge1;
                gt0_q   <= gt0;  gt1_q  <= gt1;
                left0_q <= left0; left1_q <= left1;
                hit_q   <= !key_wide_q && (hit0 || hit1);
                hbank_q <= !hit0;
                hway_q  <= hit0 ? hway0 : hway1;
                full_q  <= fill0 == FILL_W'(WAYS) && fill1 == FILL_W'(WAYS);
                ibank_q <= fill1 < fill0;       // ties go to bank 0
                iway_q  <= fill1 < fill0 ? fway1 : fway0;
                state_q <= S_ACT;
            end
            S_ACT: begin
                m_kind_o   <= K_NONE;
                m_dec_o    <= !ins_q;
                m_last_o   <= 1'b1;
                m_seq_o    <= seq_q;
                m_ts_o     <= ts_q;
                m_user_o   <= user_q;
                state_q    <= S_EMIT;
                if (ins_q) begin
                    m_sell_o   <= sell_q;
                    m_idx_o    <= p_idx_q;
                    m_better_o <= p_better_q;
                    m_shares_o <= shares_q[SHARES_W-1:0];
                    if (key_wide_q)                         ref_ovf_q    <= 1'b1;
                    else if (hit_q)                         dup_q        <= 1'b1;
                    else if (|shares_q[31:SHARES_W])        shares_ovf_q <= 1'b1;
                    else if (full_q)                        table_ovf_q  <= 1'b1;
                    else begin
                        if (ibank_q) begin
                            we1 <= 1'b1; waddr1 <= set1_q; wdata1 <= wrow1;
                        end else begin
                            we0 <= 1'b1; waddr0 <= set0_q; wdata0 <= wrow0;
                        end
                        poow_q <= p_oow_q;
                        psub_q <= p_sub_q;
                        m_kind_o <= p_oow_q ? K_OOW : p_sub_q ? K_NONE : K_LEVEL;
                    end
                end else if (!hit_q) begin
                    unknown_q <= 1'b1;                  // a U stops here: nothing to insert
                end else begin
                    if (hbank_q) begin
                        we1 <= 1'b1; waddr1 <= set1_q; wdata1 <= wrow1;
                    end else begin
                        we0 <= 1'b1; waddr0 <= set0_q; wdata0 <= wrow0;
                    end
                    over_q     <= op_q == OP_REDUCE && (hbank_q ? gt1_q[hway_q] : gt0_q[hway_q]);
                    m_sell_o   <= found.sell;
                    m_idx_o    <= found.idx;
                    m_better_o <= found.idx[WIN_BITS-1];
                    m_shares_o <= removes ? found.shares : shares_q[SHARES_W-1:0];
                    m_kind_o   <= removes ? gone_kind(found.nolevel, found.idx[WIN_BITS-2])
                                : found.nolevel ? K_NONE : K_LEVEL;
                    if (op_q == OP_REPLACE) begin
                        m_last_o <= 1'b0;               // the insert follows
                        sell_q   <= found.sell;         // U keeps the original side
                    end
                end
            end
            S_EMIT: begin
                if (m_ready_i) begin
                    if (!m_last_o) begin
                        ins_q   <= 1'b1;                // U: now insert new_ref
                        key_q   <= new_ref_q;
                        state_q <= S_HASH;
                    end else begin
                        state_q <= S_IDLE;
                    end
                end
            end
            default: state_q <= S_IDLE;
        endcase

        if (rst_i) begin
            state_q <= S_CLEAR;
            clear_q <= '0;
        end
    end

    // Sticky stale (ADR 0006): every silent-divergence counter, not price_oow.
    always_ff @(posedge clk_i) begin
        if (ref_ovf_q || dup_q || shares_ovf_q || table_ovf_q || unknown_q || over_q || psub_q)
            stale_o <= 1'b1;
        if (rst_i) stale_o <= 1'b0;
    end

    assign s_ready_o         = state_q == S_IDLE;
    assign m_valid_o         = state_q == S_EMIT;
    assign busy_o            = state_q != S_IDLE;
    assign ref_overflow_o    = ref_ovf_q;
    assign duplicate_ref_o   = dup_q;
    assign shares_overflow_o = shares_ovf_q;
    assign table_overflow_o  = table_ovf_q;
    assign unknown_ref_o     = unknown_q;
    assign over_reduce_o     = over_q;
    assign price_oow_o       = poow_q;
    assign price_subpenny_o  = psub_q;

`ifndef SYNTHESIS
    always_ff @(posedge clk_i) begin
        if (!rst_i) begin
            // A ref lives in at most one bank (inserts check both first).
            if (state_q == S_EVAL) assert (!(hit0 && hit1));
            // The reciprocal divide is exact over the window.
            if (state_q == S_EVAL && !below_q && !above_q) assert (prem < OFF_W'(TICK));
            // Records arrive only with a book type.
            if (state_q == S_IDLE && s_valid_i)
                assert (s_type_i inside {T_A, T_F, T_E, T_C, T_X, T_D, T_U});
            // A partial reduce never touches more shares than the order has.
            if (state_q == S_ACT && !ins_q && hit_q && !removes)
                assert (shares_q < 32'(found.shares));
        end
    end
`endif
endmodule
`default_nettype wire
