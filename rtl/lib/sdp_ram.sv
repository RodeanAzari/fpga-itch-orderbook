`default_nettype none
// sdp_ram: simple dual-port RAM, one write port and one registered read port, one clock.
// Written so Vivado infers block RAM (no reset on the array or the read register).
//
// Read latency: 1 + OUT_REG cycles. OUT_REG = 1 adds the output register Vivado places
// inside the BRAM (DOB_REG), which cuts the BRAM clock-to-out from the read path. A read
// and a write to the same address in the same cycle return the old data. Contents are
// undefined after power-up: callers clear what they rely on.
module sdp_ram #(
    parameter int ADDR_W  = 9,
    parameter int DATA_W  = 72,
    parameter bit OUT_REG = 1'b0
) (
    input  wire logic              clk_i,
    input  wire logic              we_i,
    input  wire logic [ADDR_W-1:0] waddr_i,
    input  wire logic [DATA_W-1:0] wdata_i,
    input  wire logic [ADDR_W-1:0] raddr_i,
    output logic      [DATA_W-1:0] rdata_o
);
    logic [DATA_W-1:0] mem [2**ADDR_W];
    logic [DATA_W-1:0] rd_q;

    always_ff @(posedge clk_i) begin
        if (we_i) mem[waddr_i] <= wdata_i;
        rd_q <= mem[raddr_i];
    end

    if (OUT_REG) begin : g_out_reg
        always_ff @(posedge clk_i) rdata_o <= rd_q;
    end else begin : g_no_out_reg
        assign rdata_o = rd_q;
    end

`ifdef FORMAL
    // tb/formal/sdp_ram.sby: the read contract that order_table's proof assumes. For one
    // address A (any constant): once A is written, a read of A returns the last data
    // written to A before the read cycle (a same-cycle write is not seen), 1 + OUT_REG
    // cycles after the read address is presented.
    logic [ADDR_W-1:0] f_addr;  // no initial value and held: any constant
    logic              f_written = 1'b0, f_rd_q = 1'b0, f_rd2_q = 1'b0;
    logic [DATA_W-1:0] f_data, f_exp_q, f_exp2_q;
    always_ff @(posedge clk_i) begin
        f_addr   <= f_addr;
        if (we_i && waddr_i == f_addr) begin
            f_written <= 1'b1;
            f_data    <= wdata_i;
        end
        f_rd_q   <= f_written && raddr_i == f_addr;
        f_exp_q  <= f_data;
        f_rd2_q  <= f_rd_q;
        f_exp2_q <= f_exp_q;
    end
    always_comb begin
        if (f_written) assert (mem[f_addr] == f_data);
        if (OUT_REG ? f_rd2_q : f_rd_q) assert (rdata_o == (OUT_REG ? f_exp2_q : f_exp_q));
        if (OUT_REG ? f_rd2_q : f_rd_q) cover (rdata_o != '0);
    end
`endif
endmodule
`default_nettype wire
