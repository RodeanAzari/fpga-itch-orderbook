# Two ILA cores for `make impl ILA=1` (buildplan 6.8), sourced by build_top.tcl after
# synth_design. Debug builds only: no RTL changes. Probes attach to ob_core's own nets by
# name: synthesis (flatten_hierarchy rebuilt) renames submodule ports but keeps these.
#   u_ila_mii  (MII rx_clk, 25 MHz, 4,096 samples = 164 us): the nibbles into eth_rx.
#   Except for these, probes are ob_core's own nets.
#   u_ila_core (core clock, 4,096 samples = 33 us): eth_rx's output (f_*), the feed
#              udp_rx's payload (u_*) and the ITCH decoder's records (r_*).
# Iterate on this file against build/arty_top_ila/synth.dcp (written just before it).
# Every probe must resolve to a net; a missing one (renamed or optimised away) is an error.

proc ila_nets {name width} {
    set nets {}
    for {set i 0} {$i < $width} {incr i} {
        set n [expr {$width == 1 ? $name : "${name}\[$i\]"}]
        set x [get_nets -quiet $n]
        if {[llength $x] != 1} { error "ILA probe: no net $n" }
        lappend nets $x
    }
    return $nets
}

proc ila_core {name clk depth probes} {
    set core [create_debug_core $name ila]
    set_property C_DATA_DEPTH          $depth $core
    set_property C_TRIGIN_EN           false  $core
    set_property C_TRIGOUT_EN          false  $core
    set_property C_ADV_TRIGGER         false  $core
    set_property C_INPUT_PIPE_STAGES   1      $core
    set_property ALL_PROBE_SAME_MU     true   $core
    set_property ALL_PROBE_SAME_MU_CNT 1      $core
    set_property port_width 1 [get_debug_ports $name/clk]
    connect_debug_port $name/clk [ila_nets $clk 1]
    set i 0
    foreach {net width} $probes {
        if {$i > 0} { create_debug_port $name probe }
        set port [get_debug_ports $name/probe$i]  ;# by index: the names sort as text
        set_property port_width $width $port
        connect_debug_port $port [ila_nets $net $width]
        incr i
    }
}

set c u_core
# The MII pins feed IOB flops, which cannot be probed between buffer and flop: probe the
# flops' outputs (mii_rx's input register, the pins one rx_clk later). dv_q's net is
# named dv_q_reg_0 by synthesis.
set m $c/u_eth_rx/u_mii_rx
ila_core u_ila_mii eth_rx_clk_IBUF_BUFG 4096 [list \
    $m/rxd_q 4  $m/dv_q_reg_0 1  $m/er_q 1]
ila_core u_ila_core $c/clk_core_o 4096 [list \
    $c/f_tdata 8  $c/f_tvalid 1  $c/f_tready 1  $c/f_tlast 1 \
    $c/u_tdata 8  $c/u_tvalid 1  $c/u_tready 1  $c/u_tlast 1 \
    $c/r_valid 1  $c/r_ready 1   $c/r_type 8    $c/e_ok 1]

set_property C_CLK_INPUT_FREQ_HZ 125000000 [get_debug_cores dbg_hub]
set_property C_ENABLE_CLK_DIVIDER false    [get_debug_cores dbg_hub]
set_property C_USER_SCAN_CHAIN 1           [get_debug_cores dbg_hub]
connect_debug_port dbg_hub/clk [ila_nets $c/clk_core_o 1]
