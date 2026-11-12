`default_nettype none
// inj_meter: the injector run's measurement (ADR 0014). Golden model: model/injector.py
// digest and expected_run.
//
// beat_i: an injector beat accepted by the feed (the first one starts the span).
// rec_valid_i / rec_i: a BBO record's first 40 bytes, the part before t0 (docs/protocol.md
//   layout, byte 0 in the top 8 bits), taken this cycle; the meter is always ready.
// cycles_o: now at the last record - now at the first beat + 1 (0 before any record).
// bbos_o:   records taken. digest_o: rotl(d, 1) ^ (XOR of the record's 64-bit words,
//   t0/t1/t2 zeroed), per record in order.
// Latency: outputs include a record 2 cycles after it is taken (register, fold).
// Reset: synchronous, active-high; zeroes the outputs (rst_i = a new config).
module inj_meter (
    input  wire logic          clk_i,
    input  wire logic          rst_i,
    input  wire logic [63:0]   now_i,
    input  wire logic          beat_i,
    input  wire logic          rec_valid_i,
    input  wire logic [319:0]  rec_i,
    output logic [63:0]        cycles_o,
    output logic [63:0]        bbos_o,
    output logic [63:0]        digest_o
);
    localparam int KEEP = 40 * 8;  // record bytes before t0 (docs/protocol.md BBO record)

    logic              started_q, v_q;
    logic [63:0]       first_q, at_q, fold;
    logic [KEEP-1:0]   rec_q;

    always_comb begin
        fold = '0;
        for (int w = 0; w < KEEP / 64; w++) fold ^= rec_q[KEEP-1-64*w -: 64];
    end

    always_ff @(posedge clk_i) begin
        v_q <= rec_valid_i && !rst_i;
        if (rec_valid_i) begin
            rec_q <= rec_i;
            at_q  <= now_i;
        end
        if (rst_i) begin
            started_q <= 1'b0;
            cycles_o  <= '0;
            bbos_o    <= '0;
            digest_o  <= '0;
        end else begin
            if (beat_i && !started_q) begin
                started_q <= 1'b1;
                first_q   <= now_i;
            end
            if (v_q) begin
                cycles_o <= at_q - first_q + 64'd1;
                bbos_o   <= bbos_o + 64'd1;
                digest_o <= {digest_o[62:0], digest_o[63]} ^ fold;
            end
        end
    end

`ifndef SYNTHESIS
    // a BBO comes from an injected message: the span has started
    always_ff @(posedge clk_i) begin
        if (!rst_i && v_q) assert (started_q);
    end
`endif
endmodule
`default_nettype wire
