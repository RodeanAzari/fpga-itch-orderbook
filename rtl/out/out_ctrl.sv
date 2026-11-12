`default_nettype none
// out_ctrl: control-port parser and the config sequence (docs/protocol.md, Config).
// Golden model: model/protocol.py parse_config (accept/reject and the record fields).
//
// Payloads in (s_*): the control port's udp_rx output, one byte per beat, always accepted.
//   A payload is a config when it is exactly 36 bytes, header magic 0x4F42, version 1,
//   type 0x10, count 1, record_len 24, record mode <= 2 and locate_valid <= 1, and
//   s_tuser_i (truncated datagram) is low on its tlast. pkt_seq and the record's last
//   2 bytes are ignored (as parse_config does). ctrl_ok_o / ctrl_bad_o pulse at tlast.
//   An accepted config and its source (src_*_i, udp_rx metadata, valid at tlast) wait in a
//   pending register; a later config that arrives before the sequence takes it replaces
//   it (only the latest is acked).
// Sequence (state):
//   OFF   after reset, until the first config: feed and book held in reset.
//   RST   1 cycle: config and destination latched, feed and book in reset, stats_clear_o.
//   CLEAR feed in reset, book out of reset clearing its RAMs; when book_busy_i is low,
//         ack_req_o pulses.
//   ACK   feed in reset until flush_i (out_builder started the ack); then RUN.
//   RUN   feed running; a pending config starts RST.
//   cfg_*_o and ack_rec_o (the record as received, out_builder's ack_rec_i) change only
//   in RST, so they are stable from ack_req_o until flush_i (out_builder's contract).
// Latency: config tlast -> RST 1 cycle (from RUN or OFF); RST -> ack_req_o after the book
//   clear (max of 2^SET_BITS and 2^WIN_BITS cycles, + 1).
// Reset: synchronous, active-high (state and pending flag; data registers are not reset).
module out_ctrl (
    input  wire logic          clk_i,
    input  wire logic          rst_i,
    // control payloads in (udp_rx)
    input  wire logic [7:0]    s_tdata_i,
    input  wire logic          s_tvalid_i,
    output logic               s_tready_o,
    input  wire logic          s_tlast_i,
    input  wire logic          s_tuser_i,
    input  wire logic [47:0]   src_mac_i,
    input  wire logic [31:0]   src_ip_i,
    input  wire logic [15:0]   src_port_i,
    // configuration out (docs/protocol.md config record)
    output logic [31:0]        cfg_group_o,
    output logic [15:0]        cfg_port_o,
    output logic               cfg_mode_o,         // mode bit 0: 1 = trace
    output logic               cfg_inject_o,       // mode 2: injector run (ADR 0014)
    output logic               cfg_locate_valid_o,
    output logic [63:0]        cfg_symbol_o,
    output logic [31:0]        cfg_lo_price_o,
    output logic [15:0]        cfg_locate_o,
    output logic [47:0]        dst_mac_o,
    output logic [31:0]        dst_ip_o,
    output logic [15:0]        dst_port_o,
    // sequence
    output logic               feed_rst_o,
    output logic               book_rst_o,
    output logic               stats_clear_o,
    input  wire logic          book_busy_i,
    output logic               ack_req_o,
    output logic [24*8-1:0]    ack_rec_o,
    input  wire logic          flush_i,
    // status (one-cycle pulses)
    output logic               ctrl_ok_o,
    output logic               ctrl_bad_o
);
    // docs/protocol.md common header and config record, byte offsets in the payload
    localparam int          HDR_LEN    = 12;
    localparam int          REC_LEN    = 24;
    localparam int          PAY_LEN    = HDR_LEN + REC_LEN;   // count = 1
    localparam logic [15:0] MAGIC      = 16'h4F42;            // offset 0
    localparam logic [7:0]  VERSION    = 8'd1;                // offset 2
    localparam logic [7:0]  TYPE_CFG   = 8'h10;               // offset 3
    localparam int          OFF_VER    = 2;
    localparam int          OFF_TYPE   = 3;
    localparam int          OFF_COUNT  = 8;                   // u16, must be 1
    localparam int          OFF_RLEN   = 10;                  // u16, must be 24
    localparam int          OFF_MODE   = HDR_LEN + 6;         // record offset 6, 0..2
    localparam int          OFF_LV     = HDR_LEN + 7;         // record offset 7, 0/1
    localparam int          IDX_W      = 6;                   // counts past PAY_LEN

    typedef enum logic [2:0] {S_OFF, S_RST, S_CLEAR, S_ACK, S_RUN} state_t;

    state_t             state_q;
    logic [IDX_W-1:0]   idx_q;
    logic               bad_q, bad_d, beat, accept, load;
    logic [REC_LEN*8-1:0] shift_q, shift_d, next_q, cfg_q;
    logic [95:0]        next_src_q, dst_q;
    logic               pend_q;

    assign s_tready_o = 1'b1;
    assign beat       = s_tvalid_i;

    // one byte's check against the header / record rules
    always_comb begin
        bad_d = bad_q || idx_q >= IDX_W'(PAY_LEN);
        case (idx_q)
            IDX_W'(0):             bad_d = bad_d || s_tdata_i != MAGIC[15:8];
            IDX_W'(1):             bad_d = bad_d || s_tdata_i != MAGIC[7:0];
            IDX_W'(OFF_VER):       bad_d = bad_d || s_tdata_i != VERSION;
            IDX_W'(OFF_TYPE):      bad_d = bad_d || s_tdata_i != TYPE_CFG;
            IDX_W'(OFF_COUNT):     bad_d = bad_d || s_tdata_i != 8'd0;
            IDX_W'(OFF_COUNT + 1): bad_d = bad_d || s_tdata_i != 8'd1;
            IDX_W'(OFF_RLEN):      bad_d = bad_d || s_tdata_i != 8'd0;
            IDX_W'(OFF_RLEN + 1):  bad_d = bad_d || s_tdata_i != 8'(REC_LEN);
            IDX_W'(OFF_MODE):      bad_d = bad_d || s_tdata_i > 8'd2;
            IDX_W'(OFF_LV):        bad_d = bad_d || s_tdata_i > 8'd1;
            default: ;
        endcase
        shift_d = idx_q >= IDX_W'(HDR_LEN) ? {shift_q[REC_LEN*8-9:0], s_tdata_i} : shift_q;
    end

    assign accept = beat && s_tlast_i && !bad_d && !s_tuser_i
                    && idx_q == IDX_W'(PAY_LEN - 1);
    assign load   = pend_q && (state_q == S_OFF || state_q == S_RUN);

    // parser
    always_ff @(posedge clk_i) begin
        ctrl_ok_o  <= 1'b0;
        ctrl_bad_o <= 1'b0;
        if (beat) begin
            shift_q <= shift_d;
            if (s_tlast_i) begin
                idx_q      <= '0;
                bad_q      <= 1'b0;
                ctrl_ok_o  <= accept;
                ctrl_bad_o <= !accept;
            end else begin
                idx_q <= idx_q == '1 ? idx_q : idx_q + 1'b1;
                bad_q <= bad_d;
            end
        end
        if (accept) begin
            next_q     <= shift_d;
            next_src_q <= {src_mac_i, src_ip_i, src_port_i};
        end
        if (rst_i) begin
            idx_q      <= '0;
            bad_q      <= 1'b0;
            ctrl_ok_o  <= 1'b0;
            ctrl_bad_o <= 1'b0;
        end
    end

    // sequence
    always_ff @(posedge clk_i) begin
        if (accept) pend_q <= 1'b1;
        else if (load) pend_q <= 1'b0;
        if (load) begin
            cfg_q <= next_q;
            dst_q <= next_src_q;
        end
        case (state_q)
            S_OFF, S_RUN: if (load) state_q <= S_RST;
            S_RST:        state_q <= S_CLEAR;
            S_CLEAR:      if (!book_busy_i) state_q <= S_ACK;
            S_ACK:        if (flush_i) state_q <= S_RUN;
            default:      state_q <= S_OFF;
        endcase
        if (rst_i) begin
            state_q <= S_OFF;
            pend_q  <= 1'b0;
        end
    end

    assign feed_rst_o    = state_q != S_RUN;
    assign book_rst_o    = state_q == S_OFF || state_q == S_RST;
    assign stats_clear_o = state_q == S_RST;
    assign ack_req_o     = state_q == S_CLEAR && !book_busy_i;
    assign ack_rec_o     = cfg_q;

    // config record: group(4) port(2) mode(1) locate_valid(1) symbol(8) lo_price(4)
    //   locate(2) pad(2)
    assign cfg_group_o        = cfg_q[191:160];
    assign cfg_port_o         = cfg_q[159:144];
    assign cfg_mode_o         = cfg_q[136];
    assign cfg_inject_o       = cfg_q[137];
    assign cfg_locate_valid_o = cfg_q[128];
    assign cfg_symbol_o       = cfg_q[127:64];
    assign cfg_lo_price_o     = cfg_q[63:32];
    assign cfg_locate_o       = cfg_q[31:16];
    assign {dst_mac_o, dst_ip_o, dst_port_o} = dst_q;

    // the unused record bits: mode and locate_valid high bits (checked 0), pad
    logic unused_cfg;
    assign unused_cfg = ^{cfg_q[143:138], cfg_q[135:129], cfg_q[15:0]};

`ifndef SYNTHESIS
    // out_builder contract: ack_rec_o stable from ack_req_o until flush_i
    always_ff @(posedge clk_i) begin
        if (!rst_i && state_q == S_ACK) assert ($stable(cfg_q));
    end
`endif
endmodule
`default_nettype wire
