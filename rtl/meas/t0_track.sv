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
// Latency: t0_o is registered, valid the cycle after frame_start_i. Memory: two small
// arrays, read asynchronously at the head (LUT RAM). Reset: synchronous, active-high.
module t0_track #(
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
    input  wire logic        frame_start_i,
    output logic [63:0]      t0_o,
    output logic             t0_valid_o,
    output logic             lost_o
);
    localparam int PEND_N = 1 << PEND_AW;
    localparam int DONE_N = 1 << DONE_AW;

    logic [63:0]       pend_mem [PEND_N];
    logic [PEND_AW:0]  pend_wr_q, pend_rd_q;
    logic [64:0]       done_mem [DONE_N];  // {valid, t0}
    logic [DONE_AW:0]  done_wr_q, done_rd_q;

    logic [PEND_AW:0]  pend_count, take;
    logic [DONE_AW:0]  done_count;
    logic              status, short, pend_full, done_full, done_empty, push_done;
    logic [64:0]       done_in;

    always_comb begin
        pend_count = pend_wr_q - pend_rd_q;
        done_count = done_wr_q - done_rd_q;
        pend_full  = pend_count == (PEND_AW + 1)'(PEND_N);
        done_full  = done_count == (DONE_AW + 1)'(DONE_N);
        done_empty = done_count == '0;
        status     = ok_i || drop_i || ovf_i;
        // frames this status covers, clipped to what PEND holds
        short      = ovf_i ? 9'(ovf_frames_i) > 9'(pend_count) : pend_count == '0;
        take       = !status ? '0
                   : ovf_i   ? (short ? pend_count : (PEND_AW + 1)'(ovf_frames_i))
                   : (short ? '0 : (PEND_AW + 1)'(1));
        push_done  = ok_i;
        done_in    = short ? 65'd0 : {1'b1, pend_mem[pend_rd_q[PEND_AW-1:0]]};
    end

    always_ff @(posedge clk_i) begin
        if (sfd_i && !pend_full) pend_mem[pend_wr_q[PEND_AW-1:0]] <= now_i;
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
            // (counted). PEND full needs 64 frames in eth_rx's FIFO, which cannot hold them.
            if (sfd_i && !pend_full) pend_wr_q <= pend_wr_q + 1'b1;
            pend_rd_q <= pend_rd_q + take;
            if (push_done && !done_full) done_wr_q <= done_wr_q + 1'b1;
            if (frame_start_i) begin
                if (!done_empty) done_rd_q <= done_rd_q + 1'b1;
                {t0_valid_o, t0_o} <= done_empty ? 65'd0 : done_mem[done_rd_q[DONE_AW-1:0]];
            end
            lost_o <= (status && short) || (sfd_i && pend_full)
                    || (push_done && done_full) || (frame_start_i && done_empty);
        end
    end

endmodule
`default_nettype wire
