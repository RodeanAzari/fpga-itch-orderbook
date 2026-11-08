`default_nettype none
// sync_fifo: single-clock first-word-fall-through FIFO on block RAM (sdp_ram).
//
// FWFT: when empty_o is low, rd_data_o already holds the oldest entry; rd_en_i pops it.
// The RAM is read one entry ahead (read address = the next read pointer), so pops can
// happen on every cycle. A write becomes visible on rd_data_o 2 cycles after it is
// accepted (wr_vis_q lags the write pointer by one cycle, so the RAM read of a freshly
// written address never returns the old contents).
// full_o counts every accepted write, so the RAM never overflows.
// flush_i: empties the FIFO in one cycle (pointers to 0); takes priority over wr/rd.
// Writes into a full FIFO and pops of an empty FIFO are protocol errors (asserted).
// Reset: synchronous, active-high (pointers only; the RAM is not cleared).
module sync_fifo #(
    parameter int ADDR_W = 11,
    parameter int DATA_W = 8
) (
    input  wire logic              clk_i,
    input  wire logic              rst_i,
    input  wire logic              flush_i,
    input  wire logic              wr_en_i,
    input  wire logic [DATA_W-1:0] wr_data_i,
    output logic                   full_o,
    input  wire logic              rd_en_i,
    output logic [DATA_W-1:0]      rd_data_o,
    output logic                   empty_o
);
    localparam int PTR_W = ADDR_W + 1;  // extra bit tells full from empty
    localparam logic [PTR_W-1:0] DEPTH = PTR_W'(2 ** ADDR_W);

    logic [PTR_W-1:0] wr_q, wr_vis_q, rd_q, rd_d;

    assign full_o  = (wr_q - rd_q) == DEPTH;
    assign empty_o = wr_vis_q == rd_q;
    assign rd_d    = rd_q + PTR_W'(rd_en_i);

    always_ff @(posedge clk_i) begin
        if (rst_i || flush_i) begin
            wr_q     <= '0;
            wr_vis_q <= '0;
            rd_q     <= '0;
        end else begin
            wr_q     <= wr_q + PTR_W'(wr_en_i);
            wr_vis_q <= wr_q;
            rd_q     <= rd_d;
        end
    end

    sdp_ram #(.ADDR_W(ADDR_W), .DATA_W(DATA_W), .OUT_REG(1'b0)) u_ram (
        .clk_i(clk_i), .we_i(wr_en_i), .waddr_i(wr_q[ADDR_W-1:0]), .wdata_i(wr_data_i),
        .raddr_i(rd_d[ADDR_W-1:0]), .rdata_o(rd_data_o)
    );

`ifndef SYNTHESIS
    always_ff @(posedge clk_i) begin
        if (!rst_i && !flush_i) begin
            assert (!(wr_en_i && full_o));
            assert (!(rd_en_i && empty_o));
        end
    end
`endif
endmodule
`default_nettype wire
