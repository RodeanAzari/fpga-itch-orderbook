# Programs a bitstream into the Arty A7-100T over JTAG (volatile; lost at power-off):
#   vivado -mode batch -source syn/prog.tcl [-tclargs <bitfile>]
# Default bitfile: build/arty_top/arty_top.bit. Under WSL the board's FTDI (0403:6010)
# must be attached with usbipd-win. Fails unless the device reports DONE high.

set repo [file normalize [file join [file dirname [info script]] ..]]
set bit  [lindex $argv 0]
if {$bit eq ""} { set bit [file join $repo build arty_top arty_top.bit] }
if {![file exists $bit]} { error "no bitstream at $bit" }

open_hw_manager
connect_hw_server -allow_non_jtag
open_hw_target
set dev [lindex [get_hw_devices xc7a100t*] 0]
if {$dev eq ""} { error "no xc7a100t on the JTAG chain" }
current_hw_device $dev
set_property PROGRAM.FILE $bit $dev
program_hw_devices $dev
refresh_hw_device $dev
set done [get_property REGISTER.CONFIG_STATUS.BIT14_DONE_PIN $dev]
puts "programmed $bit, DONE=$done"
if {$done ne 1} { error "DONE is low after programming" }
close_hw_manager
