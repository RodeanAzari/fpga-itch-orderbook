# Pmod JA: T1 / T2 scope pulses (rtl/top/arty_top.sv, buildplan 6.5). The two pin lines are
# copied verbatim (uncommented) from Digilent's Arty-A7-100-Master.xdc,
# github.com/Digilent/digilent-xdc commit bb01f62e09a8d8d5e85971aa12cf1d15e0f48207, MIT
# licence (the same commit as fpga-eth-core's pin file).
set_property -dict { PACKAGE_PIN G13   IOSTANDARD LVCMOS33 } [get_ports { ja[0] }]; #IO_0_15 Sch=ja[1]
set_property -dict { PACKAGE_PIN B11   IOSTANDARD LVCMOS33 } [get_ports { ja[1] }]; #IO_L4P_T0_15 Sch=ja[2]

# Both outputs from IOB flip-flops: equal, fixed clock-to-pad delay, so the pin-to-pin
# skew is only the board traces and the IOB difference.
set_property IOB TRUE [get_ports { ja[*] }]
# Observed by a scope, not clocked by any receiver: no output timing to meet.
set_false_path -to [get_ports { ja[*] }]
