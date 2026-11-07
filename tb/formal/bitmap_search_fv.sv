`default_nettype none
// Formal harness for ADR 0006's best-price search (buildplan §8.3: "bitmap find-first-set
// always returns the best non-empty level"). Proves, for every input:
//   1. prio_enc alone: the index it returns is a set bit, and no better bit is set
//      (higher for bids, lowest_i = 0; lower for asks, lowest_i = 1).
//   2. The two-level search as book_levels composes it: encode L1 (one bit per group =
//      OR of the group's L2 word), then encode that group's L2 word. For any non-empty
//      bitmap of 2^(HI+LO) levels the result is exactly the best set level.
// The bitmap is unconstrained (anyconst), at the real geometry (64 x 64 = 4,096 levels).
// book_levels' bookkeeping (setting/clearing bits, L1 = OR of L2) is checked in
// simulation against the golden model; this proves the search given consistent maps.
module bitmap_search_fv;
    localparam int HI = 6, LO = 6;
    localparam int G = 2 ** HI, W = 2 ** LO, N = G * W;

    // ------------------------------------------------ 1. one encoder, any input
    (* anyconst *) logic [W-1:0] v;
    (* anyconst *) logic         v_low;
    logic [LO-1:0] v_idx;
    prio_enc #(.IDX_W(LO)) u_one (.v_i(v), .lowest_i(v_low), .idx_o(v_idx));

    logic [W-1:0] v_better;  // the bits better than v_idx
    always_comb begin
        v_better = v_low ? (W'(1) << v_idx) - W'(1)             // below the index
                         : ~((W'(1) << v_idx) | ((W'(1) << v_idx) - W'(1)));  // above it
        if (v != '0) begin
            assert (v[v_idx]);
            assert ((v & v_better) == '0);
        end
    end

    // ------------------------------------------------ 2. the two-level search
    (* anyconst *) logic [N-1:0] bm;
    (* anyconst *) logic         low;   // 1 = ask side
    logic [G-1:0]  l1;
    logic [HI-1:0] g;
    logic [LO-1:0] b;
    logic [HI+LO-1:0] best;
    logic [N-1:0]  better;

    always_comb for (int i = 0; i < G; i++) l1[i] = |bm[i*W +: W];
    prio_enc #(.IDX_W(HI)) u_group (.v_i(l1), .lowest_i(low), .idx_o(g));
    prio_enc #(.IDX_W(LO)) u_level (.v_i(bm[g*W +: W]), .lowest_i(low), .idx_o(b));
    assign best = {g, b};

    always_comb begin
        better = low ? (N'(1) << best) - N'(1)
                     : ~((N'(1) << best) | ((N'(1) << best) - N'(1)));
        if (bm != '0) begin
            assert (bm[best]);
            assert ((bm & better) == '0);
        end
        // Not vacuous: the extremes of both sides are reachable.
        cover (bm != '0 && !low && best == '1);
        cover (bm != '0 && low && best == '0);
        cover (bm != '0 && !low && best == '0);
    end
endmodule
`default_nettype wire
