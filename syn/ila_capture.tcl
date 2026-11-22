# Programs the ILA build and captures one feed frame on both ILA cores (syn/ila_probes.tcl):
#   vivado -mode batch -source syn/ila_capture.tcl -tclargs <bit> <ltx> <outdir> <go_file>
# Run by scripts/ila_run.sh: after "PROGRAMMED" it waits for <go_file> (the host has
# configured the board), then arms both cores and waits for a frame.
#   u_ila_mii  triggers on rx_dv rising (the next frame's preamble), 32 samples before it.
#   u_ila_core triggers on the feed udp_rx's first payload beat, 256 samples before it
#              (the frame's headers leave eth_rx about 44 cycles earlier).
# Writes <outdir>/{mii,core}.csv for analysis/ila_frame.py and {mii,core}.ila, which the
# GUI shows with read_hw_ila_data + display_hw_ila_data for the waveform screenshot.

lassign $argv bit ltx outdir go
foreach f [list $bit $ltx] { if {![file exists $f]} { error "missing $f" } }
file mkdir $outdir

open_hw_manager
connect_hw_server -allow_non_jtag
open_hw_target
set dev [lindex [get_hw_devices xc7a100t*] 0]
if {$dev eq ""} { error "no xc7a100t on the JTAG chain" }
current_hw_device $dev
set_property PROGRAM.FILE $bit $dev
set_property PROBES.FILE $ltx $dev
set_property FULL_PROBES.FILE $ltx $dev
program_hw_devices $dev
refresh_hw_device $dev
puts "PROGRAMMED"

proc one {pattern} {
    set x [get_hw_ilas -quiet -filter "CELL_NAME =~ *$pattern*"]
    if {[llength $x] != 1} { error "ILA $pattern: found [llength $x]" }
    return $x
}
proc probe {ila pattern} {
    set p [get_hw_probes -quiet -of_objects $ila -filter "NAME =~ *$pattern*"]
    if {[llength $p] != 1} { error "probe $pattern: found {$p}" }
    return $p
}
set mii  [one u_ila_mii]
set core [one u_ila_core]
foreach {ila pos} [list $mii 32 $core 256] {
    set_property CONTROL.DATA_DEPTH 4096 $ila
    set_property CONTROL.TRIGGER_POSITION $pos $ila
    set_property CONTROL.TRIGGER_CONDITION AND $ila
}
set_property TRIGGER_COMPARE_VALUE eq1'bR [probe $mii  u_mii_rx/dv_q_reg_0]
set_property TRIGGER_COMPARE_VALUE eq1'bR [probe $core u_core/u_tvalid]

while {![file exists $go]} { after 200 }
run_hw_ila $mii
run_hw_ila $core
puts "ARMED"
wait_on_hw_ila -timeout 2 $mii
wait_on_hw_ila -timeout 2 $core
foreach {name ila} [list mii $mii core $core] {
    set d [upload_hw_ila_data $ila]
    write_hw_ila_data -force -csv_file [file join $outdir $name.csv] $d
    write_hw_ila_data -force [file join $outdir $name.ila] $d
}
puts "CAPTURED $outdir"
close_hw_manager
