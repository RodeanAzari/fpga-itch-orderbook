`default_nettype none
// t0_track: matches each frame's T0 (the counter at its synchronized SFD) to the frame as
// it leaves eth_rx (ADR 0011).
//
//   sfd_i --push now_i--> PEND queue --eth_rx status--> DONE queue --frame_start_i--> t0_o
//
// eth_rx gives exactly one status per frame that had an SFD, in wire order: ok_i, drop_i
// (crc_err, rx_err, runt or oversize), or ovf_i covering ovf_frames_i frames. A status
// takes the oldest entries of PEND: ok moves one to DONE, drop and ovf discard. A good
// frame leaves eth_rx in the same order, so frame_start_i (its first beat accepted)
// takes DONE's oldest entry into t0_o, held until the next frame starts.
//
// Losses are counted, never misaligned silently (lost_o pulses, one per event):
//   - a status for more frames than PEND holds (an SFD missed, e.g. during reset):
//     PEND empties, and an ok pushes an invalid entry so DONE stays in step;
//   - an SFD while PEND is full: the newest is dropped, so its frame finds PEND empty;
//   - a frame start with DONE empty, or a push while DONE is full.
// A frame whose T0 was lost gets t0_valid_o = 0 (t0_o = 0).
// Depths: PEND covers eth_rx's async FIFO (2 KB / 64-byte minimum frames = 32 frames);
// DONE covers its frame buffer (4 KB / 60-byte minimum stored frames = 68 frames). Status
// pulses come at most one per cycle and frame_start_i at most one per frame.
// Cut-through (CUT_THROUGH = 1, ADR 0013): a frame leaves eth_rx before its status, so
// frame_start_i takes the frame's T0 from PEND directly (DONE is not built). emitted_i
// (eth_rx emitted_o, valid with a status) says the frame put a beat on eth_rx's output;
// its status then discards only the other frames it covers. A frame judged before its
// start (a 5-byte frame whose one beat waits on backpressure) parks its T0 in a one-entry
// hold that its start takes. A start and a status on the same cycle are the same frame.
// Golden model: model/timestamps.py T0Track(cut_through=True).
// Latency: t0_o is registered, valid the cycle after frame_start_i. Memory: two small
// arrays, read asynchronously at the head (LUT RAM). Reset: synchronous, active-high.
module t0_track #(
    parameter bit CUT_THROUGH = 1'b0,
    parameter int PEND_AW = 6,  // 64 entries
    parameter int DONE_AW = 7   // 128 entries
) (
    input  wire logic        clk_i,
    input  wire logic        rst_i,
    input  wire logic [63:0] now_i,
    input  wire logic        sfd_i,          // synchronized SFD (cdc_pulse)
    input  wire logic        ok_i,
    input  wire logic        drop_i,
    input  wire logic        ovf_i,
    input  wire logic [7:0]  ovf_frames_i,   // valid with ovf_i
    input  wire logic        emitted_i,      // valid with a status; read in cut-through only
    input  wire logic        frame_start_i,
    output logic [63:0]      t0_o,
    output logic             t0_valid_o,
    output logic             lost_o
);
    localparam int PEND_N = 1 << PEND_AW;
    localparam int DONE_N = 1 << DONE_AW;

    logic [63:0]       pend_mem [PEND_N];
    logic [PEND_AW:0]  pend_wr_q, pend_rd_q, pend_count;
    logic              pend_full, status;

    always_comb begin
        pend_count = pend_wr_q - pend_rd_q;
        pend_full  = pend_count == (PEND_AW + 1)'(PEND_N);
        status     = ok_i || drop_i || ovf_i;
    end

    always_ff @(posedge clk_i) begin
        if (sfd_i && !pend_full) pend_mem[pend_wr_q[PEND_AW-1:0]] <= now_i;
    end

    if (!CUT_THROUGH) begin : g_store_forward
        logic [64:0]       done_mem [DONE_N];  // {valid, t0}
        logic [DONE_AW:0]  done_wr_q, done_rd_q;
        logic [PEND_AW:0]  take;
        logic [DONE_AW:0]  done_count;
        logic              short, done_full, done_empty, push_done;
        logic [64:0]       done_in;
        logic              unused_ct;

        always_comb begin
            unused_ct  = emitted_i;
            done_count = done_wr_q - done_rd_q;
            done_full  = done_count == (DONE_AW + 1)'(DONE_N);
            done_empty = done_count == '0;
            // frames this status covers, clipped to what PEND holds
            short      = ovf_i ? 9'(ovf_frames_i) > 9'(pend_count) : pend_count == '0;
            take       = !status ? '0
                       : ovf_i   ? (short ? pend_count : (PEND_AW + 1)'(ovf_frames_i))
                       : (short ? '0 : (PEND_AW + 1)'(1));
            push_done  = ok_i;
            done_in    = short ? 65'd0 : {1'b1, pend_mem[pend_rd_q[PEND_AW-1:0]]};
        end

        always_ff @(posedge clk_i) begin
            if (push_done && !done_full) done_mem[done_wr_q[DONE_AW-1:0]] <= done_in;
        end

        always_ff @(posedge clk_i) begin
            if (rst_i) begin
                pend_wr_q  <= '0;
                pend_rd_q  <= '0;
                done_wr_q  <= '0;
                done_rd_q  <= '0;
                t0_o       <= '0;
                t0_valid_o <= 1'b0;
                lost_o     <= 1'b0;
            end else begin
                // A same-cycle SFD may fill the slot a status frees; it is checked against
                // the count before this cycle's take, so it may be dropped one cycle early
                // (counted). PEND full needs 64 frames in eth_rx's FIFO, which cannot hold
                // them.
                if (sfd_i && !pend_full) pend_wr_q <= pend_wr_q + 1'b1;
                pend_rd_q <= pend_rd_q + take;
                if (push_done && !done_full) done_wr_q <= done_wr_q + 1'b1;
                if (frame_start_i) begin
                    if (!done_empty) done_rd_q <= done_rd_q + 1'b1;
                    {t0_valid_o, t0_o} <= done_empty ? 65'd0
                                                     : done_mem[done_rd_q[DONE_AW-1:0]];
                end
                lost_o <= (status && short) || (sfd_i && pend_full)
                        || (push_done && done_full) || (frame_start_i && done_empty);
            end
        end
    end else begin : g_cut_through
        if (PEND_AW > 8) begin : g_bad_depth
            $error("t0_track: cut-through counts frames in 9 bits; PEND_AW must be <= 8");
        end
        logic [64:0]      hold_q;       // {valid, t0} for a frame judged before its start
        logic             hold_full_q;  // hold_q is waiting for its start
        logic             started_q;    // a frame started (not from hold) since the last status
        // PEND occupancy kept in registers (= pend_wr_q - pend_rd_q) so the pop logic does
        // not start with a pointer subtract (timing at 125 MHz behind eth_rx's status).
        logic [PEND_AW:0] cnt_q;        // PEND entries
        logic             some_q;       // cnt_q != 0 (0 at power-up, like cnt_q)
        logic             pend_empty, start_pop, need_hold, hold_take, short, push;
        logic [8:0]       want;         // frames the status covers
        logic [9:0]       need;         // entries this cycle wants: start + hold + status
        logic [PEND_AW:0] pops;         // entries popped this cycle (<= cnt_q)

        always_comb begin
            pend_empty = !some_q;
            push       = sfd_i && !pend_full;
            start_pop  = frame_start_i && !hold_full_q && !pend_empty;
            want       = (ovf_i ? 9'(ovf_frames_i) : 9'd1) - 9'(emitted_i);
            need_hold  = status && emitted_i && !started_q && !frame_start_i;
            // an entry left after the start's pop
            hold_take  = need_hold && (start_pop ? cnt_q > (PEND_AW + 1)'(1) : some_q);
            // the status takes want entries after start and hold, or what is left
            need       = 10'(want) + 10'(start_pop) + 10'(hold_take);
            short      = status && need > 10'(cnt_q);
            pops       = short ? cnt_q : need[PEND_AW:0];
            if (!status) pops = (PEND_AW + 1)'(start_pop);
        end

        always_ff @(posedge clk_i) begin
            if (need_hold) hold_q <= hold_take ? {1'b1, pend_mem[pend_rd_q[PEND_AW-1:0]]}
                                               : 65'd0;
        end

        always_ff @(posedge clk_i) begin
            if (rst_i) begin
                pend_wr_q   <= '0;
                pend_rd_q   <= '0;
                cnt_q       <= '0;
                some_q      <= 1'b0;
                hold_full_q <= 1'b0;
                started_q   <= 1'b0;
                t0_o        <= '0;
                t0_valid_o  <= 1'b0;
                lost_o      <= 1'b0;
            end else begin
                if (push) pend_wr_q <= pend_wr_q + 1'b1;
                pend_rd_q <= pend_rd_q + pops;
                cnt_q     <= cnt_q + (PEND_AW + 1)'(push) - pops;
                some_q    <= cnt_q + (PEND_AW + 1)'(push) != pops;
                if (frame_start_i) begin
                    {t0_valid_o, t0_o} <= hold_full_q ? hold_q
                                        : pend_empty  ? 65'd0
                                        : {1'b1, pend_mem[pend_rd_q[PEND_AW-1:0]]};
                end
                if (need_hold)                         hold_full_q <= 1'b1;
                else if (frame_start_i && hold_full_q) hold_full_q <= 1'b0;
                if (status)                              started_q <= 1'b0;
                else if (frame_start_i && !hold_full_q)  started_q <= 1'b1;
                lost_o <= short || (need_hold && !hold_take) || (sfd_i && pend_full)
                        || (frame_start_i && !hold_full_q && pend_empty);
            end
        end

`ifndef SYNTHESIS
        always_ff @(posedge clk_i) begin
            if (!rst_i) begin
                assert (cnt_q == pend_count);
                assert (some_q == (cnt_q != '0));
            end
        end
`endif
    end

endmodule
`default_nettype wire
