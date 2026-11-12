`default_nettype none
// ob_core: the whole pipeline from MII receive pins to MII transmit pins, without the
// board's clocking and reset sequencing (arty_top). Simulated end to end by
// tb/cocotb/test_core.py against model.protocol.expected().
//
//   MII RX -> eth_rx -+-> udp_rx (feed: cfg group:port) -> pkt_gate -> feed_frontend
//                     |                                                   |
//                     |                         mode 0: book_engine <-----+
//                     |                                    |              | mode 1
//                     |                                 out_mux <---------+
//                     |                                    |
//                     +-> udp_rx (ctrl: OWN_IP:CTRL_PORT) -> out_ctrl -> out_builder
//                                                     out_stats ----------^   |
//   MII TX <- eth_tx <- udp_tx (to the last config's sender, from CTRL_PORT) <-+
//
// eth_rx's frames go to both udp_rx at once (each beat when both are ready; udp_rx's ready
// does not depend on its valid). The control path never stalls (out_ctrl is always
// ready); the feed path stalls only through the decoder and book.
// Config sequence and resets (docs/protocol.md Config, out_ctrl): until the first config
// the feed decoder and the book are in reset and nothing is sent. The feed decoder's
// reset (feed_rst) also blocks pkt_gate, which drains the feed udp_rx meanwhile and
// passes only whole datagrams. The eth-core blocks and the output side run from rst_i.
// Stats: docs/protocol.md table; sources as listed there. The feed udp_rx counts every
// frame (control frames show up in feed_filtered).
// Clocks: clk_i (core), mii_rx_clk_i, mii_tx_clk_i (PHY); resets synchronous, active-high,
// one per domain from a common source (eth-core requirement).
// Timestamps (ADR 0011): now_q counts core cycles from reset. T0 = now_q at the frame's
// synchronized SFD, matched to the frame by t0_track; T1 = now_q when the decoder takes
// the message's last byte; T2 = now_q on the first cycle a BBO is valid at the book's
// output. T0 and T1 ride the book's per-record sideband with the feed stale bit.
// Latency: the sum of the blocks' (docs/latency.md).
module ob_core #(
    parameter logic [47:0] OWN_MAC     = 48'h02_00_0A_0B_0C_0D,
    parameter logic [31:0] OWN_IP      = 32'hC0_A8_0A_02,  // 192.168.10.2
    parameter logic [15:0] CTRL_PORT   = 16'd7000,
    parameter int          TICK_CYCLES = 125_000_000,      // stats period: 1 s of core clock
    parameter int          SET_BITS    = 11,               // book geometry (ADR 0001, 0010)
    parameter int          WAYS        = 16,
    parameter int          SHARES_W    = 20,
    parameter int          WIN_BITS    = 12                // price window (ADR 0002)
) (
    input  wire logic       clk_i,
    input  wire logic       rst_i,
    // MII receive (rx_clk domain)
    input  wire logic       mii_rx_clk_i,
    input  wire logic       mii_rx_rst_i,
    input  wire logic [3:0] mii_rxd_i,
    input  wire logic       mii_rx_dv_i,
    input  wire logic       mii_rx_er_i,
    output logic            sfd_o,          // T0 event, rx_clk domain (Phase E)
    // MII transmit (tx_clk domain)
    input  wire logic       mii_tx_clk_i,
    input  wire logic       mii_tx_rst_i,
    output logic [3:0]      mii_txd_o,
    output logic            mii_tx_en_o,
    // board status (core domain)
    output logic            running_o,      // a config has been acked; the feed runs
    output logic            stale_o,        // feed or book stale
    output logic            sent_o,         // a datagram left udp_tx (pulse)
    output logic            error_o,        // sticky: frame/FIFO/TX errors since reset
    // scope cross-check (buildplan 6.5): one-cycle pulses, each 1 cycle after its event,
    // so pulse-to-pulse equals T2 - T1. T1 pulses for every decoded message.
    output logic            t1_pulse_o,
    output logic            t2_pulse_o
);
    localparam int LEVEL_W  = SHARES_W + SET_BITS + 1 + $clog2(WAYS);  // book_engine's
    localparam int N_STATS  = 41;
    localparam int USER_W   = 1 + 64 + 64;  // {feed stale, T0, T1} through the book

    logic [63:0] now_q;
    always_ff @(posedge clk_i) begin
        if (rst_i) now_q <= '0;
        else       now_q <= now_q + 64'd1;
    end

    // --------------------------------------------------------------- receive
    logic [7:0] f_tdata;
    logic       f_tvalid, f_tready, f_tlast;
    logic       e_ok, e_crc, e_rxerr, e_runt, e_over, e_ovf;
    logic [7:0] e_ovf_frames;
    logic [11:0] fifo_level_unused;

    eth_rx u_eth_rx (
        .mii_clk_i    (mii_rx_clk_i),
        .mii_rst_i    (mii_rx_rst_i),
        .mii_rxd_i    (mii_rxd_i),
        .mii_rx_dv_i  (mii_rx_dv_i),
        .mii_rx_er_i  (mii_rx_er_i),
        .sfd_o        (sfd_o),
        .clk_i        (clk_i),
        .rst_i        (rst_i),
        .m_tdata_o    (f_tdata),
        .m_tvalid_o   (f_tvalid),
        .m_tready_i   (f_tready),
        .m_tlast_o    (f_tlast),
        .frame_ok_o   (e_ok),
        .crc_err_o    (e_crc),
        .rx_err_o     (e_rxerr),
        .runt_o       (e_runt),
        .oversize_o   (e_over),
        .ovf_o        (e_ovf),
        .ovf_frames_o (e_ovf_frames),
        .fifo_level_o (fifo_level_unused)
    );

    // T0 (ADR 0011): the SFD into the core domain, then matched to the frame as it
    // leaves eth_rx. t0 is the frame's T0 (0 if lost) until the next frame starts.
    logic        sfd_core, f_first_q, t0_lost, t0_valid_unused;
    logic [63:0] t0;

    cdc_pulse u_sfd_sync (
        .src_clk_i   (mii_rx_clk_i),
        .src_rst_i   (mii_rx_rst_i),
        .src_pulse_i (sfd_o),
        .dst_clk_i   (clk_i),
        .dst_rst_i   (rst_i),
        .dst_pulse_o (sfd_core)
    );

    always_ff @(posedge clk_i) begin
        if (rst_i)                      f_first_q <= 1'b1;
        else if (f_tvalid && f_tready) f_first_q <= f_tlast;
    end

    t0_track u_t0 (
        .clk_i         (clk_i),
        .rst_i         (rst_i),
        .now_i         (now_q),
        .sfd_i         (sfd_core),
        .ok_i          (e_ok),
        .drop_i        (e_crc || e_rxerr || e_runt || e_over),
        .ovf_i         (e_ovf),
        .ovf_frames_i  (e_ovf_frames),
        .frame_start_i (f_tvalid && f_tready && f_first_q),
        .t0_o          (t0),
        .t0_valid_o    (t0_valid_unused),
        .lost_o        (t0_lost)
    );

    // broadcast to both udp_rx: a beat moves when both take it. No combinational loop:
    // udp_rx's s_tready_o depends only on its output side. (Verilator's UNOPTFLAT note
    // here is a false loop: udp_rx computes s_tready_o in the same always_comb as fire.)
    logic fd_ready, ct_ready, fd_valid, ct_valid;
    assign f_tready = fd_ready && ct_ready;
    assign ct_valid = f_tvalid && fd_ready;
    assign fd_valid = f_tvalid && ct_ready;

`ifndef SYNTHESIS
    // broadcast: a beat either udp_rx takes is a beat taken from eth_rx (no duplicates)
    always_ff @(posedge clk_i) begin
        if (!rst_i) begin
            assert (!(ct_valid && ct_ready) || (f_tvalid && f_tready));
            assert (!(fd_valid && fd_ready) || (f_tvalid && f_tready));
        end
    end
`endif

    // control port
    logic [7:0]  c_tdata;
    logic        c_tvalid, c_tready, c_tlast, c_tuser;
    logic [47:0] c_mac;
    logic [31:0] c_ip;
    logic [15:0] c_port, c_dport_unused;
    logic        c_ok_unused, c_filt_unused, c_csum_unused, c_mal_unused;

    udp_rx u_udp_ctrl (
        .clk_i         (clk_i),
        .rst_i         (rst_i),
        .cfg_mac_i     (OWN_MAC),
        .cfg_ip_i      (OWN_IP),
        .cfg_port_i    (CTRL_PORT),
        .s_tdata_i     (f_tdata),
        .s_tvalid_i    (ct_valid),
        .s_tready_o    (ct_ready),
        .s_tlast_i     (f_tlast),
        .m_tdata_o     (c_tdata),
        .m_tvalid_o    (c_tvalid),
        .m_tready_i    (c_tready),
        .m_tlast_o     (c_tlast),
        .m_tuser_o     (c_tuser),
        .src_mac_o     (c_mac),
        .src_ip_o      (c_ip),
        .src_port_o    (c_port),
        .dst_port_o    (c_dport_unused),
        .ok_o          (c_ok_unused),
        .filtered_o    (c_filt_unused),
        .ip_csum_err_o (c_csum_unused),
        .malformed_o   (c_mal_unused)
    );

    // configuration and sequence
    logic [31:0]          cfg_group, cfg_lo_price;
    logic [15:0]          cfg_port, cfg_locate;
    logic                 cfg_mode, cfg_locate_valid;
    logic [63:0]          cfg_symbol;
    logic [47:0]          dst_mac;
    logic [31:0]          dst_ip;
    logic [15:0]          dst_port;
    logic                 feed_rst, book_rst, stats_clear, book_busy, ack_req, flush;
    logic [24*8-1:0]      ack_rec;
    logic                 ctrl_ok, ctrl_bad;

    out_ctrl u_ctrl (
        .clk_i              (clk_i),
        .rst_i              (rst_i),
        .s_tdata_i          (c_tdata),
        .s_tvalid_i         (c_tvalid),
        .s_tready_o         (c_tready),
        .s_tlast_i          (c_tlast),
        .s_tuser_i          (c_tuser),
        .src_mac_i          (c_mac),
        .src_ip_i           (c_ip),
        .src_port_i         (c_port),
        .cfg_group_o        (cfg_group),
        .cfg_port_o         (cfg_port),
        .cfg_mode_o         (cfg_mode),
        .cfg_locate_valid_o (cfg_locate_valid),
        .cfg_symbol_o       (cfg_symbol),
        .cfg_lo_price_o     (cfg_lo_price),
        .cfg_locate_o       (cfg_locate),
        .dst_mac_o          (dst_mac),
        .dst_ip_o           (dst_ip),
        .dst_port_o         (dst_port),
        .feed_rst_o         (feed_rst),
        .book_rst_o         (book_rst),
        .stats_clear_o      (stats_clear),
        .book_busy_i        (book_busy),
        .ack_req_o          (ack_req),
        .ack_rec_o          (ack_rec),
        .flush_i            (flush),
        .ctrl_ok_o          (ctrl_ok),
        .ctrl_bad_o         (ctrl_bad)
    );

    // feed
    logic [7:0]  u_tdata, g_tdata;
    logic        u_tvalid, u_tready, u_tlast, u_tuser;
    logic        g_tvalid, g_tready, g_tlast, g_tuser;
    logic [47:0] u_mac_unused;
    logic [31:0] u_ip_unused;
    logic [15:0] u_sport_unused, u_dport_unused;
    logic        u_ok, u_filt, u_csum, u_mal;

    udp_rx u_udp_feed (
        .clk_i         (clk_i),
        .rst_i         (rst_i),
        .cfg_mac_i     (OWN_MAC),
        .cfg_ip_i      (cfg_group),
        .cfg_port_i    (cfg_port),
        .s_tdata_i     (f_tdata),
        .s_tvalid_i    (fd_valid),
        .s_tready_o    (fd_ready),
        .s_tlast_i     (f_tlast),
        .m_tdata_o     (u_tdata),
        .m_tvalid_o    (u_tvalid),
        .m_tready_i    (u_tready),
        .m_tlast_o     (u_tlast),
        .m_tuser_o     (u_tuser),
        .src_mac_o     (u_mac_unused),
        .src_ip_o      (u_ip_unused),
        .src_port_o    (u_sport_unused),
        .dst_port_o    (u_dport_unused),
        .ok_o          (u_ok),
        .filtered_o    (u_filt),
        .ip_csum_err_o (u_csum),
        .malformed_o   (u_mal)
    );

    pkt_gate #(
        .DATA_W (8)
    ) u_gate (
        .clk_i      (clk_i),
        .rst_i      (rst_i),
        .block_i    (feed_rst),
        .s_tdata_i  (u_tdata),
        .s_tvalid_i (u_tvalid),
        .s_tready_o (u_tready),
        .s_tlast_i  (u_tlast),
        .s_tuser_i  (u_tuser),
        .m_tdata_o  (g_tdata),
        .m_tvalid_o (g_tvalid),
        .m_tready_i (g_tready),
        .m_tlast_o  (g_tlast),
        .m_tuser_o  (g_tuser)
    );

    logic        r_valid, r_ready, r_sell;
    logic [7:0]  r_type;
    logic [63:0] r_seq, r_ref, r_new_ref, gap_msgs, r_t0, r_t1;
    logic [47:0] r_ts;
    logic [31:0] r_shares, r_price;
    logic        m_pkt, m_hb, m_end, m_gap, m_dup, m_sess, m_mal_pkt, m_empty;
    logic        i_unknown, i_mal, i_other, i_dir, i_loc_chg, loc_valid, feed_stale;
    logic [15:0] locate;

    feed_frontend #(
        .DATA_W (8)
    ) u_feed (
        .clk_i              (clk_i),
        .rst_i              (feed_rst),
        .cfg_symbol_i       (cfg_symbol),
        .cfg_locate_valid_i (cfg_locate_valid),
        .cfg_locate_i       (cfg_locate),
        .s_tdata_i          (g_tdata),
        .s_tvalid_i         (g_tvalid),
        .s_tready_o         (g_tready),
        .s_tlast_i          (g_tlast),
        .s_tuser_i          (g_tuser),
        .s_t0_i             (t0),  // the frame inside udp_rx: its payload's T0 (ADR 0011)
        .now_i              (now_q),
        .m_valid_o          (r_valid),
        .m_ready_i          (r_ready),
        .m_type_o           (r_type),
        .m_seq_o            (r_seq),
        .m_ts_o             (r_ts),
        .m_ref_o            (r_ref),
        .m_new_ref_o        (r_new_ref),
        .m_sell_o           (r_sell),
        .m_shares_o         (r_shares),
        .m_price_o          (r_price),
        .m_t0_o             (r_t0),
        .m_t1_o             (r_t1),
        .t1_o               (t1_pulse_o),   // registered: T1 + 1
        .pkt_o              (m_pkt),
        .heartbeat_o        (m_hb),
        .end_o              (m_end),
        .gap_o              (m_gap),
        .gap_msgs_o         (gap_msgs),
        .dup_o              (m_dup),
        .session_change_o   (m_sess),
        .malformed_pkt_o    (m_mal_pkt),
        .empty_msg_o        (m_empty),
        .unknown_o          (i_unknown),
        .malformed_msg_o    (i_mal),
        .other_locate_o     (i_other),
        .dir_match_o        (i_dir),
        .locate_change_o    (i_loc_chg),
        .locate_valid_o     (loc_valid),
        .locate_o           (locate),
        .stale_o            (feed_stale)
    );

    // --------------------------------------------------------------- book
    logic               b_s_valid, b_s_ready, b_m_valid, b_m_ready, b_oow, b_fstale, b_stale;
    logic [63:0]        b_t0, b_t1, b_t2, b_t2_q;
    logic               b_hold_q, b_new;
    logic [31:0]        b_bid_px, b_ask_px;
    logic [LEVEL_W-1:0] b_bid_sz, b_ask_sz;
    logic [63:0]        b_seq;
    logic [47:0]        b_ts;
    logic               k_ref_ovf, k_dup, k_sh_ovf, k_tab_ovf, k_unknown, k_over;
    logic               k_oow, k_subpenny, k_bbo_oow, book_stale;

    book_engine #(
        .SET_BITS (SET_BITS),
        .WAYS     (WAYS),
        .SHARES_W (SHARES_W),
        .WIN_BITS (WIN_BITS),
        .USER_W   (USER_W)
    ) u_book (
        .clk_i             (clk_i),
        .rst_i             (book_rst),
        .cfg_lo_price_i    (cfg_lo_price),
        .s_valid_i         (b_s_valid),
        .s_ready_o         (b_s_ready),
        .s_type_i          (r_type),
        .s_seq_i           (r_seq),
        .s_ts_i            (r_ts),
        .s_user_i          ({feed_stale, r_t0, r_t1}),  // ride to the record's BBO
        .s_ref_i           (r_ref),
        .s_new_ref_i       (r_new_ref),
        .s_sell_i          (r_sell),
        .s_shares_i        (r_shares),
        .s_price_i         (r_price),
        .m_valid_o         (b_m_valid),
        .m_ready_i         (b_m_ready),
        .m_bid_px_o        (b_bid_px),
        .m_bid_sz_o        (b_bid_sz),
        .m_ask_px_o        (b_ask_px),
        .m_ask_sz_o        (b_ask_sz),
        .m_oow_o           (b_oow),
        .m_seq_o           (b_seq),
        .m_ts_o            (b_ts),
        .m_user_o          ({b_fstale, b_t0, b_t1}),
        .m_stale_o         (b_stale),
        .ref_overflow_o    (k_ref_ovf),
        .duplicate_ref_o   (k_dup),
        .shares_overflow_o (k_sh_ovf),
        .table_overflow_o  (k_tab_ovf),
        .unknown_ref_o     (k_unknown),
        .over_reduce_o     (k_over),
        .price_oow_o       (k_oow),
        .price_subpenny_o  (k_subpenny),
        .bbo_oow_o         (k_bbo_oow),
        .stale_o           (book_stale),
        .busy_o            (book_busy)
    );

    // T2: the first cycle a BBO is valid (held while out_builder stalls it).
    always_ff @(posedge clk_i) begin
        if (rst_i) b_hold_q <= 1'b0;
        else       b_hold_q <= b_m_valid && !b_m_ready;
        if (b_new) b_t2_q <= now_q;
        t2_pulse_o <= b_new && !rst_i;  // T2 + 1, as t1_pulse_o is T1 + 1
    end
    assign b_new = b_m_valid && !b_hold_q;
    assign b_t2  = b_new ? now_q : b_t2_q;

    // --------------------------------------------------------------- output
    logic           o_valid, o_ready;
    logic [64*8-1:0] o_rec;

    out_mux #(
        .LEVEL_W (LEVEL_W)
    ) u_mux (
        .mode_i         (cfg_mode),
        .f_valid_i      (r_valid),
        .f_ready_o      (r_ready),
        .f_type_i       (r_type),
        .f_seq_i        (r_seq),
        .f_ts_i         (r_ts),
        .f_ref_i        (r_ref),
        .f_new_ref_i    (r_new_ref),
        .f_sell_i       (r_sell),
        .f_shares_i     (r_shares),
        .f_price_i      (r_price),
        .b_s_valid_o    (b_s_valid),
        .b_s_ready_i    (b_s_ready),
        .b_m_valid_i    (b_m_valid),
        .b_m_ready_o    (b_m_ready),
        .b_bid_px_i     (b_bid_px),
        .b_bid_sz_i     (b_bid_sz),
        .b_ask_px_i     (b_ask_px),
        .b_ask_sz_i     (b_ask_sz),
        .b_oow_i        (b_oow),
        .b_seq_i        (b_seq),
        .b_ts_i         (b_ts),
        .b_feed_stale_i (b_fstale),
        .b_book_stale_i (b_stale),
        .b_t0_i         (b_t0),
        .b_t1_i         (b_t1),
        .b_t2_i         (b_t2),
        .o_valid_o      (o_valid),
        .o_ready_i      (o_ready),
        .o_rec_o        (o_rec)
    );

    logic [N_STATS*64-1:0] stats;
    logic                  tick, dgram;
    logic [5:0]            dgram_recs;

    out_stats #(
        .TICK_CYCLES (TICK_CYCLES)
    ) u_stats (
        .clk_i                     (clk_i),
        .rst_i                     (rst_i),
        .clear_i                   (stats_clear),
        .rx_frames_ok_i            (e_ok),
        .rx_crc_err_i              (e_crc),
        .rx_err_i                  (e_rxerr),
        .rx_runt_i                 (e_runt),
        .rx_oversize_i             (e_over),
        .rx_fifo_overflow_i        (e_ovf ? e_ovf_frames : 8'd0),
        .feed_datagrams_i          (u_ok),
        .feed_filtered_i           (u_filt),
        .ip_csum_err_i             (u_csum),
        .udp_malformed_i           (u_mal),
        .mold_packets_i            (m_pkt),
        .mold_heartbeats_i         (m_hb),
        .mold_end_of_session_i     (m_end),
        .mold_gaps_i               (m_gap),
        .mold_gap_messages_i       (gap_msgs),
        .mold_duplicates_i         (m_dup),
        .mold_session_changes_i    (m_sess),
        .mold_malformed_packets_i  (m_mal_pkt),
        .itch_unknown_types_i      (i_unknown),
        .itch_malformed_messages_i (2'(i_mal) + 2'(m_empty)),
        .itch_other_locate_i       (i_other),
        .itch_directory_matches_i  (i_dir),
        .itch_locate_changes_i     (i_loc_chg),
        .locate_valid_i            (loc_valid),
        .locate_i                  (locate),
        .book_records_i            (r_valid && r_ready),
        .ref_overflow_i            (k_ref_ovf),
        .duplicate_ref_i           (k_dup),
        .shares_overflow_i         (k_sh_ovf),
        .table_overflow_i          (k_tab_ovf),
        .unknown_ref_i             (k_unknown),
        .over_reduce_i             (k_over),
        .price_oow_i               (k_oow),
        .price_subpenny_i          (k_subpenny),
        .bbo_oow_i                 (k_bbo_oow),
        .feed_stale_i              (feed_stale),
        .book_stale_i              (book_stale),
        .out_records_i             (dgram ? dgram_recs : 6'd0),
        .out_datagrams_i           (dgram),
        .ctrl_ok_i                 (ctrl_ok),
        .ctrl_bad_i                (ctrl_bad),
        .t0_lost_i                 (t0_lost),
        .stats_o                   (stats),
        .tick_o                    (tick)
    );

    logic [7:0] p_tdata, t_tdata;
    logic       p_tvalid, p_tready, p_tlast, t_tvalid, t_tready, t_tlast;
    logic       t_over, t_underrun;

    out_builder #(
        .N_STATS (N_STATS)
    ) u_builder (
        .clk_i        (clk_i),
        .rst_i        (rst_i),
        .mode_i       (cfg_mode),
        .s_valid_i    (o_valid),
        .s_ready_o    (o_ready),
        .s_rec_i      (o_rec),
        .stats_req_i  (tick),
        .stats_i      (stats),
        .ack_req_i    (ack_req),
        .ack_rec_i    (ack_rec),
        .m_tdata_o    (p_tdata),
        .m_tvalid_o   (p_tvalid),
        .m_tready_i   (p_tready),
        .m_tlast_o    (p_tlast),
        .dgram_o      (dgram),
        .dgram_recs_o (dgram_recs),
        .flush_o      (flush)
    );

    udp_tx u_udp_tx (
        .clk_i      (clk_i),
        .rst_i      (rst_i),
        .cfg_mac_i  (OWN_MAC),
        .cfg_ip_i   (OWN_IP),
        .s_tdata_i  (p_tdata),
        .s_tvalid_i (p_tvalid),
        .s_tready_o (p_tready),
        .s_tlast_i  (p_tlast),
        .s_tuser_i  (1'b0),
        .dst_mac_i  (dst_mac),
        .dst_ip_i   (dst_ip),
        .dst_port_i (dst_port),
        .src_port_i (CTRL_PORT),
        .m_tdata_o  (t_tdata),
        .m_tvalid_o (t_tvalid),
        .m_tready_i (t_tready),
        .m_tlast_o  (t_tlast),
        .sent_o     (sent_o),
        .oversize_o (t_over)
    );

    eth_tx u_eth_tx (
        .clk_i       (clk_i),
        .rst_i       (rst_i),
        .s_tdata_i   (t_tdata),
        .s_tvalid_i  (t_tvalid),
        .s_tready_o  (t_tready),
        .s_tlast_i   (t_tlast),
        .underrun_o  (t_underrun),
        .mii_clk_i   (mii_tx_clk_i),
        .mii_rst_i   (mii_tx_rst_i),
        .mii_txd_o   (mii_txd_o),
        .mii_tx_en_o (mii_tx_en_o)
    );

    // --------------------------------------------------------------- board status
    always_ff @(posedge clk_i) begin
        if (e_crc || e_rxerr || e_ovf || u_csum || u_mal || t_over || t_underrun)
            error_o <= 1'b1;
        if (rst_i) error_o <= 1'b0;
    end
    assign running_o = !feed_rst;
    assign stale_o   = feed_stale || book_stale;

    logic unused;
    assign unused = ^{fifo_level_unused, c_dport_unused, c_ok_unused, c_filt_unused,
                      c_csum_unused, c_mal_unused, u_mac_unused, u_ip_unused,
                      u_sport_unused, u_dport_unused, t0_valid_unused};
endmodule
`default_nettype wire
