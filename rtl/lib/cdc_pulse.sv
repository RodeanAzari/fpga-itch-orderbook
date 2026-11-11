`default_nettype none
// cdc_pulse: carries single-cycle pulses from src_clk_i's domain to dst_clk_i's (toggle
// synchronizer). Each source pulse flips a toggle; the destination sees the flip after
// STAGES ASYNC_REG flops and emits one dst_pulse_o.
// Constraint: source pulses must be at least (STAGES + 2) destination cycles apart, or two
// flips can merge into none. For T0 (ADR 0011) the source is eth_rx's SFD: frames are at
// least 84 bytes apart on the wire (6.7 us), far above STAGES + 2 core cycles.
// Listed CDC (docs/design.md): the toggle into an ASYNC_REG chain, set_max_delay
// -datapath_only on stage 0 (constr/arty_timing.xdc).
// Latency: STAGES + 1 dst cycles from the toggle's flip to dst_pulse_o (plus up to one
// dst cycle of phase); the constant is calibrated in docs/latency.md.
// Reset: synchronous, active-high, one per domain.
module cdc_pulse #(
    parameter int STAGES = 2
) (
    input  wire logic src_clk_i,
    input  wire logic src_rst_i,
    input  wire logic src_pulse_i,
    input  wire logic dst_clk_i,
    input  wire logic dst_rst_i,
    output logic      dst_pulse_o
);
    logic tgl_q;
    always_ff @(posedge src_clk_i) begin
        if (src_rst_i)        tgl_q <= 1'b0;
        else if (src_pulse_i) tgl_q <= !tgl_q;
    end

    (* ASYNC_REG = "TRUE" *) logic [STAGES-1:0] sync_q;
    logic                                      last_q;
    always_ff @(posedge dst_clk_i) begin
        sync_q <= {sync_q[STAGES-2:0], tgl_q};
        if (dst_rst_i) begin
            // Follow the toggle in reset: a flip during reset is dropped, never emitted
            // late (for T0 a dropped SFD is counted downstream, a late one is not).
            last_q      <= sync_q[STAGES-1];
            dst_pulse_o <= 1'b0;
        end else begin
            last_q      <= sync_q[STAGES-1];
            dst_pulse_o <= sync_q[STAGES-1] != last_q;
        end
    end
endmodule
`default_nettype wire
