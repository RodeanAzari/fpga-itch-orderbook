`default_nettype none
// byte_packer: a byte stream -> beats of LANES bytes (ADR 0015: the network feed into the
// DATA_W 64 deframer). Bytes fill lanes from 0 in arrival order (lane i = tdata bits
// 8i+7..8i); a beat leaves when it holds LANES bytes or a packet's last byte. So output
// beats are full except a packet's last, whose tkeep is contiguous from lane 0
// (mold_deframer's input contract). Lanes outside tkeep hold stale data.
// m_tuser_o: s_tuser_i of the beat's final byte (on tlast: the packet's last byte's).
// m_meta_o:  s_meta_i at the packet's first byte, for all of its beats (T0, ADR 0011).
// Latency: 1 cycle from the byte that completes a beat to the beat. Throughput: one byte
// per cycle: a beat leaves on the cycle the next packet byte enters.
// Backpressure: s_tready_o = no complete beat waiting, or it leaves this cycle.
// Reset: synchronous, active-high (control state; data, keep and meta are not reset).
module byte_packer #(
    parameter int LANES  = 8,   // power of two, >= 2
    parameter int META_W = 64
) (
    input  wire logic                clk_i,
    input  wire logic                rst_i,
    input  wire logic [7:0]          s_tdata_i,
    input  wire logic                s_tvalid_i,
    output logic                     s_tready_o,
    input  wire logic                s_tlast_i,
    input  wire logic                s_tuser_i,
    input  wire logic [META_W-1:0]   s_meta_i,
    output logic [8*LANES-1:0]       m_tdata_o,
    output logic [LANES-1:0]         m_tkeep_o,
    output logic                     m_tvalid_o,
    input  wire logic                m_tready_i,
    output logic                     m_tlast_o,
    output logic                     m_tuser_o,
    output logic [META_W-1:0]        m_meta_o
);
    localparam int LW = $clog2(LANES);
    if (LANES < 2 || (1 << LW) != LANES) begin : g_bad_lanes
        $error("byte_packer: LANES must be a power of two >= 2");
    end

    // The output register is also the accumulator: lanes fill while m_tvalid_o is low.
    logic [LW-1:0] lane_q;   // lane of the next byte
    logic          first_q;  // the next byte starts a packet
    logic          take;

    assign s_tready_o = !m_tvalid_o || m_tready_i;
    assign take       = s_tvalid_i && s_tready_o;

    always_ff @(posedge clk_i) begin
        if (take) begin
            m_tdata_o[8*lane_q +: 8] <= s_tdata_i;
            m_tkeep_o <= lane_q == '0 ? LANES'(1) : m_tkeep_o | (LANES'(1) << lane_q);
            m_tlast_o <= s_tlast_i;
            m_tuser_o <= s_tuser_i;
            if (first_q) m_meta_o <= s_meta_i;
        end
        if (rst_i) begin
            lane_q     <= '0;
            first_q    <= 1'b1;
            m_tvalid_o <= 1'b0;
        end else begin
            if (take) begin
                lane_q  <= s_tlast_i ? '0 : lane_q + 1'b1;  // wraps to 0 on a full beat
                first_q <= s_tlast_i;
            end
            if (take)            m_tvalid_o <= s_tlast_i || lane_q == LW'(LANES - 1);
            else if (m_tready_i) m_tvalid_o <= 1'b0;
        end
    end

`ifndef SYNTHESIS
    always_ff @(posedge clk_i) begin
        if (!rst_i) begin
            if (m_tvalid_o) begin
                // tkeep contiguous from lane 0; only a packet's last beat is partial
                assert (m_tkeep_o != '0 && (m_tkeep_o & (m_tkeep_o + 1'b1)) == '0);
                assert (m_tlast_o || m_tkeep_o == '1);
                assert (lane_q == '0);  // a waiting beat is complete
            end
            if ($past(m_tvalid_o && !m_tready_i) && !$past(rst_i))  // AXI-S: held
                assert (m_tvalid_o && m_tdata_o == $past(m_tdata_o)
                        && m_tkeep_o == $past(m_tkeep_o) && m_tlast_o == $past(m_tlast_o)
                        && m_tuser_o == $past(m_tuser_o) && m_meta_o == $past(m_meta_o));
        end
    end
`endif
endmodule
`default_nettype wire
