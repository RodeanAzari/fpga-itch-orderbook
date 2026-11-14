`default_nettype none
// injector: replays a ROM of MoldUDP64 payloads once, at up to one beat per cycle
// (buildplan 6.7, ADR 0014; DATA_W 64: ADR 0015). Golden model: model/injector.py
// rom_words (the image, lanes = DATA_W / 8) and build_image (its content).
//
// ROM image (INIT_FILE, $readmemh): words of DATA_W / 8 9-bit lanes, lane i = bits
// 9i+8..9i = {last, byte}; last = the final byte of its payload. Header: the data word
// count N, big-endian, in lanes 0 and 1 (DATA_W 8: words 0 and 1; DATA_W 64: word 0). Then
// N data words; each payload starts at lane 0 of a word, lanes after its last byte are 0.
// After rst_i falls the module reads the count (3 cycles), then streams the N words on
// m_* (AXI-Stream, honours backpressure) and stops; done_o is high once all N have been
// accepted. Hold rst_i to replay again. Per beat: m_tdata_o lane i = lane i's byte,
// m_tlast_o = any last bit, m_tkeep_o = the lanes up to the first last bit (all if none).
// Throughput: one beat per cycle while m_tready_i is high (a 2-entry output FIFO covers
// the ROM's 1-cycle read latency). Latency: the first beat is valid after the 5th rising
// edge with rst_i low at DATA_W 8, the 4th at 64 (one header word fewer).
// Reset: synchronous, active-high (control state; the ROM and FIFO data are not reset).
module injector #(
    parameter int    DATA_W    = 8,                        // 8 or 64
    parameter int    DEPTH     = 32768 * 8 / DATA_W,       // ROM words, power of two
    parameter string INIT_FILE = "injector.mem"
) (
    input  wire logic                clk_i,
    input  wire logic                rst_i,
    output logic [DATA_W-1:0]        m_tdata_o,
    output logic [DATA_W/8-1:0]      m_tkeep_o,
    output logic                     m_tvalid_o,
    input  wire logic                m_tready_i,
    output logic                     m_tlast_o,
    output logic                     done_o
);
    localparam int LANES  = DATA_W / 8;
    localparam int WORD_W = 9 * LANES;
    if (DATA_W != 8 && DATA_W != 64) begin : g_bad_width
        $error("injector: DATA_W must be 8 or 64");
    end
    localparam int AW  = $clog2(DEPTH);
    localparam int HDR = LANES == 1 ? 2 : 1;      // header words (data word count)

    (* rom_style = "block" *) logic [WORD_W-1:0] rom [DEPTH];
    initial $readmemh(INIT_FILE, rom);

    logic [AW:0]  addr_q, end_q;                  // next read; one past the last word
    logic [WORD_W-1:0] rom_q;
    logic [1:0]   hdr_q;                          // header word read last cycle (1, 2)
    logic         have_len_q, rd_q, issue, pop;
    logic [WORD_W-1:0] f0_q, f1_q;
    logic [AW:0]  count_hi;                       // count from header word 0 (8: high byte)
    logic [1:0]   cnt_q;

    assign pop        = m_tvalid_o && m_tready_i;
    assign m_tvalid_o = cnt_q != 2'd0;

    always_comb begin
        m_tkeep_o = '0;
        m_tlast_o = 1'b0;
        for (int i = 0; i < LANES; i++) begin
            m_tdata_o[8*i +: 8] = f0_q[9*i +: 8];
            m_tkeep_o[i]        = !m_tlast_o;     // no last bit in a lower lane
            m_tlast_o           = m_tlast_o || f0_q[9*i + 8];
        end
    end

    if (LANES == 1) begin : g_hdr2
        assign count_hi = (AW+1)'({rom_q[7:0], 8'd0});
    end else begin : g_hdr1
        assign count_hi = (AW+1)'({rom_q[7:0], rom_q[16:9]});  // N < DEPTH
    end

    // a data word read only if it will have a FIFO slot next cycle
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
            if (hdr_q == 2'd1) begin
                end_q <= count_hi + (AW+1)'(HDR);
                if (HDR == 1) have_len_q <= 1'b1;
            end
            if (hdr_q == 2'd2) begin                  // HDR 2 only
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
                        && m_tkeep_o == $past(m_tkeep_o) && m_tlast_o == $past(m_tlast_o));
        end
    end
`endif
endmodule
`default_nettype wire
