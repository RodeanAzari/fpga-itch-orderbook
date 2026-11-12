`default_nettype none
// injector: replays a ROM of MoldUDP64 payloads once, at up to one byte per cycle
// (buildplan 6.7, ADR 0014). Golden model: model/injector.py rom_words (the image) and
// build_image (its content).
//
// ROM image (INIT_FILE, $readmemh, 9-bit words): word 0, 1 = payload byte count N
// (big-endian), then N words {last, byte}: last = the final byte of its payload.
// After rst_i falls the module reads the count (3 cycles), then streams the N bytes on
// m_* (AXI-Stream, honours backpressure) and stops; done_o is high once all N bytes
// have been accepted. Hold rst_i to replay again.
// Throughput: one beat per cycle while m_tready_i is high (a 2-entry output FIFO covers
// the ROM's 1-cycle read latency). Latency: first beat valid 4 cycles after reset.
// Reset: synchronous, active-high (control state; the ROM and FIFO data are not reset).
module injector #(
    parameter int    DEPTH     = 32768,           // ROM words, power of two
    parameter string INIT_FILE = "injector.mem"
) (
    input  wire logic       clk_i,
    input  wire logic       rst_i,
    output logic [7:0]      m_tdata_o,
    output logic            m_tvalid_o,
    input  wire logic       m_tready_i,
    output logic            m_tlast_o,
    output logic            done_o
);
    localparam int AW  = $clog2(DEPTH);
    localparam int HDR = 2;                       // header words (byte count)

    (* rom_style = "block" *) logic [8:0] rom [DEPTH];
    initial $readmemh(INIT_FILE, rom);

    logic [AW:0]  addr_q, end_q;                  // next read; one past the last byte
    logic [8:0]   rom_q;
    logic [1:0]   hdr_q;                          // header word read last cycle (1, 2)
    logic         have_len_q, rd_q, issue, pop;
    logic [8:0]   f0_q, f1_q;
    logic [1:0]   cnt_q;

    assign pop        = m_tvalid_o && m_tready_i;
    assign m_tvalid_o = cnt_q != 2'd0;
    assign m_tdata_o  = f0_q[7:0];
    assign m_tlast_o  = f0_q[8];
    // a payload read only if its byte will have a FIFO slot next cycle
    assign issue = have_len_q && addr_q != end_q
                   && (3'(cnt_q) + 3'(rd_q) - 3'(pop)) < 3'd2;
    assign done_o = have_len_q && addr_q == end_q && !rd_q && cnt_q == 2'd0;

    always_ff @(posedge clk_i) begin
        if (issue || addr_q < (AW+1)'(HDR)) rom_q <= rom[addr_q[AW-1:0]];
    end

    always_ff @(posedge clk_i) begin
        if (rst_i) begin
            addr_q     <= '0;
            hdr_q      <= '0;
            have_len_q <= 1'b0;
            rd_q       <= 1'b0;
            cnt_q      <= '0;
        end else begin
            rd_q  <= issue;
            hdr_q <= addr_q < (AW+1)'(HDR) ? 2'(addr_q) + 2'd1 : 2'd0;
            if (addr_q < (AW+1)'(HDR) || issue) addr_q <= addr_q + 1'b1;
            if (hdr_q == 2'd1) end_q <= (AW+1)'({rom_q[7:0], 8'd0}) + (AW+1)'(HDR);
            if (hdr_q == 2'd2) begin
                end_q      <= end_q + (AW+1)'(rom_q[7:0]);
                have_len_q <= 1'b1;
            end
            case ({rd_q, pop})
                2'b10: begin
                    if (cnt_q == 2'd0) f0_q <= rom_q;
                    else               f1_q <= rom_q;
                    cnt_q <= cnt_q + 2'd1;
                end
                2'b01: begin
                    f0_q  <= f1_q;
                    cnt_q <= cnt_q - 2'd1;
                end
                2'b11: begin
                    if (cnt_q == 2'd1) f0_q <= rom_q;
                    else begin
                        f0_q <= f1_q;
                        f1_q <= rom_q;
                    end
                end
                default: ;
            endcase
        end
    end

`ifndef SYNTHESIS
    always_ff @(posedge clk_i) begin
        if (!rst_i) begin
            assert (cnt_q <= 2'd2);
            assert (!(rd_q && !pop && cnt_q == 2'd2));  // never pushes into a full FIFO
            if ($past(m_tvalid_o && !m_tready_i) && !$past(rst_i))  // AXI-S: held
                assert (m_tvalid_o && m_tdata_o == $past(m_tdata_o)
                        && m_tlast_o == $past(m_tlast_o));
        end
    end
`endif
endmodule
`default_nettype wire
