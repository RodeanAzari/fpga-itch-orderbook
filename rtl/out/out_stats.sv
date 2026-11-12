`default_nettype none
// out_stats: the 45 u64 stats counters of docs/protocol.md (type 0x03) and the stats tick.
//
// Ports are named after the counters (docs/protocol.md table, model.protocol.STATS_NAMES).
// Each *_i pulse adds 1 to its counter, except the amount inputs: mold_gap_messages_i is
// added when mold_gaps_i pulses; rx_fifo_overflow_i (frames lost, eth_rx ovf_frames_o
// gated by ovf_o), itch_malformed_messages_i and out_records_i are counts this cycle.
// locate and stale are values, not counts: locate = {known, locate[15:0]},
// stale = {book, feed}; inj_cycles, inj_bbos, inj_digest (ADR 0014) are inj_meter's
// outputs (it is reset by the same clear). uptime_cycles counts cycles since the last clear.
// clear_i (a config) zeroes every counter except ctrl_ok and ctrl_bad, restarts the tick,
// and an event in the same cycle is dropped (the pipeline is in reset then anyway).
// Counters wrap at 2^64 (never in practice: 4,600 years of cycles at 125 MHz).
// stats_o: counter k at stats_o[(N_STATS-k)*64-1 -: 64] (out_builder's layout), registered:
//   it includes events up to the previous cycle.
// tick_o: a one-cycle pulse every TICK_CYCLES cycles after a clear (1 s of core clock).
// Reset: synchronous, active-high; zeroes every counter, ctrl counters included.
// TICK_CYCLES >= 2.
module out_stats #(
    parameter int TICK_CYCLES = 125_000_000,
    localparam int N_STATS    = 45
) (
    input  wire logic                  clk_i,
    input  wire logic                  rst_i,
    input  wire logic                  clear_i,
    input  wire logic                  rx_frames_ok_i,
    input  wire logic                  rx_crc_err_i,
    input  wire logic                  rx_err_i,
    input  wire logic                  rx_runt_i,
    input  wire logic                  rx_oversize_i,
    input  wire logic [7:0]            rx_fifo_overflow_i,
    input  wire logic                  feed_datagrams_i,
    input  wire logic                  feed_filtered_i,
    input  wire logic                  ip_csum_err_i,
    input  wire logic                  udp_malformed_i,
    input  wire logic                  mold_packets_i,
    input  wire logic                  mold_heartbeats_i,
    input  wire logic                  mold_end_of_session_i,
    input  wire logic                  mold_gaps_i,
    input  wire logic [63:0]           mold_gap_messages_i,
    input  wire logic                  mold_duplicates_i,
    input  wire logic                  mold_session_changes_i,
    input  wire logic                  mold_malformed_packets_i,
    input  wire logic                  itch_unknown_types_i,
    input  wire logic [1:0]            itch_malformed_messages_i,
    input  wire logic                  itch_other_locate_i,
    input  wire logic                  itch_directory_matches_i,
    input  wire logic                  itch_locate_changes_i,
    input  wire logic                  locate_valid_i,
    input  wire logic [15:0]           locate_i,
    input  wire logic                  book_records_i,
    input  wire logic                  ref_overflow_i,
    input  wire logic                  duplicate_ref_i,
    input  wire logic                  shares_overflow_i,
    input  wire logic                  table_overflow_i,
    input  wire logic                  unknown_ref_i,
    input  wire logic                  over_reduce_i,
    input  wire logic                  price_oow_i,
    input  wire logic                  price_subpenny_i,
    input  wire logic                  bbo_oow_i,
    input  wire logic                  feed_stale_i,
    input  wire logic                  book_stale_i,
    input  wire logic [5:0]            out_records_i,
    input  wire logic                  out_datagrams_i,
    input  wire logic                  ctrl_ok_i,
    input  wire logic                  ctrl_bad_i,
    input  wire logic                  t0_lost_i,
    input  wire logic                  cut_through_bad_i,  // ADR 0013
    input  wire logic [63:0]           inj_cycles_i,       // ADR 0014, values
    input  wire logic [63:0]           inj_bbos_i,
    input  wire logic [63:0]           inj_digest_i,
    output logic [N_STATS*64-1:0]      stats_o,
    output logic                       tick_o
);
    // docs/protocol.md stats table: indices of the non-plain counters
    localparam int K_LOCATE = 24;
    localparam int K_STALE  = 35;
    localparam int K_CTRL0  = 38;  // ctrl_ok, ctrl_bad: not cleared by a config
    localparam int K_INJ    = 42;  // inj_cycles, inj_bbos, inj_digest
    localparam int TICK_W   = $clog2(TICK_CYCLES);

    logic [63:0] add [N_STATS];
    logic [63:0] cnt_q [N_STATS];
    logic [TICK_W-1:0] tick_q;

    always_comb begin
        add[0]  = 64'd1;  // uptime_cycles
        add[1]  = 64'(rx_frames_ok_i);
        add[2]  = 64'(rx_crc_err_i);
        add[3]  = 64'(rx_err_i);
        add[4]  = 64'(rx_runt_i);
        add[5]  = 64'(rx_oversize_i);
        add[6]  = 64'(rx_fifo_overflow_i);
        add[7]  = 64'(feed_datagrams_i);
        add[8]  = 64'(feed_filtered_i);
        add[9]  = 64'(ip_csum_err_i);
        add[10] = 64'(udp_malformed_i);
        add[11] = 64'(mold_packets_i);
        add[12] = 64'(mold_heartbeats_i);
        add[13] = 64'(mold_end_of_session_i);
        add[14] = 64'(mold_gaps_i);
        add[15] = mold_gaps_i ? mold_gap_messages_i : 64'd0;
        add[16] = 64'(mold_duplicates_i);
        add[17] = 64'(mold_session_changes_i);
        add[18] = 64'(mold_malformed_packets_i);
        add[19] = 64'(itch_unknown_types_i);
        add[20] = 64'(itch_malformed_messages_i);
        add[21] = 64'(itch_other_locate_i);
        add[22] = 64'(itch_directory_matches_i);
        add[23] = 64'(itch_locate_changes_i);
        add[24] = '0;  // locate: a value
        add[25] = 64'(book_records_i);
        add[26] = 64'(ref_overflow_i);
        add[27] = 64'(duplicate_ref_i);
        add[28] = 64'(shares_overflow_i);
        add[29] = 64'(table_overflow_i);
        add[30] = 64'(unknown_ref_i);
        add[31] = 64'(over_reduce_i);
        add[32] = 64'(price_oow_i);
        add[33] = 64'(price_subpenny_i);
        add[34] = 64'(bbo_oow_i);
        add[35] = '0;  // stale: a value
        add[36] = 64'(out_records_i);
        add[37] = 64'(out_datagrams_i);
        add[38] = 64'(ctrl_ok_i);
        add[39] = 64'(ctrl_bad_i);
        add[40] = 64'(t0_lost_i);
        add[41] = 64'(cut_through_bad_i);
        add[42] = '0;  // inj_*: values
        add[43] = '0;
        add[44] = '0;
    end

    always_ff @(posedge clk_i) begin
        for (int k = 0; k < N_STATS; k++) begin
            if (rst_i || (clear_i && (k < K_CTRL0 || k > K_CTRL0 + 1))) cnt_q[k] <= '0;
            else cnt_q[k] <= cnt_q[k] + add[k];
        end
        // values, registered like the counters
        cnt_q[K_LOCATE] <= 64'({locate_valid_i, locate_i});
        cnt_q[K_STALE]  <= 64'({book_stale_i, feed_stale_i});
        cnt_q[K_INJ]    <= inj_cycles_i;
        cnt_q[K_INJ+1]  <= inj_bbos_i;
        cnt_q[K_INJ+2]  <= inj_digest_i;
    end

    always_ff @(posedge clk_i) begin
        tick_o <= 1'b0;
        if (rst_i || clear_i) begin
            tick_q <= '0;
        end else if (tick_q == TICK_W'(TICK_CYCLES - 1)) begin
            tick_q <= '0;
            tick_o <= 1'b1;
        end else begin
            tick_q <= tick_q + 1'b1;
        end
    end

    for (genvar k = 0; k < N_STATS; k++) begin : g_out
        assign stats_o[(N_STATS-k)*64-1 -: 64] = cnt_q[k];
    end
endmodule
`default_nettype wire
