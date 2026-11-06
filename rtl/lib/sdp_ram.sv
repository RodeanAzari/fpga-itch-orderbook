`default_nettype none
// sdp_ram: simple dual-port RAM, one write port and one registered read port, one clock.
// Written so Vivado infers block RAM (no reset on the array or the read register).
//
// Read latency: 1 cycle (rdata_o holds mem[raddr_i] from the previous cycle). A read and
// a write to the same address in the same cycle return the old data. Contents are
// undefined after power-up: callers clear what they rely on.
module sdp_ram #(
    parameter int ADDR_W = 9,
    parameter int DATA_W = 72
) (
    input  wire logic              clk_i,
    input  wire logic              we_i,
    input  wire logic [ADDR_W-1:0] waddr_i,
    input  wire logic [DATA_W-1:0] wdata_i,
    input  wire logic [ADDR_W-1:0] raddr_i,
    output logic      [DATA_W-1:0] rdata_o
);
    logic [DATA_W-1:0] mem [2**ADDR_W];

    always_ff @(posedge clk_i) begin
        if (we_i) mem[waddr_i] <= wdata_i;
        rdata_o <= mem[raddr_i];
    end
endmodule
`default_nettype wire
