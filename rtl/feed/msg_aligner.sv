`default_nettype none
// msg_aligner: puts each message's first byte in lane 0 (ADR 0015 step 2), between
// mold_deframer (DATA_W 64) and itch_decoder, so the decoder can index its capture
// registers by byte offset alone.
//
// Input:  one message per tlast in mold_deframer's output shape: contiguous tkeep; only a
//         message's first beat may start above lane 0, only its last may end below the
//         top lane. s_seq_i, s_t0_i are constant through a message; s_tuser_i on tlast =
//         discard the message.
// Output: the same bytes, packed: lane 0 of a message's first beat is its byte 0, every
//         beat is full except the last, whose tkeep is contiguous from lane 0. m_seq_o,
//         m_t0_o with every beat; m_tuser_o on tlast as at the input.
//
// How: up to LANES-1 bytes of the current message wait in a residual buffer. Each input
// beat is shifted down to lane 0 and appended after the residual; once LANES bytes are
// there they leave as one beat. A last beat that brings the total above LANES leaves in
// two beats: the second (a "flush") takes one extra cycle with s_tready_o low.
// Latency: 1 cycle from the input beat that completes an output beat to that beat.
// Throughput: one input beat per cycle, plus at most one flush cycle per message.
// Backpressure: s_tready_o = !flush pending && (!m_tvalid_o || m_tready_i).
// Reset: synchronous, active-high; clears the residual and the output.
module msg_aligner #(
    parameter int DATA_W = 64
) (
    input  wire logic                clk_i,
    input  wire logic                rst_i,
    input  wire logic [DATA_W-1:0]   s_tdata_i,
    input  wire logic [DATA_W/8-1:0] s_tkeep_i,
    input  wire logic                s_tvalid_i,
    output logic                     s_tready_o,
    input  wire logic                s_tlast_i,
    input  wire logic                s_tuser_i,
    input  wire logic [63:0]         s_seq_i,
    input  wire logic [63:0]         s_t0_i,
    output logic [DATA_W-1:0]        m_tdata_o,
    output logic [DATA_W/8-1:0]      m_tkeep_o,
    output logic                     m_tvalid_o,
    input  wire logic                m_tready_i,
    output logic                     m_tlast_o,
    output logic                     m_tuser_o,
    output logic [63:0]              m_seq_o,
    output logic [63:0]              m_t0_o
);
    localparam int LANES = DATA_W / 8;
    localparam int CNT_W = $clog2(2 * LANES);  // holds 0 .. 2*LANES-1

    if (DATA_W != 64) begin : g_bad_width
        $error("msg_aligner: only DATA_W = 64 is used and verified (ADR 0015)");
    end

    // Control (reset)
    logic                 flush_q;            // the residual is a message's last beat
    logic [CNT_W-1:0]     res_n_q;            // bytes in the residual, 0 .. LANES-1
    // Datapath (not reset; residual lanes at and above res_n_q are zero)
    logic [DATA_W-1:0]    res_q;
    logic                 res_user_q;
    logic [63:0]          res_seq_q, res_t0_q;

    logic                 take, full;
    logic [CNT_W-1:0]     lo, nb, tot;
    logic [DATA_W-1:0]    din;
    logic [2*DATA_W-1:0]  cat;
    logic [DATA_W/8-1:0]  tail_keep;

    // Lowest kept lane and byte count of a contiguous tkeep.
    always_comb begin
        lo = '0;
        nb = '0;
        for (int i = LANES - 1; i >= 0; i--) if (s_tkeep_i[i]) lo = CNT_W'(i);
        for (int i = 0; i < LANES; i++) nb += CNT_W'(s_tkeep_i[i]);
    end

    always_comb begin
        s_tready_o = !flush_q && (!m_tvalid_o || m_tready_i);
        take       = s_tvalid_i && s_tready_o;
        // Kept bytes only, shifted to lane 0, then placed after the residual.
        din = '0;
        for (int i = 0; i < LANES; i++) if (s_tkeep_i[i]) din[8*i +: 8] = s_tdata_i[8*i +: 8];
        din  = din >> (8 * lo);
        cat  = {{DATA_W{1'b0}}, res_q} | ({{DATA_W{1'b0}}, din} << (8 * res_n_q));
        tot  = res_n_q + nb;
        full = tot >= CNT_W'(LANES);
        for (int i = 0; i < LANES; i++) tail_keep[i] = CNT_W'(i) < tot;
    end

    // Datapath.
    always_ff @(posedge clk_i) begin
        if (take) begin
            res_user_q <= s_tuser_i;
            res_seq_q  <= s_seq_i;
            res_t0_q   <= s_t0_i;
            if (full) res_q <= cat[2*DATA_W-1:DATA_W];
            else      res_q <= s_tlast_i ? '0 : cat[DATA_W-1:0];
            if (full || s_tlast_i) begin
                m_tdata_o <= cat[DATA_W-1:0];
                m_seq_o   <= s_seq_i;
                m_t0_o    <= s_t0_i;
            end
        end else if (flush_q && (!m_tvalid_o || m_tready_i)) begin
            res_q     <= '0;
            m_tdata_o <= res_q;
            m_seq_o   <= res_seq_q;
            m_t0_o    <= res_t0_q;
        end
    end

    // Control.
    always_ff @(posedge clk_i) begin
        if (rst_i) begin
            flush_q    <= 1'b0;
            res_n_q    <= '0;
            m_tvalid_o <= 1'b0;
            m_tkeep_o  <= '0;
            m_tlast_o  <= 1'b0;
            m_tuser_o  <= 1'b0;
        end else if (take) begin
            if (full) res_n_q <= tot - CNT_W'(LANES);
            else      res_n_q <= s_tlast_i ? '0 : tot;
            // A last beat that overflows one output beat leaves its rest as a flush.
            flush_q <= s_tlast_i && tot > CNT_W'(LANES);
            if (full || s_tlast_i) begin
                m_tvalid_o <= 1'b1;
                m_tkeep_o  <= full ? '1 : tail_keep;
                m_tlast_o  <= s_tlast_i && tot <= CNT_W'(LANES);
                m_tuser_o  <= s_tlast_i && tot <= CNT_W'(LANES) && s_tuser_i;
            end else if (m_tready_i) begin
                m_tvalid_o <= 1'b0;
            end
        end else if (flush_q && (!m_tvalid_o || m_tready_i)) begin
            flush_q    <= 1'b0;
            res_n_q    <= '0;
            m_tvalid_o <= 1'b1;
            m_tkeep_o  <= {LANES{1'b1}} >> (CNT_W'(LANES) - res_n_q);
            m_tlast_o  <= 1'b1;
            m_tuser_o  <= res_user_q;
        end else if (m_tready_i) begin
            m_tvalid_o <= 1'b0;
        end
    end

`ifndef SYNTHESIS
    // Input contract (mold_deframer's output shape) and output shape.
    logic in_mid_q;  // the next input beat continues a message
    always_ff @(posedge clk_i) begin
        if (rst_i)     in_mid_q <= 1'b0;
        else if (take) in_mid_q <= !s_tlast_i;
    end
    always_ff @(posedge clk_i) begin
        if (!rst_i) begin
            if (s_tvalid_i) begin
                assert (s_tkeep_i != '0);
                assert (((s_tkeep_i >> lo) & ((s_tkeep_i >> lo) + 1'b1)) == '0);
                assert (!in_mid_q || s_tkeep_i[0]);
                assert (s_tlast_i || s_tkeep_i[LANES-1]);
            end
            if (m_tvalid_o) begin
                assert (m_tkeep_o[0] && (m_tkeep_o & (m_tkeep_o + 1'b1)) == '0);
                assert (m_tlast_o || m_tkeep_o == '1);
                assert (!m_tuser_o || m_tlast_o);
            end
            assert (res_n_q < CNT_W'(LANES));
        end
    end
`endif
endmodule
`default_nettype wire
