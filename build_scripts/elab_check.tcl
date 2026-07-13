open_project C:/Users/dllau/Developer/AuV2-SLI/build/Au2_SLI/Au2_SLI.xpr
update_compile_order -fileset sources_1
# RTL elaboration only -- fast, and prints any VHDL/Verilog error to the console.
synth_design -top Au2_SLI -part xc7a35tftg256-2 -rtl -name elab_check
puts "=== ELAB_OK ==="
exit 0
