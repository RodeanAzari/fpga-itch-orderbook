# Out-of-context Vivado build of rtl/feed/feed_frontend for timing and utilization:
#   vivado -mode batch -source syn/ooc_feed.tcl [-tclargs <period_ns> [<DATA_W>]]
# Non-project flow; writes only build/ooc_feed[_w64]/ and docs/reports/ooc_feed[_w64]/
# (DATA_W 8, the default, keeps the unsuffixed names).
# I/O is unconstrained (OOC): only the internal register-to-register paths are judged.

set expected_vivado 2025.2
if {[version -short] ne $expected_vivado} {
    puts "WARNING: Vivado [version -short], pinned version is $expected_vivado (docs/toolchain.md)"
}

set period [lindex $argv 0]
if {$period eq ""} { set period 8.000 }
set part    xc7a100tcsg324-1
set top     feed_frontend
set data_w  [lindex $argv 1]
if {$data_w eq ""} { set data_w 8 }
set tag     [expr {$data_w == 8 ? "ooc_feed" : "ooc_feed_w$data_w"}]
set repo    [file normalize [file join [file dirname [info script]] ..]]
set rundir  [file join $repo build $tag]
set reports [file join $repo docs reports $tag]
file mkdir $rundir $reports
cd $rundir

read_verilog -sv [lsort [glob [file join $repo rtl feed *.sv]]]
synth_design -top $top -part $part -mode out_of_context -generic DATA_W=$data_w
create_clock -name clk -period $period [get_ports clk_i]
opt_design
place_design
phys_opt_design
route_design

report_timing_summary -max_paths 10 -file [file join $reports timing_summary.rpt]
report_utilization                  -file [file join $reports utilization.rpt]
report_utilization -hierarchical    -file [file join $reports utilization_hier.rpt]

set wns [get_property SLACK [get_timing_paths -delay_type max -max_paths 1 -nworst 1]]
set whs [get_property SLACK [get_timing_paths -delay_type min -max_paths 1 -nworst 1]]
set fh [open [file join $reports summary.txt] w]
puts $fh "vivado [version -short]  part $part  top $top  DATA_W $data_w  (out of context)"
puts $fh "clock period ${period} ns"
puts $fh "WNS ${wns} ns  WHS ${whs} ns"
close $fh
puts "RESULT: period $period ns, WNS $wns ns, WHS $whs ns"
