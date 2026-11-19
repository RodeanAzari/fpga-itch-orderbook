`default_nettype none
// Formal harness for sdp_ram (the read contract order_table's proof assumes; properties
// in sdp_ram under FORMAL). Both read latencies; every input free.
module sdp_ram_fv (
    input wire logic       clk_i,
    input wire logic       we_i,
    input wire logic [2:0] waddr_i,
    input wire logic [3:0] wdata_i,
    input wire logic [2:0] raddr_i
);
    sdp_ram #(.ADDR_W(3), .DATA_W(4), .OUT_REG(1'b0)) u_ram0 (
        .clk_i, .we_i, .waddr_i, .wdata_i, .raddr_i, .rdata_o()
    );
    sdp_ram #(.ADDR_W(3), .DATA_W(4), .OUT_REG(1'b1)) u_ram1 (
        .clk_i, .we_i, .waddr_i, .wdata_i, .raddr_i, .rdata_o()
    );
endmodule
`default_nettype wire
