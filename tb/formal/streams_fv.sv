`default_nettype none
// Formal harness: AXI-Stream handshake at the pipeline's small stream blocks (buildplan
// §8.3). Each block's input obeys the rule (assumed: axis_hold ASSUME), its output must
// (asserted). Every other input is free, including mid-run resets.
//   pkt_gate:    also the whole-packet rule from its header (in pkt_gate under FORMAL).
//                While block_i is high the consumer is in reset, so its handshake is void.
//   byte_packer: its own SVA (tkeep contiguous, only a last beat partial, a waiting beat
//                complete, a stalled beat held) are proved here, not only simulated.
module streams_fv (
    input wire logic       clk_i,
    input wire logic       rst_i,
    // pkt_gate
    input wire logic       g_block_i,
    input wire logic [7:0] g_tdata_i,
    input wire logic       g_tvalid_i,
    input wire logic       g_tlast_i,
    input wire logic       g_tuser_i,
    input wire logic       g_m_tready_i,
    // byte_packer
    input wire logic [7:0] p_tdata_i,
    input wire logic       p_tvalid_i,
    input wire logic       p_tlast_i,
    input wire logic       p_tuser_i,
    input wire logic [3:0] p_meta_i,
    input wire logic       p_m_tready_i
);
    logic past = 1'b0;
    always_ff @(posedge clk_i) past <= 1'b1;
    always_comb if (!past) assume (rst_i);

    // ---------------------------------------------------------------- pkt_gate
    logic       g_s_tready, g_m_tvalid, g_m_tlast, g_m_tuser;
    logic [7:0] g_m_tdata;
    pkt_gate #(.DATA_W(8)) u_gate (
        .clk_i, .rst_i, .block_i(g_block_i),
        .s_tdata_i(g_tdata_i), .s_tvalid_i(g_tvalid_i), .s_tready_o(g_s_tready),
        .s_tlast_i(g_tlast_i), .s_tuser_i(g_tuser_i),
        .m_tdata_o(g_m_tdata), .m_tvalid_o(g_m_tvalid), .m_tready_i(g_m_tready_i),
        .m_tlast_o(g_m_tlast), .m_tuser_o(g_m_tuser)
    );
    axis_hold #(.W(10), .ASSUME(1)) u_g_in (
        .clk_i, .rst_i, .valid_i(g_tvalid_i), .ready_i(g_s_tready),
        .payload_i({g_tdata_i, g_tlast_i, g_tuser_i})
    );
    axis_hold #(.W(10)) u_g_out (
        .clk_i, .rst_i(rst_i || g_block_i), .valid_i(g_m_tvalid), .ready_i(g_m_tready_i),
        .payload_i({g_m_tdata, g_m_tlast, g_m_tuser})
    );
    // The whole-packet rule, its invariant and cover are in pkt_gate (FORMAL).

    // ---------------------------------------------------------------- byte_packer
    logic        p_s_tready, p_m_tvalid, p_m_tlast, p_m_tuser;
    logic [63:0] p_m_tdata;
    logic [7:0]  p_m_tkeep;
    logic [3:0]  p_m_meta;
    byte_packer #(.LANES(8), .META_W(4)) u_packer (
        .clk_i, .rst_i,
        .s_tdata_i(p_tdata_i), .s_tvalid_i(p_tvalid_i), .s_tready_o(p_s_tready),
        .s_tlast_i(p_tlast_i), .s_tuser_i(p_tuser_i), .s_meta_i(p_meta_i),
        .m_tdata_o(p_m_tdata), .m_tkeep_o(p_m_tkeep), .m_tvalid_o(p_m_tvalid),
        .m_tready_i(p_m_tready_i), .m_tlast_o(p_m_tlast), .m_tuser_o(p_m_tuser),
        .m_meta_o(p_m_meta)
    );
    axis_hold #(.W(14), .ASSUME(1)) u_p_in (
        .clk_i, .rst_i, .valid_i(p_tvalid_i), .ready_i(p_s_tready),
        .payload_i({p_tdata_i, p_tlast_i, p_tuser_i, p_meta_i})
    );
    axis_hold #(.W(64 + 8 + 1 + 1 + 4)) u_p_out (
        .clk_i, .rst_i, .valid_i(p_m_tvalid), .ready_i(p_m_tready_i),
        .payload_i({p_m_tdata, p_m_tkeep, p_m_tlast, p_m_tuser, p_m_meta})
    );
    always_comb if (past && !rst_i) begin
        cover (p_m_tvalid && p_m_tkeep == '1 && !p_m_tready_i);
        cover (p_m_tvalid && p_m_tlast && p_m_tkeep == 8'h07);
    end
endmodule
`default_nettype wire
