`default_nettype none
// arty_top: the order-book pipeline on the Digilent Arty A7-100T. Board clocking, reset
// sequencing and status LEDs around ob_core (which holds the whole datapath).
//
// Start-up (as fpga-eth-core's examples): the MMCM locks; the PHY reset (eth_rstn) is
// released at PHY_RST_CYCLES, the MAC (all three domains) at MAC_START_CYCLES, because a
// domain whose clock is absent cannot leave reset and the PHY's clocks start only after
// its reset. ck_rst (button, active low) restarts the sequence.
// Then nothing is sent until the host's first config (docs/protocol.md).
//
// LEDs: led[0] 1 Hz heartbeat (core clock running, MAC out of reset); led[1] running (a
// config was acked); led[2] stale or a sticky error (frame, FIFO, TX); led[3] datagram
// activity (stretched to ~33 ms).
// UART: idle (the stats travel as UDP datagrams); MDIO unused, eth_mdc held low.
// Pmod JA (scope cross-check, buildplan 6.5): ja[0] rises 1 cycle after each T1, ja[1] 1
// cycle after each T2 (ob_core's pulses), each held PULSE_CYCLES; both leave through IOB
// registers (constr/arty_pmod.xdc), so their rising edges are T2 - T1 apart plus the pin-
// to-pin skew. An event inside the previous pulse extends it (no new edge).
// Top-level ports use the Digilent master-XDC names.
module arty_top #(
    parameter real         CORE_DIV         = 8.0,                 // 1000/8 = 125 MHz
    parameter int          CLK_HZ           = 125_000_000,         // = 1e9 / CORE_DIV
    parameter int          PHY_RST_CYCLES   = CLK_HZ / 100,        // 10 ms
    parameter int          MAC_START_CYCLES = CLK_HZ / 5,          // 200 ms
    parameter logic [47:0] OWN_MAC          = 48'h02_00_0A_0B_0C_0D,
    parameter logic [31:0] OWN_IP           = 32'hC0_A8_0A_02,    // 192.168.10.2
    parameter logic [15:0] CTRL_PORT        = 16'd7000,
    parameter int          PULSE_CYCLES     = 4,                   // 32 ns at 125 MHz
    parameter bit          CUT_THROUGH      = 1'b0                 // eth_rx mode (ADR 0013)
) (
    input  wire logic       CLK100MHZ,
    input  wire logic       ck_rst,        // active low
    output logic [3:0]      led,
    output logic            uart_rxd_out,  // FPGA -> host, idle
    // Ethernet PHY (TI DP83848J, MII)
    output logic            eth_ref_clk,
    output logic            eth_rstn,
    input  wire logic       eth_rx_clk,
    input  wire logic       eth_rx_dv,
    input  wire logic [3:0] eth_rxd,
    input  wire logic       eth_rxerr,
    input  wire logic       eth_tx_clk,
    output logic            eth_tx_en,
    output logic [3:0]      eth_txd,
    output logic            eth_mdc,
    output logic [1:0]      ja             // Pmod JA1 = T1, JA2 = T2
);
    localparam int START_W  = $clog2(MAC_START_CYCLES + 1);
    localparam int BEAT_W   = $clog2(CLK_HZ / 2);
    localparam int LED_HOLD = CLK_HZ / 30;  // ~33 ms activity flash
    localparam int LED_W    = $clog2(LED_HOLD + 1);

    // ------------------------------------------------------- clocks and resets
    logic clk, locked, rst;

    clk_gen #(
        .CORE_DIV (CORE_DIV)
    ) u_clk (
        .clk100_i      (CLK100MHZ),
        .clk_core_o    (clk),
        .eth_ref_clk_o (eth_ref_clk),
        .locked_o      (locked)
    );

    cdc_reset_sync u_rst_core (
        .clk_i     (clk),
        .rst_req_i (!ck_rst || !locked),
        .rst_o     (rst)
    );

    logic [START_W-1:0] start_q;
    logic               mac_rst_q, mii_rx_rst, mii_tx_rst;

    always_ff @(posedge clk) begin
        if (rst) begin
            start_q   <= '0;
            eth_rstn  <= 1'b0;
            mac_rst_q <= 1'b1;
        end else begin
            if (start_q != START_W'(MAC_START_CYCLES)) start_q <= start_q + START_W'(1);
            eth_rstn  <= start_q >= START_W'(PHY_RST_CYCLES);
            mac_rst_q <= start_q != START_W'(MAC_START_CYCLES);
        end
    end

    cdc_reset_sync u_rst_mii_rx (
        .clk_i     (eth_rx_clk),
        .rst_req_i (mac_rst_q),
        .rst_o     (mii_rx_rst)
    );

    cdc_reset_sync u_rst_mii_tx (
        .clk_i     (eth_tx_clk),
        .rst_req_i (mac_rst_q),
        .rst_o     (mii_tx_rst)
    );

    // ------------------------------------------------------------- pipeline
    logic running, stale, sent, error, sfd_unused, t1_pulse, t2_pulse;

    ob_core #(
        .OWN_MAC     (OWN_MAC),
        .OWN_IP      (OWN_IP),
        .CTRL_PORT   (CTRL_PORT),
        .TICK_CYCLES (CLK_HZ),
        .CUT_THROUGH (CUT_THROUGH)
    ) u_core (
        .clk_i        (clk),
        .rst_i        (mac_rst_q),
        .mii_rx_clk_i (eth_rx_clk),
        .mii_rx_rst_i (mii_rx_rst),
        .mii_rxd_i    (eth_rxd),
        .mii_rx_dv_i  (eth_rx_dv),
        .mii_rx_er_i  (eth_rxerr),
        .sfd_o        (sfd_unused),
        .mii_tx_clk_i (eth_tx_clk),
        .mii_tx_rst_i (mii_tx_rst),
        .mii_txd_o    (eth_txd),
        .mii_tx_en_o  (eth_tx_en),
        .running_o    (running),
        .stale_o      (stale),
        .sent_o       (sent),
        .error_o      (error),
        .t1_pulse_o   (t1_pulse),
        .t2_pulse_o   (t2_pulse)
    );

    // ------------------------------------------------------------ Pmod pulses
    localparam int PULSE_W = $clog2(PULSE_CYCLES + 1);
    logic [PULSE_W-1:0] t1_left_q, t2_left_q;

    always_ff @(posedge clk) begin
        if (mac_rst_q) begin
            t1_left_q <= '0;
            t2_left_q <= '0;
            ja        <= '0;
        end else begin
            if (t1_pulse) t1_left_q <= PULSE_W'(PULSE_CYCLES - 1);
            else if (t1_left_q != '0) t1_left_q <= t1_left_q - PULSE_W'(1);
            if (t2_pulse) t2_left_q <= PULSE_W'(PULSE_CYCLES - 1);
            else if (t2_left_q != '0) t2_left_q <= t2_left_q - PULSE_W'(1);
            ja[0] <= t1_pulse || t1_left_q != '0;  // output registers, packed into the IOBs
            ja[1] <= t2_pulse || t2_left_q != '0;
        end
    end

    // ----------------------------------------------------------------- LEDs
    logic [BEAT_W-1:0] beat_q;
    logic              beat_led_q;
    logic [LED_W-1:0]  hold_q;

    always_ff @(posedge clk) begin
        if (mac_rst_q) begin
            beat_q     <= '0;
            beat_led_q <= 1'b0;
            hold_q     <= '0;
        end else begin
            if (beat_q == BEAT_W'(CLK_HZ / 2 - 1)) begin
                beat_q     <= '0;
                beat_led_q <= !beat_led_q;
            end else begin
                beat_q <= beat_q + BEAT_W'(1);
            end
            if (sent) hold_q <= LED_W'(LED_HOLD);
            else if (hold_q != '0) hold_q <= hold_q - LED_W'(1);
        end
    end

    always_comb begin
        led[0] = beat_led_q;
        led[1] = running;
        led[2] = stale || error;
        led[3] = hold_q != '0;
    end
    assign uart_rxd_out = 1'b1;
    assign eth_mdc      = 1'b0;

    logic unused;
    assign unused = sfd_unused;
endmodule
`default_nettype wire
