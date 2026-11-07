`default_nettype none
// prio_enc: index of the highest (lowest_i = 0) or lowest (lowest_i = 1) set bit of v_i.
// The best-price search of ADR 0006: bids want the highest occupied level, asks the
// lowest. idx_o is 0 when v_i is 0 (callers only encode non-empty vectors).
// Combinational. Proven by tb/formal/bitmap_search.sby.
module prio_enc #(
    parameter int IDX_W = 6
) (
    input  wire logic [2**IDX_W-1:0] v_i,
    input  wire logic                lowest_i,
    output logic      [IDX_W-1:0]    idx_o
);
    localparam int N = 2 ** IDX_W;

    always_comb begin
        idx_o = '0;
        for (int i = 0; i < N; i++) begin
            if (lowest_i) begin
                if (v_i[N-1-i]) idx_o = IDX_W'(N - 1 - i);  // last write wins: the lowest
            end else begin
                if (v_i[i]) idx_o = IDX_W'(i);              // last write wins: the highest
            end
        end
    end
endmodule
`default_nettype wire
