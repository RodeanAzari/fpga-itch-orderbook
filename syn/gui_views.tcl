# Opens the release build's routed checkpoint in the Vivado GUI with every view the README's
# "In Vivado" grid needs (buildplan 11.4 item 9) already open, for screenshots:
#   make gui-views            (vivado -mode gui -source syn/gui_views.tcl [-tclargs <dcp>])
# Default checkpoint: build/arty_top/routed.dcp (written by `make impl`).

set repo [file normalize [file join [file dirname [info script]] ..]]
set dcp  [lindex $argv 0]
if {$dcp eq ""} { set dcp [file join $repo build arty_top routed.dcp] }
if {![file exists $dcp]} { error "no checkpoint at $dcp (run make impl)" }

open_checkpoint $dcp

report_timing_summary -name timing_summary
report_utilization    -name utilization
report_power          -name power
report_timing -delay_type max -max_paths 1 -name worst_setup_path

# Order table (ADR 0001): highlight its cells in the device view and draw its schematic.
set table [get_cells -hierarchical -filter {NAME =~ u_core/u_book/u_table/*}]
if {[llength $table] == 0} { error "no cells under u_core/u_book/u_table" }
puts "order table: [llength $table] cells"
highlight_objects -color yellow $table
show_schematic [get_cells -hierarchical -filter {NAME =~ u_core/u_book/u_table/* && PRIMITIVE_GROUP == BLOCKRAM}]
