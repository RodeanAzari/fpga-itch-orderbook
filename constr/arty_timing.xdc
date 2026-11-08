# Timing constraints for rtl/top/arty_top on the Arty A7-100T. Pins: fpga-eth-core's
# constr/arty_a7_100_pins.xdc + arty_a7_100_pins_tx.xdc (from Digilent's master XDC, read
# from the submodule at its tag); async FIFO pointer CDC: its async_fifo.xdc, read scoped
# to async_fifo. Structure and datasheet numbers follow eth-core's udp_echo_timing.xdc.
# --- Clocks -------------------------------------------------------------------------
create_clock -name sys_clk_pin -period 10.000 -waveform {0 5} [get_ports CLK100MHZ]
# PHY receive and transmit clocks, 25 MHz at 100 Mb/s (802.3 clause 22). The core clock
# and the 25 MHz reference come from the MMCM and are derived automatically.
create_clock -name eth_rx_clk -period 40.000 [get_ports eth_rx_clk]
create_clock -name eth_tx_clk -period 40.000 [get_ports eth_tx_clk]
# The 25 MHz reference leaves through an ODDR; describe the forwarded clock at the pin.
create_generated_clock -name eth_ref_clk_out -divide_by 1 \
    -source [get_pins u_clk/u_oddr_ref/C] [get_ports eth_ref_clk]
# --- MII receive inputs ----------------------------------------------------------
# TI DP83848 datasheet SNLS250E (Apr 2015), T2.5.2: RX_CLK to RXD[3:0], RX_DV, RX_ER
# delay 10 ns min, 30 ns max at 100 Mb/s. Sampled on the next rising edge of rx_clk.
set mii_in [get_ports {eth_rxd[*] eth_rx_dv eth_rxerr}]
set_input_delay -clock eth_rx_clk -max 30.000 $mii_in
set_input_delay -clock eth_rx_clk -min 10.000 $mii_in
set_property IOB TRUE [get_cells -hier -filter {NAME =~ *u_mii_rx/rxd_q_reg*}]
set_property IOB TRUE [get_cells -hier -filter {NAME =~ *u_mii_rx/dv_q_reg}]
set_property IOB TRUE [get_cells -hier -filter {NAME =~ *u_mii_rx/er_q_reg}]
# --- MII transmit outputs -------------------------------------------------------------
# DP83848 SNLS250E, 100 Mb/s MII transmit: T2.4.2 TXD/TX_EN setup to TX_CLK 10 ns min;
# T2.4.3 hold 0 ns min. mii_tx launches from IOB flops on TX_CLK's rising edge.
set mii_out [get_ports {eth_txd[*] eth_tx_en}]
set_output_delay -clock eth_tx_clk -max 10.000 $mii_out
set_output_delay -clock eth_tx_clk -min 0.000 $mii_out
set_property IOB TRUE [get_cells -hier -filter {NAME =~ *u_mii_tx/mii_txd_o_reg*}]
set_property IOB TRUE [get_cells -hier -filter {NAME =~ *u_mii_tx/mii_tx_en_o_reg}]
# --- Clock-domain crossings (docs/design.md CDC list) --------------------------------
# 1. async FIFO Gray pointers (eth_rx, eth_tx): set_max_delay -datapath_only, async_fifo.xdc.
# 2. Reset requests into cdc_reset_sync: level signals into an ASYNC_REG chain.
set_false_path -to [get_cells -hier -filter {NAME =~ *u_rst_*/sync_q_reg[0]}]
# 3. eth_tx underrun toggle (tx_clk -> core): changes at most once per frame; bound skew.
set_max_delay -datapath_only -from [get_cells -hier -filter {NAME =~ *u_eth_tx/underrun_tgl_q_reg}] \
    -to [get_cells -hier -filter {NAME =~ *u_eth_tx/underrun_sync_q_reg[0]}] 8.000
# --- Static or slow board I/O --------------------------------------------------------
set_false_path -from [get_ports ck_rst]
set_false_path -to [get_ports {led[*] uart_rxd_out eth_rstn eth_mdc}]
# --- Configuration -------------------------------------------------------------------
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 4 [current_design]
