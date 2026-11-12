# Full-chip batch build of rtl/top/arty_top for the Arty A7-100T:
#   vivado -mode batch -source syn/build_top.tcl [-tclargs <core_div> [ct]]
# core_div: MMCM divider of the 1000 MHz VCO for the core clock (default 8 = 125 MHz).
# ct: eth_rx in cut-through (CUT_THROUGH=1, ADR 0013); writes build/arty_top_ct/ and
# docs/reports/arty_top_ct/ instead.
# Non-project flow; writes build/arty_top/ and the reports in docs/reports/arty_top/.
# The bitstream is written only if every timing path meets and no clock-domain crossing
# is unconstrained or unsafe. Flow and gates follow fpga-eth-core's syn/build_example.tcl.

set expected_vivado 2025.2
if {[version -short] ne $expected_vivado} {
    puts "WARNING: Vivado [version -short], pinned version is $expected_vivado (docs/toolchain.md)"
}

set core_div [lindex $argv 0]
if {$core_div eq ""} { set core_div 8 }
set part    xc7a100tcsg324-1
set top     arty_top
set repo    [file normalize [file join [file dirname [info script]] ..]]
set eth     [file join $repo rtl eth]
set ct      [expr {[lindex $argv 1] eq "ct"}]
set tag     [expr {$ct ? "${top}_ct" : $top}]
set rundir  [file join $repo build $tag]
set reports [file join $repo docs reports $tag]
file mkdir $rundir $reports
cd $rundir

set sv {}
foreach d {lib lib/xilinx feed book meas out top} {
    set sv [concat $sv [lsort [glob [file join $repo rtl $d *.sv]]]]
}
set sv [concat $sv [lsort [glob [file join $eth rtl *.sv]]]]
read_verilog -sv $sv
read_xdc [file join $eth constr arty_a7_100_pins.xdc]
read_xdc [file join $eth constr arty_a7_100_pins_tx.xdc]
read_xdc [file join $repo constr arty_timing.xdc]
read_xdc [file join $repo constr arty_pmod.xdc]
read_xdc -ref async_fifo [file join $eth constr async_fifo.xdc]

set clk_hz [expr {int(round(1.0e9 / $core_div))}]
synth_design -top $top -part $part -generic CORE_DIV=$core_div -generic CLK_HZ=$clk_hz \
    -generic CUT_THROUGH=$ct
set bb [get_cells -hier -quiet -filter {IS_BLACKBOX == 1}]
if {[llength $bb] > 0} { error "black boxes in the netlist: $bb" }

opt_design
place_design
phys_opt_design
route_design
phys_opt_design

report_timing_summary -max_paths 20 -report_unconstrained -file [file join $reports timing_summary.rpt]
report_utilization                    -file [file join $reports utilization.rpt]
report_utilization -hierarchical      -file [file join $reports utilization_hier.rpt]
report_clock_interaction -delay_type min_max -file [file join $reports clock_interaction.rpt]
report_cdc -details                   -file [file join $reports cdc.rpt]
report_methodology                    -file [file join $reports methodology.rpt]
report_drc                            -file [file join $reports drc.rpt]

set wns [get_property SLACK [get_timing_paths -delay_type max -max_paths 1 -nworst 1]]
set whs [get_property SLACK [get_timing_paths -delay_type min -max_paths 1 -nworst 1]]
set core_period [get_property PERIOD [get_clocks -of_objects [get_pins u_clk/u_bufg_core/O]]]
set fh [open [file join $reports summary.txt] w]
puts $fh "vivado [version -short]  part $part  top $top"
puts $fh "core clock period ${core_period} ns (MMCM divider $core_div)"
puts $fh "WNS ${wns} ns  WHS ${whs} ns"
close $fh
puts "RESULT: core $core_period ns, WNS $wns ns, WHS $whs ns"

set cdc_bad [llength [get_cdc_violations -quiet -filter {SEVERITY == Critical}]]
if {$wns < 0 || $whs < 0} { error "timing not met (WNS $wns, WHS $whs): no bitstream" }
if {$cdc_bad > 0} { error "$cdc_bad critical CDC violation(s), see cdc.rpt: no bitstream" }

write_bitstream -force [file join $rundir $top.bit]
puts "BITSTREAM: [file join $rundir $top.bit]"
